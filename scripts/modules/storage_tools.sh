#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_storage_tools() {
    apt_install ntfs-3g exfatprogs udisks2
    if (( ! DRY_RUN )); then
        as_root /usr/bin/python3 "${NETWORK_HELPER%/*}/storage.py"
    fi
}
