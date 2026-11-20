#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_media_tools() {
    apt_install_available zip unzip rar unrar tree
}
