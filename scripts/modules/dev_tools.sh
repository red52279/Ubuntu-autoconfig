#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_dev_tools() {
    apt_install_available cmake ninja-build gcc g++ make libssl-dev \
        libeigen3-dev libopencv-dev libzbar-dev libaravis-dev aravis-tools
}
