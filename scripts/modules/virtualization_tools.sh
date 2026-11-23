#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_virtualization_tools() {
    local loader=wine64
    [[ "$ARCH" != armhf ]] || loader=wine32
    apt_install wine "$loader"
}
