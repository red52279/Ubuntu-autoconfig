#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

nvidia_is_jetson() {
    [[ "$ARCH" == arm64 ]] && /usr/bin/python3 "${NETWORK_HELPER%/*}/tensorrt.py" jetson
}

configure_nvidia_repository() (
    local settings repo source_line source key fingerprint preferences host work
    settings=$(/usr/bin/python3 "${NETWORK_HELPER%/*}/tensorrt.py" repository "$UBUNTU_CODENAME" "$ARCH") || return $?
    {
        read -r repo
        read -r source_line
        read -r source
        read -r key
        read -r fingerprint
        read -r preferences
        read -r host
    } <<< "$settings"

    apt_install gnupg ca-certificates || return $?
    work=$(mktemp -d) || return $?
    trap 'rm -rf -- "$work"' EXIT
    if [[ ! -f "$key" ]] || [[ $(signing_key_fingerprints "$work" "$key") != "$fingerprint" ]]; then
        DOWNLOAD_TIMEOUT=90 download_from_mirrors "$work/keyring.deb" "$repo/cuda-keyring_1.1-1_all.deb" || return $?
        dpkg-deb --fsys-tarfile "$work/keyring.deb" |
            tar -xOf - ./usr/share/keyrings/cuda-archive-keyring.gpg > "$work/key.gpg" || return $?
        [[ $(signing_key_fingerprints "$work" "$work/key.gpg") == "$fingerprint" ]] ||
            die "NVIDIA CUDA 仓库签名公钥指纹不匹配。"
        as_root install -m 0644 "$work/key.gpg" "$key" || return $?
    fi
    # Ubuntu's normal priority (500) takes precedence for overlapping packages.
    printf 'Package: *\nPin: origin %s\nPin-Priority: 100\n' "$host" > "$work/preferences"
    as_root install -m 0644 "$work/preferences" "$preferences" || return $?
    apt_configure_source "$source" <<< "$source_line"
)
