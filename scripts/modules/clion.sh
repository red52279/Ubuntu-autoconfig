#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_clion() {
    if (( DRY_RUN )); then
        say "将安装 JetBrains CLion Linux 稳定版，优先复用本地包，否则从官方中国 CDN 下载"
        return 0
    fi
    if (( ${UPDATE_ARCHIVE:-0} )) || [[ ! -x /opt/clion/bin/clion.sh ]]; then
        local options=()
        if [[ -n ${CLION_VERSION:-} ]]; then options+=(--version "$CLION_VERSION"); fi
        as_root /usr/bin/python3 "${NETWORK_HELPER%/*}/archive_apps.py" "$ARCH" "${options[@]}"
    fi
    as_root install -d -m 0755 /usr/share/applications
    as_root tee /usr/share/applications/jetbrains-clion.desktop >/dev/null <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=CLion
Exec=/opt/clion/bin/clion.sh %f
Icon=/opt/clion/bin/clion.svg
Terminal=false
Categories=Development;IDE;
StartupWMClass=jetbrains-clion
DESKTOP
}
