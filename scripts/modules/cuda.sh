#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/nvidia.sh"

install_cuda() {
    if [[ "$ARCH" != amd64 && "$ARCH" != arm64 ]] || nvidia_is_jetson; then
        warn "CUDA 工具链支持 amd64 和 arm64 SBSA，Jetson 请使用 JetPack。"
        return "$SKIP_STATUS"
    fi
    if [[ "$ARCH:$UBUNTU_CODENAME" == arm64:focal ]]; then
        if (( DRY_RUN )); then
            say "将从 NVIDIA 中国签名仓库安装 Ubuntu 20.04 arm64 SBSA 的 CUDA 工具链"
            return 0
        fi
        configure_nvidia_repository || return $?
        APT_UPDATED=1 APT_INSTALL_NO_REMOVE=1 apt_install cuda-toolkit
        return $?
    fi
    # Ubuntu archive packages are served through the configured domestic mirror.
    APT_INSTALL_NO_REMOVE=1 apt_install_available nvidia-cuda-toolkit nvidia-cuda-dev nvidia-cuda-gdb
}
