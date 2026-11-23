#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_desktop_tools() {
    if (( DRY_RUN )); then
        say "将安装当前 Ubuntu 源提供的 Qt6 开发工具、Qt Creator 和以 -dev 结尾的扩展包"
        return 0
    fi
    apt_update || return $?
    local listing packages=()
    listing=$(/usr/bin/python3 "${NETWORK_HELPER%/*}/qt_packages.py" "$ARCH") || return $?
    mapfile -t packages <<< "$listing"
    APT_INSTALL_NO_REMOVE=1 apt_install "${packages[@]}"
}
