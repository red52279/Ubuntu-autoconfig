#!/usr/bin/env bash
# Shared helpers for the Ubuntu autoconfiguration modules.

set -o pipefail
export PYTHONDONTWRITEBYTECODE=1

: "${DRY_RUN:=0}"
: "${APT_UPDATED:=0}"
: "${ARCH:=}"
: "${UBUNTU_CODENAME:=}"
: "${SKIP_STATUS:=20}"
: "${MIRROR_BASE:=https://mirrors.tuna.tsinghua.edu.cn/ubuntu}"
: "${MIRROR_PORTS:=https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports}"
NETWORK_HELPER="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/network.py"

setup_run_temp() {
    local directory
    directory=$(cd -- "$1" && pwd -P) || return $?
    RUN_TEMP_DIR=$(mktemp -d -- "$directory/tmp.XXXXXX") || return $?
    # APT's sandbox user and the target desktop user also need temporary files.
    if (( EUID == 0 )); then chmod 1777 "$RUN_TEMP_DIR" || return $?; fi
    export TMPDIR="$RUN_TEMP_DIR" TMP="$RUN_TEMP_DIR" TEMP="$RUN_TEMP_DIR"
}

cleanup_run_temp() {
    if [[ -n ${RUN_TEMP_DIR:-} ]]; then
        rm -rf -- "$RUN_TEMP_DIR"
    fi
}

setup_download_cache() {
    local state=$1
    export AUTOCONFIG_CACHE_DIR="$state/runs/cache"
    if [[ -d "$state/cache" && ! -L "$state/cache" &&
          ! -e "$AUTOCONFIG_CACHE_DIR" && ! -L "$AUTOCONFIG_CACHE_DIR" ]]; then
        mv -T -- "$state/cache" "$AUTOCONFIG_CACHE_DIR" ||
            warn "旧缓存无法迁移，将使用 $AUTOCONFIG_CACHE_DIR。"
    fi
}

network_tool() {
    local command=(python3 "$NETWORK_HELPER")
    if [[ -n ${DOWNLOAD_TIMEOUT:-} ]]; then
        command=(timeout --foreground --kill-after=10s "${DOWNLOAD_TIMEOUT}s" "${command[@]}")
    fi
    "${command[@]}" "$@"
}

install_signing_key() (
    set -Eeuo pipefail
    local key=$1 fingerprint=$2 tmp_dir actual
    shift 2
    tmp_dir=$(mktemp -d)
    trap 'rm -rf -- "$tmp_dir"' EXIT
    download_from_mirrors "$tmp_dir/key.asc" "$@"
    actual=$(gpg --batch --homedir "$tmp_dir" --show-keys --with-colons "$tmp_dir/key.asc" |
        awk -F: '$1 == "pub" { primary=1 } $1 == "fpr" && primary { print $10; primary=0 }')
    [[ "$actual" == "$fingerprint" ]] || die "APT 签名公钥指纹不匹配: $key"
    gpg --batch --homedir "$tmp_dir" --dearmor -o "$tmp_dir/key.gpg" "$tmp_dir/key.asc"
    as_root install -m 0644 "$tmp_dir/key.gpg" "$key"
)

select_domestic_mirror() {
    if (( DRY_RUN )); then
        say "将绕过代理，直连测速清华、中科大、南京大学、阿里云 Ubuntu 镜像并选取最快可用源"
        return 0
    fi
    local archive=ubuntu selected
    [[ "$ARCH" == amd64 ]] || archive=ubuntu-ports
    selected=$(network_tool select --kind xz \
        --suffix "/dists/$UBUNTU_CODENAME/main/binary-$ARCH/Packages.xz" \
        "https://mirrors.tuna.tsinghua.edu.cn/$archive" \
        "https://mirrors.ustc.edu.cn/$archive" \
        "https://mirrors.nju.edu.cn/$archive" \
        "https://mirrors.aliyun.com/$archive") || return $?
    if [[ "$ARCH" == amd64 ]]; then MIRROR_BASE=$selected; else MIRROR_PORTS=$selected; fi
    export MIRROR_BASE MIRROR_PORTS
    say "本次最快可用国内 Ubuntu 源: $selected（直连）"
}

