#!/usr/bin/env bash
# Minimal command line/runtime dependencies used by the other modules.
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_base_tools() {
    apt_install_available \
        ca-certificates apt-transport-https software-properties-common \
        curl wget gnupg git jq unzip zip vim tree rsync \
        python3 python3-pip python3-venv python3-setuptools \
        build-essential pkg-config
}
