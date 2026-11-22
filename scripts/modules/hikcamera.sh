#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_hikcamera() (
    set -Eeuo pipefail
    if (( DRY_RUN )); then
        say "直连海康机器人官网下载 MVS Linux 客户端、SDK 和配套工具"
        return 0
    fi
    local destination=/opt/MVS desktop=/usr/share/applications/MVS.desktop
    local runtime=/var/lib/ubuntu-autoconfig/mvs account=${SUDO_USER:-$(id -un)}
    local tmp_dir= staging= previous= url machine archive_entries bundle payload
    apt_install ca-certificates unzip net-tools x11-utils libgl1 libx11-xcb1 libxkbcommon-x11-0 \
        libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xinerama0 libusb-1.0-0
    if (( ${UPDATE_ARCHIVE:-0} )) || [[ ! -x "$destination/bin/MVS" ]]; then
        [[ ! -L "$destination" && ( ! -e "$destination" || -x "$destination/bin/MVS" ) ]] ||
            die "$destination 已存在但不符合 MVS 安装布局，请检查。"
        machine=$(architecture_name)
        url=${MVS_RELEASE_URL:-}
        if [[ -z "$url" ]]; then url=$(/usr/bin/python3 "${NETWORK_HELPER%/*}/vendor_release.py" hikcamera "$ARCH"); fi
        tmp_dir=$(mktemp -d)
        trap 'rm -rf -- "$tmp_dir"
            if [[ -n "$previous" && -d "$previous/app" && ! -e "$destination" ]]; then
                as_root mv -T -- "$previous/app" "$destination"
            fi
            if [[ -n "$previous" ]]; then as_root rm -rf -- "$previous"; fi
            if [[ -n "$staging" ]]; then as_root rm -rf -- "$staging"; fi' EXIT
        download_from_mirrors "$tmp_dir/mvs.zip" "$url"
        archive_entries=$(unzip -Z1 "$tmp_dir/mvs.zip")
        bundle=$(grep -E "^MVS-[0-9.]+_${machine}_[0-9]{8}\\.tar\\.gz$" <<< "$archive_entries" || true)
        [[ -n "$bundle" && "$bundle" != *$'\n'* ]] || die "MVS 发布包没有唯一匹配的 Linux 安装归档。"
        unzip -p "$tmp_dir/mvs.zip" "$bundle" > "$tmp_dir/bundle.tar.gz"
        archive_entries=$(tar -tzf "$tmp_dir/bundle.tar.gz")
        payload=$(grep -E '(^|/)MVS\.tar\.gz$' <<< "$archive_entries" || true)
        [[ -n "$payload" && "$payload" != *$'\n'* ]] || die "MVS 安装归档缺少客户端文件。"
        tar -xOzf "$tmp_dir/bundle.tar.gz" -- "$payload" > "$tmp_dir/MVS.tar.gz"

        # The vendor setup script executes login profiles as root and installs
        # world-writable boot services. Deploy the client and SDK payload directly.
        umask 022
        staging=$(as_root mktemp -d /opt/.ubuntu-autoconfig-mvs.XXXXXX)
        as_root tar -xzf "$tmp_dir/MVS.tar.gz" --no-same-owner --no-same-permissions -C "$staging"
        binary_matches_arch "$staging/bin/MVS" || die "MVS 客户端架构与本机不匹配。"
        as_root install -d -m 0755 "$runtime"
        as_root install -d -m 0755 -o "$account" "$runtime/Temp" "$runtime/Temp/Log"
        as_root ln -s "$runtime/Temp" "$staging/bin/Temp"
        as_root chmod 0755 "$staging"
        if [[ -d "$destination" ]]; then
            previous=$(as_root mktemp -d /opt/.ubuntu-autoconfig-mvs-previous.XXXXXX)
            as_root mv -T -- "$destination" "$previous/app"
        fi
        as_root mv -T -- "$staging" "$destination"
        staging=
    fi
    binary_matches_arch "$destination/bin/MVS" || die "$destination/bin/MVS 的架构与本机不匹配。"

    as_root install -d -m 0755 /usr/share/applications /etc/udev/rules.d
    as_root tee /etc/udev/rules.d/70-ubuntu-autoconfig-mvs.rules >/dev/null <<'RULE'
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="2bdf", TAG+="uaccess"
RULE
    if [[ -d /run/udev ]]; then as_root udevadm control --reload-rules; fi
    if [[ ! -f "$desktop" ]]; then
        as_root tee "$desktop" >/dev/null <<'DESKTOP'
[Desktop Entry]
Name=MVS
Type=Application
Exec=/opt/MVS/bin/MVS.sh
Icon=/opt/MVS/bin/DesktopLink/MVS.ico
Terminal=false
Categories=Utility;
DESKTOP
    fi
)
