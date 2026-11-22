#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_gpu_driver() {
    if [[ "$ARCH" != amd64 ]]; then
        warn "通用显卡驱动模块仅支持 amd64；ARM 请使用板卡/厂商驱动。"
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
        recommended=$(ubuntu-drivers devices | awk '/driver.*recommended/ {print $3; exit}')
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
