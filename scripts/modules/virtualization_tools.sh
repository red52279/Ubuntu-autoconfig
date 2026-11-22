#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_virtualization_tools() {
    if [[ "$ARCH" != amd64 ]]; then
        warn "当前 Wine 模块仅支持 amd64，ARM 已禁用。"
        return "$SKIP_STATUS"
    fi
    apt_install_available wine
}
