#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

ensure_fcitx4_im_config() {
    local directory=${1:-/usr/share/im-config/data}
    if compgen -G "$directory/??_fcitx.conf" >/dev/null && compgen -G "$directory/??_fcitx.rc" >/dev/null; then
        return 0
    fi
    # im-config 0.62 (Ubuntu 26.04) removed the Fcitx4 profile while Ubuntu
    # still ships Fcitx4 and Sogou requires it. Add a local compatibility entry.
    as_root install -d -m 0755 "$directory"
    as_root tee "$directory/22_fcitx.conf" >/dev/null <<'CONF'
# Managed by ubuntu-autoconfig for Sogou/Fcitx4 compatibility.
IM_CONFIG_SHORT="Fcitx4 for Sogou Pinyin"
IM_CONFIG_LONG="Activate the installed Fcitx4 input method framework for Sogou."
package_auto() { package_status fcitx; }
package_menu() { package_status fcitx; }
CONF
    as_root tee "$directory/22_fcitx.rc" >/dev/null <<'RC'
# Managed by ubuntu-autoconfig for Sogou/Fcitx4 compatibility.
if [ "${IM_CONFIG_PHASE:-}" = 1 ]; then
    XMODIFIERS=@im=fcitx
    GTK_IM_MODULE=fcitx
    QT_IM_MODULE=fcitx
    CLUTTER_IM_MODULE=xim
    SDL_IM_MODULE=fcitx
fi
if [ "${IM_CONFIG_PHASE:-}" = 2 ]; then
    /usr/bin/fcitx -d 2>/dev/null
fi
RC
}

configure_chinese_desktop() {
    local account=$1 user_home=$2 uid console_output
    uid=$(id -u "$account")
    as_user "$account" /usr/bin/python3 "${NETWORK_HELPER%/*}/desktop_locale.py" "$user_home"
    as_root /usr/bin/python3 "${NETWORK_HELPER%/*}/input_config.py" system-keyboard
    if command -v setupcon >/dev/null 2>&1; then
        console_output=$(as_root setupcon --keyboard-only --save-only 2>&1) ||
            die "无法更新虚拟终端键盘配置: $console_output"
    fi
    if busctl --system get-property org.freedesktop.Accounts "/org/freedesktop/Accounts/User$uid" \
        org.freedesktop.Accounts.User Language >/dev/null 2>&1; then
        as_root busctl --system call org.freedesktop.Accounts "/org/freedesktop/Accounts/User$uid" \
            org.freedesktop.Accounts.User SetLanguage s 'zh_CN:zh:en_US:en'
        as_root busctl --system call org.freedesktop.Accounts "/org/freedesktop/Accounts/User$uid" \
            org.freedesktop.Accounts.User SetFormatsLocale s zh_CN.UTF-8
    fi
    if command -v gsettings >/dev/null 2>&1 && [[ -S "/run/user/$uid/bus" ]]; then
        as_user "$account" env XDG_RUNTIME_DIR="/run/user/$uid" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
            gsettings set org.gnome.system.locale region 'zh_CN.UTF-8'
    fi
    say "已设置 $account 的中文界面/地区偏好，Desktop、Downloads、Documents 等目录路径保留英文。"
}

