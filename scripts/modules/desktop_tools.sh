#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_desktop_tools() {
    apt_install_available qt6-base-dev qt6-tools-dev-tools qtcreator \
        libqt6core6 libqt6widgets6 libxcb-cursor0
}