apt_get() {
    local host proxy_options=() deadline=() source_options=()
    if [[ -n ${AUTOCONFIG_APT_SOURCES_DIR:-} ]]; then
        local sources=$AUTOCONFIG_APT_SOURCES_DIR
        network_tool audit-apt "$sources/sources.list" "$sources/sources.list.d/"*.list "$sources/sources.list.d/"*.sources || return $?
        source_options=(-o "Dir::Etc::sourcelist=$sources/sources.list"
                        -o "Dir::Etc::sourceparts=$sources/sources.list.d" -o APT::Get::List-Cleanup=false)
    else
        network_tool audit-apt || return $?
    fi
    for host in mirrors.tuna.tsinghua.edu.cn mirrors.ustc.edu.cn mirrors.nju.edu.cn mirrors.aliyun.com \
        mirrors.huaweicloud.com typoraio.cn downloads.typoraio.cn; do
        proxy_options+=(-o "Acquire::http::Proxy::$host=DIRECT" -o "Acquire::https::Proxy::$host=DIRECT")
    done
    if [[ -n ${APT_FETCH_TIMEOUT:-} ]]; then deadline=(timeout --foreground --kill-after=10s "${APT_FETCH_TIMEOUT}s"); fi
    as_root env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
        NO_PROXY='*' no_proxy='*' DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a "${deadline[@]}" apt-get \
        -o Acquire::http::Proxy=DIRECT -o Acquire::https::Proxy=DIRECT \
        -o Acquire::http::Proxy-Auto-Detect= -o Acquire::https::Proxy-Auto-Detect= \
        -o Acquire::http::AllowRedirect=false -o Acquire::https::AllowRedirect=false \
        -o Acquire::Retries=2 -o Acquire::http::Timeout=25 -o Acquire::https::Timeout=25 \
        -o DPkg::Lock::Timeout=120 "${proxy_options[@]}" "${source_options[@]}" "$@"
}

say()  { printf '\033[1;34m[autoconfig]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[autoconfig]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[autoconfig]\033[0m %s\n' "$*" >&2; exit 1; }

run() {
    if (( DRY_RUN )); then
        printf '+ %q' "$1"
        shift
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

run_shell() {
    if (( DRY_RUN )); then
        printf '+ %s\n' "$1"
        return 0
    fi
    bash -c "$1"
}

as_root() {
    if (( EUID == 0 )); then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        die "需要 root 权限；请以 root 运行或安装 sudo。"
    fi
}

as_user() {
    local account=$1
    shift
    if [[ $(id -un) == "$account" ]]; then
        "$@"
    elif [[ -n ${RUN_TEMP_DIR:-} ]]; then
        as_root sudo -H -u "$account" env TMPDIR="$RUN_TEMP_DIR" TMP="$RUN_TEMP_DIR" TEMP="$RUN_TEMP_DIR" "$@"
    else
        as_root sudo -H -u "$account" "$@"
    fi
}

apt_update() {
    if (( APT_UPDATED )); then return 0; fi
    if (( DRY_RUN )); then
        say "将更新 APT 软件包索引"
        APT_UPDATED=1
        return 0
    fi
    say "更新 APT 软件包索引"
    APT_FETCH_TIMEOUT=180 apt_get -o APT::Update::Error-Mode=any update || return $?
    APT_UPDATED=1
}

apt_configure_source() (
    local source=$1 tmp_dir restore=0
    tmp_dir=$(mktemp -d) || return $?
    trap 'if (( restore )); then
        if [[ -f "$tmp_dir/previous" ]]; then
            as_root install -m 0644 "$tmp_dir/previous" "$source"
        else
            as_root rm -f -- "$source"
        fi
    fi
    rm -rf -- "$tmp_dir"' EXIT
    if [[ -f "$source" ]]; then cp -p "$source" "$tmp_dir/previous" || return $?; fi
    cat > "$tmp_dir/source" || return $?
    restore=1
    as_root install -m 0644 "$tmp_dir/source" "$source" || return $?
    APT_UPDATED=0
    apt_update || return $?
    restore=0
)

clean_reinstall_package() {
    local package=$1 source=$2 allowed simulation
    (( ${CLEAN_REINSTALL:-0} )) || return 0
    has_pkg "$package" || return 0
    allowed=$(/usr/bin/python3 - "$NETWORK_HELPER" "${CURRENT_STEP:-}" <<'PYCLEAN'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).parent))
from catalog import FEATURES
print('\n'.join(FEATURES.get(sys.argv[2],{}).get('clean',[])))
PYCLEAN
)
    grep -Fxq "$package" <<< "$allowed" || return 0
    # Download the replacement/dependencies before removing the working app.
    apt_update
    APT_FETCH_TIMEOUT=300 apt_get --download-only --reinstall install -y --no-install-recommends "$source"
    simulation=$(LC_ALL=C apt_get -s remove -- "$package")
    printf '%s\n' "$simulation" | /usr/bin/python3 -c '
import re,sys
allowed=sys.argv[1].split(":")[0]
extra=[p for p in re.findall(r"^Remv (\S+)",sys.stdin.read(),re.M) if p.split(":")[0] != allowed]
if extra: raise SystemExit("卸载会影响其他软件，已停止: " + ", ".join(extra))
' "$package"
    say "卸载后重装: $package（保留用户数据）"
    apt_get remove -y -- "$package"
}

