#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_storage_tools() {
    local packages
    packages=$(/usr/bin/python3 "${NETWORK_HELPER%/*}/catalog.py" packages \
        --feature storage_tools --suite "$UBUNTU_CODENAME") || return $?
    local names=()
    mapfile -t names <<< "$packages"
    if (( ! DRY_RUN )) && has_pkg exfatprogs; then names=(ntfs-3g exfatprogs udisks2); fi
    apt_install "${names[@]}"
    if (( ! DRY_RUN )); then
        as_root /usr/bin/python3 "${NETWORK_HELPER%/*}/storage.py"
    fi
}
