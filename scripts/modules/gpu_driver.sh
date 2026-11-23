#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/nvidia.sh"

install_gpu_driver() {
    if nvidia_is_jetson; then
        warn "Jetson 显卡驱动由板卡对应的 NVIDIA JetPack 提供。"
        return "$SKIP_STATUS"
    fi
    apt_install_available ubuntu-drivers-common
    if (( DRY_RUN )); then
        say "将使用 Ubuntu 官方仓库的 ubuntu-drivers 自动安装最新稳定推荐驱动"
        return 0
    fi
    if command -v ubuntu-drivers >/dev/null 2>&1; then
        say "根据当前 GPU 自动选择 Ubuntu 仓库中的最新稳定推荐驱动"
        local recommended
        recommended=$(LC_ALL=C ubuntu-drivers devices |
            awk '$1 == "driver" && $3 ~ /^nvidia-driver-/ && /recommended/ {print $3; exit}')
        if [[ -z "$recommended" ]]; then
            warn "未检测到需要额外安装的推荐显卡驱动。"
            return "$SKIP_STATUS"
        fi
        # Route installation through our audited, proxy-free APT wrapper.
        apt_install "$recommended"
    else
        warn "ubuntu-drivers 不可用，跳过显卡驱动安装。"
        return "$SKIP_STATUS"
    fi
}
