#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

v2rayn_existing_binary() {
    [[ -s /opt/v2rayN/v2rayN && -x /opt/v2rayN/v2rayN ]] && binary_matches_arch /opt/v2rayN/v2rayN
}

v2rayn_desktop_entry() {
    as_root install -d -m 0755 /usr/share/applications
    as_root tee /usr/share/applications/v2rayn.desktop >/dev/null <<'DESKTOP'
[Desktop Entry]
Name=v2rayN
Comment=v2rayN
Exec=/opt/v2rayN/v2rayN
Terminal=false
Type=Application
Categories=Network;
DESKTOP
}

install_v2rayn() (
    set -Eeuo pipefail
    local machine tmp_dir archive install_dir binary
    machine=$(architecture_name)
    case "$machine" in
        x86_64|aarch64) ;;
        *) warn "v2rayN 没有适用于 $machine 的已验证 Linux 构建，跳过。"; return "$SKIP_STATUS" ;;
    esac
    if (( DRY_RUN )); then
        say "复用已有 v2rayN；也可使用本地 deb 或国内签名镜像"
        return 0
    fi
    if v2rayn_existing_binary; then
        v2rayn_desktop_entry
        say "已复用本机 v2rayN 并补全桌面入口，没有更新程序版本。"
        return 0
    fi
    local urls=()
    if [[ -n "${V2RAYN_DOMESTIC_URLS:-}" ]]; then
        mapfile -t urls <<< "$V2RAYN_DOMESTIC_URLS"
    else
        install_vendor_deb v2rayn
        return
    fi
    apt_install ca-certificates unzip
    tmp_dir=$(mktemp -d)
    trap 'rm -rf -- "$tmp_dir"' EXIT
    archive="$tmp_dir/v2rayn.zip"
    install_dir=/opt/v2rayN
    download_from_mirrors "$archive" "${urls[@]}"
    unzip -tq "$archive" >/dev/null
    unzip -oq "$archive" -d "$tmp_dir/unpacked"
    binary=$(find "$tmp_dir/unpacked" -maxdepth 3 -type f -name v2rayN -print -quit)
    [[ -n "$binary" ]] || die "v2rayN 发布包中没有找到可执行文件。"
    binary_matches_arch "$binary" || die "v2rayN 压缩包中的程序架构不匹配，未安装。"
    as_root install -d -m 0755 "$install_dir"
    as_root cp -a "$(dirname "$binary")/." "$install_dir/"
    as_root chmod a+rx "$install_dir/v2rayN"
    if [[ -f "$install_dir/AmazTool" ]]; then as_root chmod a+rx "$install_dir/AmazTool"; fi
    v2rayn_desktop_entry
    say "v2rayN 安装完成。"
)
