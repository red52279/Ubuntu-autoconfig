#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_cuda() {
    if [[ "$ARCH" != amd64 ]]; then
        warn "ARM CUDA 需要板卡/厂商 SDK；不使用通用 x86 安装流程。"
        return "$SKIP_STATUS"
    fi
    # Ubuntu archive packages are served through the configured domestic mirror.
    apt_install_available nvidia-cuda-toolkit nvidia-cuda-dev nvidia-cuda-gdb
}
