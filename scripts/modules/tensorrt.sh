#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/nvidia.sh"

install_tensorrt() (
    if [[ "$ARCH" != amd64 && "$ARCH" != arm64 ]] || nvidia_is_jetson; then
        warn "TensorRT 签名 APT 包适用于 amd64 和 arm64 SBSA，Jetson 请使用 JetPack。"
        return "$SKIP_STATUS"
    fi
    if (( DRY_RUN )); then
        say "将通过 NVIDIA 中国签名仓库安装 TensorRT C++ 开发库、Python 绑定、ONNX 解析器、trtexec 和配套 CUDA 运行库"
        return 0
    fi

    local version listing
    local packages=()
    configure_nvidia_repository || return $?
    probe_feature tensorrt || return $?
    if [[ "$PROBE_STATUS" == ready ]]; then return 0; fi

    version=$(LC_ALL=C apt-cache policy libnvinfer-bin | awk '$1 == "Candidate:" { print $2; exit }') || return $?
    listing=$(/usr/bin/python3 "${NETWORK_HELPER%/*}/tensorrt.py" packages "$version") || return $?
    mapfile -t packages <<< "$listing"
    say "安装 TensorRT $version 及对应 CUDA 运行库"
    apt_get install -y --no-remove --no-install-recommends "${packages[@]}"
)