configure_user_input() {
    local account=$1 user_home=$2 framework=$3 uid
    shift 3
    uid=$(id -u "$account")
    if [[ -S "/run/user/$uid/bus" ]]; then
        local session=(env "XDG_RUNTIME_DIR=/run/user/$uid" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus")
        as_user "$account" "${session[@]}" /usr/bin/python3 "${NETWORK_HELPER%/*}/input_config.py" "$framework" "$user_home" "$@"
        as_user "$account" "${session[@]}" systemctl --user daemon-reload ||
            warn "当前用户服务未重新加载，新的英语输入默认设置将在用户服务重启后生效。"
    else
        as_user "$account" dbus-run-session -- /usr/bin/python3 "${NETWORK_HELPER%/*}/input_config.py" \
            "$framework" "$user_home" --offline "$@"
    fi
}

sogou_download_urls() {
    python3 - "$1" "$ARCH" <<'PY'
import json, pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
match = re.search(r'window\.IFE\s*=\s*', text)
if not match:
    raise SystemExit('搜狗官网下载配置已变化。')
data, _ = json.JSONDecoder().raw_decode(text[match.end():])
config = json.loads(data['data']['downloadConfig'])
name = {'amd64': 'x86_64', 'arm64': 'arm64'}[sys.argv[2]]
url = next(item['link'] for item in config['linux'] if item['name'] == name)
print(url)
# Both are official Tencent download CDN domains. The non-sec hostname also
# serves HTTPS and may work when ime-sec responds 403 in a particular region.
if url.startswith('https://ime-sec.gtimg.com/'):
    print(url.replace('https://ime-sec.gtimg.com/', 'https://ime.gtimg.com/', 1))
PY
}

install_sogou_input() (
    set -Eeuo pipefail
    if [[ "$ARCH" != amd64 && "$ARCH" != arm64 ]]; then
        warn "搜狗官方安装包不支持 $ARCH。"
        return "$SKIP_STATUS"
    fi
    if (( DRY_RUN )); then
        say "安装搜狗与 Fcitx4，为当前用户配置默认英语输入"
        return 0
    fi
    local tmp_dir target_user target_home
    tmp_dir=$(mktemp -d)
    trap 'rm -rf -- "$tmp_dir"' EXIT
    local requirements=(locales language-pack-zh-hans language-pack-gnome-zh-hans language-pack-gnome-zh-hans-base
        language-selector-gnome fonts-noto-cjk fcitx-ui-classic fcitx-config-gtk dbus-x11
        libgsettings-qt1 libxcb-xinerama0 libqt5qml5 libqt5quick5 libqt5quickwidgets5 libqt5svg5 libqt5x11extras5)
    if has_pkg sogoupinyin; then
        say "搜狗包已安装，只检查依赖和配置。"
        apt_install "${requirements[@]}"
    else
    tmp_download https://shurufa.sogou.com/linux "$tmp_dir/sogou.html"
    local urls_text urls=()
    urls_text=$(sogou_download_urls "$tmp_dir/sogou.html")
    mapfile -t urls <<< "$urls_text"
    download_from_mirrors "$tmp_dir/sogoupinyin.deb" "${urls[@]}"
    [[ $(dpkg-deb -f "$tmp_dir/sogoupinyin.deb" Package) == sogoupinyin ]] || die "搜狗下载包名不匹配。"
    [[ $(dpkg-deb -f "$tmp_dir/sogoupinyin.deb" Architecture) == "$ARCH" ]] || die "搜狗下载包架构不匹配。"
    chmod 0755 "$tmp_dir"
    chmod 0644 "$tmp_dir/sogoupinyin.deb"
    apt_install "$tmp_dir/sogoupinyin.deb" "${requirements[@]}"
    fi
    has_pkg sogoupinyin || die "搜狗安装未完成。"
    has_pkg fcitx || die "Fcitx4 安装未完成。"
    local missing_libraries
    missing_libraries=$(ldd /opt/sogoupinyin/files/bin/sogoupinyin-service 2>/dev/null | awk '/=> not found/ {print $1}')
    [[ -z "$missing_libraries" ]] || die "搜狗服务缺少运行库: $missing_libraries"
    set_locale
    ensure_fcitx4_im_config
    target_user=${SUDO_USER:-$(id -un)}
    target_home=$(getent passwd "$target_user" | cut -d: -f6)
    [[ -n "$target_home" ]] || die "无法找到用户 $target_user 的主目录。"
    configure_chinese_desktop "$target_user" "$target_home"
    as_user "$target_user" im-config -n fcitx
    as_root install -d -m 0755 "$target_home/.config/fcitx" /etc/profile.d
    configure_user_input "$target_user" "$target_home" sogou
    # Ubuntu 26.04 disables im-launch's XDG autostart entry. Ensure Fcitx4 is
    # started for this user on the next desktop login, including XWayland.
    local autostart="$target_home/.config/autostart/ubuntu-autoconfig-fcitx.desktop"
    as_root install -d -m 0755 "$target_home/.config/autostart"
    as_root tee "$autostart" >/dev/null <<'AUTOSTART'
[Desktop Entry]
Type=Application
Name=Fcitx for Sogou
Exec=/usr/bin/fcitx -d
NoDisplay=true
X-GNOME-Autostart-enabled=true
AUTOSTART
    as_root chown "$target_user:$(id -gn "$target_user")" "$target_home/.config/autostart" "$autostart"
    # Replace the old managed Fcitx5 environment file as well.
    as_root tee /etc/profile.d/fcitx5.sh >/dev/null <<'PROFILE'
# Managed by ubuntu-autoconfig: Sogou uses Fcitx4.
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
PROFILE
    as_root install -d -m 0755 /etc/ubuntu-autoconfig
    printf 'sogou\n' | as_root tee /etc/ubuntu-autoconfig/input-method >/dev/null
    as_root rm -f /etc/ubuntu-autoconfig/input-fallback
    say "已配置 Fcitx4 默认英语输入，Ctrl+空格切换搜狗；注销后重新登录生效。"
    if [[ "${XDG_SESSION_TYPE:-}" == wayland ]]; then
        warn "搜狗 Fcitx4 对原生 Wayland 应用的支持有限；本次只保证安装与 Fcitx4 配置，实际输入需在会话中验证。"
    fi
)

native_chinese_engine() {
    # Prefer an already installed distribution engine before adding a new one.
    local package
    for package in ibus-libpinyin ibus-pinyin ibus-rime; do
        if has_pkg "$package"; then printf '%s\n' "$package"; return 0; fi
    done
    for package in ibus-libpinyin ibus-pinyin ibus-rime; do
        if apt_has_candidate "$package"; then printf '%s\n' "$package"; return 0; fi
    done
    return 1
}

install_native_input() {
    local package=$1 engine account user_home
    case "$package" in
        ibus-libpinyin) engine=libpinyin ;;
        ibus-pinyin) engine=pinyin ;;
        ibus-rime) engine=rime ;;
        *) die "不支持的系统中文引擎包: $package" ;;
    esac
    apt_install ibus "$package" im-config locales language-pack-zh-hans \
        language-pack-gnome-zh-hans language-selector-gnome fonts-noto-cjk
    set_locale
    account=${SUDO_USER:-$(id -un)}
    user_home=$(getent passwd "$account" | cut -d: -f6)
    configure_chinese_desktop "$account" "$user_home"
    as_user "$account" im-config -n ibus
    # Replace our old Fcitx-wide exports so GTK4, Electron/Wayland and Qt apps
    # use the GNOME input framework rather than searching for Fcitx4 modules.
    as_root tee /etc/profile.d/fcitx5.sh >/dev/null <<'PROFILE'