apt_install() {
    (($#)) || return 0
    if (( DRY_RUN )); then
        say "将检查并仅安装缺少的软件包: $*"
        if (( ${CLEAN_REINSTALL:-0} )); then say "独立应用会先下载验证，再卸载后重装。"; fi
        return 0
    fi
    local item package version pending=()
    for item in "$@"; do
        if [[ "$item" == /*.deb ]]; then
            package=$(dpkg-deb -f "$item" Package)
            version=$(dpkg-deb -f "$item" Version)
            clean_reinstall_package "$package" "$item"
            if has_pkg "$package" && [[ $(dpkg-query -W -f='${Version}' "$package") == "$version" ]]; then
                say "已安装同版本，跳过: $package $version"
                continue
            fi
        else
            clean_reinstall_package "$item" "$item"
            if has_pkg "$item"; then continue; fi
        fi
        pending+=("$item")
    done
    ((${#pending[@]})) || { say "所需软件包均已安装。"; return 0; }
    apt_update || return $?
    say "安装缺少的软件包: ${pending[*]}"
    local options=()
    if (( ${CLEAN_REINSTALL:-0} || ${APT_INSTALL_NO_REMOVE:-0} )); then options+=(--no-remove); fi
    apt_get install -y --no-install-recommends "${options[@]}" "${pending[@]}"
}

apt_install_available() {
    local package available=() missing=() installed=0
    for package in "$@"; do
        if (( ! DRY_RUN )) && has_pkg "$package" && (( ! ${CLEAN_REINSTALL:-0} )); then
            installed=1
        else
            missing+=("$package")
        fi
    done
    ((${#missing[@]})) || return 0
    apt_update || return $?
    for package in "${missing[@]}"; do
        if (( DRY_RUN )) || apt_has_candidate "$package"; then available+=("$package")
        else warn "当前 Ubuntu/架构没有软件包 $package，跳过。"; fi
    done
    if ((${#available[@]})); then apt_install "${available[@]}"
    elif (( installed )); then return 0
    else return "$SKIP_STATUS"; fi
}

apt_has_candidate() {
    local candidate
    candidate=$(LC_ALL=C apt-cache policy "$1" | awk '$1 == "Candidate:" {print $2; exit}') || return $?
    [[ -n "$candidate" && "$candidate" != '(none)' ]]
}

# Use one multiarch entry. Separate amd64/i386 entries duplicate architecture-
# independent Packages, translations and AppStream targets on recent APT.
configure_ubuntu_i386_source() {
    if (( DRY_RUN )); then
        say "将配置国内 Ubuntu i386 软件源: $MIRROR_BASE ($UBUNTU_CODENAME)"
        return 0
    fi
    local suffix
    as_root install -d -m 0755 /etc/apt/sources.list.d
    {
    printf '# Managed by ubuntu-autoconfig; domestic amd64 and i386 packages.\n'
    for suffix in '' -updates -security -backports; do
        printf 'deb [arch=amd64,i386] %s %s%s main restricted universe multiverse\n' \
            "$MIRROR_BASE" "$UBUNTU_CODENAME" "$suffix"
    done
    } | as_root tee /etc/apt/sources.list >/dev/null
    as_root rm -f /etc/apt/sources.list.d/ubuntu-i386.list
    APT_UPDATED=0
}

# Download to a separate file so an HTTP error cannot leave an empty or
# partially downloaded installer at the destination. Keep TLS verification on.
tmp_download() {
    local url=$1 output=$2
    download_from_mirrors "$output" "$url"
}

download_from_mirrors() {
    local output=$1 kind=text
    shift
    case "$output" in
        *.deb) kind=deb ;; *.zip) kind=zip ;; *.sh) kind=sh ;;
        *.yaml) kind=yaml ;; *.asc) kind=key ;; *.html) kind=html ;;
    esac
    network_tool download --kind "$kind" --output "$output" "$@"
}

has_pkg() { dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null | grep -q '^ii'; }

binary_matches_arch() {
    PYTHONPATH="${NETWORK_HELPER%/*}" /usr/bin/python3 -c '
import sys
from preflight import binary_arch
raise SystemExit(binary_arch(sys.argv[1]) != sys.argv[2])
' "$1" "$ARCH"
}

set_locale() {
    local locale_file
    if (( DRY_RUN )); then
        say "设置系统区域为 zh_CN.UTF-8（保留现有非区域设置）"
        return 0
    fi
    as_root locale-gen zh_CN.UTF-8
    if command -v localectl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
        as_root localectl set-locale LANG=zh_CN.UTF-8
    fi
    # Ubuntu 26.04 uses /etc/locale.conf; older Ubuntu also reads default/locale.
    for locale_file in /etc/default/locale /etc/locale.conf; do
        as_root tee "$locale_file" >/dev/null <<'LOCALE'
LANG=zh_CN.UTF-8
LANGUAGE=zh_CN:zh:en_US:en
LOCALE
    done
}

architecture_name() {
    case "${ARCH:-$(dpkg --print-architecture 2>/dev/null || true)}" in
        amd64) printf 'x86_64\n' ;;
        arm64) printf 'aarch64\n' ;;
        armhf) printf 'armv7\n' ;;
        *) printf '%s\n' "${ARCH:-unknown}" ;;
    esac
}