# Managed by ubuntu-autoconfig: native GNOME/IBus input framework.
export GTK_IM_MODULE=ibus
export QT_IM_MODULE=ibus
export XMODIFIERS=@im=ibus
PROFILE
    as_root install -d -m 0755 /etc/ubuntu-autoconfig
    printf 'ibus\n' | as_root tee /etc/ubuntu-autoconfig/input-method >/dev/null
    configure_user_input "$account" "$user_home" ibus --engine "$engine"
    as_root rm -f /etc/ubuntu-autoconfig/input-fallback
    say "已配置默认英语输入，Super+空格 / Ctrl+空格可切换到 $engine。"
}

install_language_input() {
    local requested=${INPUT_METHOD:-auto} package native_result
    case "$requested" in auto|ibus|sogou) ;; *) die "输入法选项必须是 auto、ibus 或 sogou。" ;; esac
    if [[ "$requested" == sogou ]]; then install_sogou_input; return $?; fi
    if (( DRY_RUN )); then
        say "配置登录、终端与新窗口默认英语输入，保留 IBus 或搜狗中文切换"
        return 0
    fi
    apt_update
    if package=$(native_chinese_engine); then
        # Preserve strict failure handling inside this function: calling it in
        # an if/! context would mask package/download failures in Bash.
        set +e
        (
            set -Eeuo pipefail
            shopt -s inherit_errexit
            install_native_input "$package"
        )
        native_result=$?
        set -e
        if (( native_result == 21 )) && [[ "$requested" == auto ]]; then
            warn "系统中文引擎已安装但无法初始化，回退到搜狗；Wayland 原生应用可能不支持 Fcitx4。"
            install_sogou_input
            printf 'native-unavailable\n' | as_root tee /etc/ubuntu-autoconfig/input-fallback >/dev/null
        else
            return "$native_result"
        fi
    elif [[ "$requested" == auto ]]; then
        warn "当前系统/架构没有可用的发行版中文引擎，尝试搜狗；Wayland 原生应用仍受 Fcitx4 兼容性限制。"
        install_sogou_input
        printf 'native-unavailable\n' | as_root tee /etc/ubuntu-autoconfig/input-fallback >/dev/null
    else
        die "当前软件源没有可用的系统中文引擎。"
    fi
}
