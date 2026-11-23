#!/usr/bin/env bash
# Applications which have either an official domestic endpoint or an Ubuntu
# archive package. No installer archives are kept in this repository.
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_gedit() {
    apt_install gedit
}

configure_chrome_updates() {
    {
        if [[ -n "$1" ]]; then printf '%s\n' "$1"; fi
        printf '%s\n' 'repo_add_once="false"' 'repo_reenable_on_distupgrade="false"'
    } | as_root tee /etc/default/google-chrome >/dev/null
}

install_chrome() (
    if (( DRY_RUN )); then
        say "从山东大学星火签名镜像下载 Google Chrome 官方稳定版 deb"
        return 0
    fi
    local defaults=/etc/default/google-chrome settings=
    if [[ -f "$defaults" ]]; then
        settings=$(sed -E '/^[[:space:]]*(repo_add_once|repo_reenable_on_distupgrade)[[:space:]]*=/d' "$defaults")
    fi
    # A clean reinstall can remove defaults before dpkg runs the postinst again.
    trap 'archive_foreign_app_sources /etc/apt/sources.list.d/google-chrome.{list,sources}
        configure_chrome_updates "$settings"' EXIT
    configure_chrome_updates "$settings"
    if (( ${CLEAN_REINSTALL:-0} )) || ! has_pkg google-chrome-stable; then
        install_vendor_deb google-chrome-stable
    fi
)

install_steam() {
    if [[ "$ARCH" != amd64 ]]; then
        warn "Ubuntu 官方 Steam 安装器目前仅提供 amd64，当前为 $ARCH，跳过。"
        return "$SKIP_STATUS"
    fi
    # steam-installer is the signed Ubuntu multiverse package. It is fetched
    # from the domestic Ubuntu mirror configured by install.sh; no Valve
    # repository is added by this installer.
    if (( ! DRY_RUN )); then
        as_root dpkg --add-architecture i386
    fi
    configure_ubuntu_i386_source
    apt_install steam-installer
}

archive_foreign_app_sources() {
    local source backup=
    for source in "$@"; do
        [[ -f "$source" ]] || continue
        network_tool audit-apt "$source" >/dev/null 2>&1 && continue
        if [[ -z "$backup" ]]; then
            as_root install -d -m 0755 "$STATE_DIR/source-backups"
            backup=$(as_root mktemp -d "$STATE_DIR/source-backups/vendor.XXXXXX")
        fi
        as_root mv -- "$source" "$backup/"
    done
}

install_microsoft_app() (
    local package=$1
    if (( DRY_RUN )); then
        say "从本地 deb 或山大星火签名镜像安装 $package"
        return 0
    fi
    if (( ! ${CLEAN_REINSTALL:-0} )) && has_pkg "$package"; then return 0; fi
    # The vendor deb can create an update feed during dpkg configuration.
    trap 'archive_foreign_app_sources /etc/apt/sources.list.d/{microsoft-edge,vscode}.{list,sources}' EXIT
    install_vendor_deb "$package"
)

install_edge() {
    install_microsoft_app microsoft-edge-stable
}

install_vscode() {
    install_microsoft_app code
}

install_downloaded_deb() (
    set -Eeuo pipefail
    local tmp_dir
    tmp_dir=$(mktemp -d)
    trap 'rm -rf -- "$tmp_dir"' EXIT
    download_from_mirrors "$tmp_dir/package.deb" "$@"
    install_checked_deb "$tmp_dir/package.deb" "${EXPECTED_DEB_PACKAGE:-}" "${EXPECTED_DEB_SHA256:-}"
)

install_checked_deb() {
    local file=$1 expected_package=$2 digest=${3:-} package_arch
    dpkg-deb --info "$file" >/dev/null
    if [[ -n "$expected_package" && $(dpkg-deb -f "$file" Package) != "$expected_package" ]]; then
        die "下载的 deb 不是 $expected_package。"
    fi
    if [[ -n "$digest" ]]; then
        printf '%s  %s\n' "$digest" "$file" | sha256sum --check --status ||
            die "$expected_package 的 SHA-256 与签名索引不一致。"
    fi
    package_arch=$(dpkg-deb -f "$file" Architecture)
    [[ "$package_arch" == "$ARCH" || "$package_arch" == all ]] || die "下载的 deb 架构为 $package_arch，系统为 $ARCH。"
    chmod 0755 "${file%/*}"
    chmod 0644 "$file"
    apt_install "$file"
}

fetch_vendor_deb() (
    local package=$1 output=$2 file="$SCRIPT_DIR/packages.local/${1}_${ARCH}.deb" tmp_dir release url digest
    tmp_dir=$(mktemp -d)
    trap 'rm -rf -- "$tmp_dir"' EXIT
    if [[ -f "$file" ]]; then
        cp -- "$file" "$output"
        return
    fi
    local base="https://mirrors.sdu.edu.cn/spark-store/$ARCH-store"
    install_signing_key "$tmp_dir/spark.gpg" 9D9AA859F75024B1A1ECE16E0E41D354A29A440C \
        https://gitee.com/spark-store-project/spark-store/raw/master/tool/spark-store.asc
    download_from_mirrors "$tmp_dir/InRelease" "$base/InRelease"
    gpgv --keyring "$tmp_dir/spark.gpg" --output "$tmp_dir/Release" "$tmp_dir/InRelease"
    download_from_mirrors "$tmp_dir/Packages" "$base/Packages"
    release=$(/usr/bin/python3 "${NETWORK_HELPER%/*}/vendor_release.py" spark-archive "$ARCH" \
        --package "$package" --release "$tmp_dir/Release" --index "$tmp_dir/Packages")
    IFS=$'\t' read -r url digest <<< "$release"
    DOWNLOAD_TIMEOUT=300 download_from_mirrors "$output" "$url"
    printf '%s  %s\n' "$digest" "$output" | sha256sum --check --status
)

install_vendor_deb() (
    local package=$1 tmp_dir
    tmp_dir=$(mktemp -d)
    trap 'rm -rf -- "$tmp_dir"' EXIT
    apt_install ca-certificates gnupg gpgv
    fetch_vendor_deb "$package" "$tmp_dir/package.deb"
    install_checked_deb "$tmp_dir/package.deb" "$package"
)

qq_release_url() {
    if [[ -n ${QQ_OFFICIAL_URL:-} ]]; then
        printf '%s\n' "$QQ_OFFICIAL_URL"
    else
        python3 "${NETWORK_HELPER%/*}/qq.py" --arch "$ARCH"
    fi
}

install_qq() (
    set -Eeuo pipefail
    if [[ "$ARCH" != amd64 && "$ARCH" != arm64 ]]; then
        warn "腾讯 QQ 当前安装流程支持 amd64/arm64，当前为 $ARCH，跳过。"
        return "$SKIP_STATUS"
    fi
    if (( ! DRY_RUN && ! ${CLEAN_REINSTALL:-0} )) && has_pkg linuxqq; then
        say "QQ 已安装，跳过下载。"
        return 0
    fi
    if (( DRY_RUN )); then
        say "从腾讯官网下载配置和公开签名接口获取当前 QQ deb，校验后安装"
        return 0
    fi
    apt_install curl ca-certificates python3
    local url
    url=$(qq_release_url)
    EXPECTED_DEB_PACKAGE=linuxqq install_downloaded_deb "$url"
)

wechat_release_urls() {
    local platform
    case "$ARCH" in
        amd64) platform=x86_64 ;;
        arm64) platform=arm64 ;;
        *) return "$SKIP_STATUS" ;;
    esac
    if [[ -n ${WECHAT_OFFICIAL_URL:-} ]]; then
        printf '%s\n' "$WECHAT_OFFICIAL_URL"
    else
        printf '%s\n' "https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_${platform}.deb" \
            "https://dldir1.qq.com/weixin/Universal/Linux/WeChatLinux_${platform}.deb"
    fi
}

install_wechat() {
    if [[ "$ARCH" != amd64 && "$ARCH" != arm64 ]]; then
        warn "腾讯微信官方 Linux 包提供 amd64 和 arm64，当前为 $ARCH。"
        return "$SKIP_STATUS"
    fi
    if (( ! DRY_RUN && ! ${CLEAN_REINSTALL:-0} )) && has_pkg wechat; then
        say "微信已安装，跳过下载。"
        return 0
    fi
    apt_install curl ca-certificates
    # Official Tencent mainland download endpoint. Override this when Tencent
    # publishes a newer filename through WECHAT_OFFICIAL_URL.
    if (( DRY_RUN )); then
        say "从腾讯官方国内地址下载微信并安装"
        return 0
    fi
    local urls=()
    mapfile -t urls < <(wechat_release_urls)
    EXPECTED_DEB_PACKAGE=wechat install_downloaded_deb "${urls[@]}"
}

fetch_typora_deb() (
    local output=$1 tmp_dir release url digest
    tmp_dir=$(mktemp -d)
    trap 'rm -rf -- "$tmp_dir"' EXIT
    install_signing_key "$tmp_dir/typora.gpg" 4AC441BE68B4ADAB7439FBF9BA300B7755AFCFAE \
        https://typoraio.cn/linux/public-key.asc
    download_from_mirrors "$tmp_dir/InRelease" https://downloads.typoraio.cn/linux/InRelease
    gpgv --keyring "$tmp_dir/typora.gpg" --output "$tmp_dir/Release" "$tmp_dir/InRelease"
    download_from_mirrors "$tmp_dir/Packages" https://downloads.typoraio.cn/linux/Packages
    release=$(/usr/bin/python3 "${NETWORK_HELPER%/*}/vendor_release.py" typora-archive "$ARCH" \
        --release "$tmp_dir/Release" --index "$tmp_dir/Packages")
    IFS=$'\t' read -r url digest <<< "$release"
    DOWNLOAD_TIMEOUT=300 download_from_mirrors "$output" "$url"
    printf '%s  %s\n' "$digest" "$output" | sha256sum --check --status
)

install_typora() (
    local list=/etc/apt/sources.list.d/typora.list
    if (( DRY_RUN )); then
        say "将验证 Typora 官方签名索引并使用中国 CDN 安装，迁移旧版 APT 配置"
        return 0
    fi
    if (( ${CLEAN_REINSTALL:-0} )) || ! has_pkg typora; then
        local tmp_dir
        apt_install ca-certificates gnupg gpgv
        tmp_dir=$(mktemp -d)
        trap 'rm -rf -- "$tmp_dir"' EXIT
        fetch_typora_deb "$tmp_dir/package.deb"
        install_checked_deb "$tmp_dir/package.deb" typora
    fi
    if [[ -f "$list" ]] && /usr/bin/python3 "${NETWORK_HELPER%/*}/vendor_release.py" \
        typora-legacy-source "$ARCH" --source "$list"; then
        as_root rm -- "$list"
        say "已移除旧版安装器生成的 Typora APT 源，版本检查使用官方签名索引。"
    fi
)

configure_anaconda_channels() {
    if (( DRY_RUN )); then return 0; fi
    [[ -x /opt/anaconda3/bin/conda ]] || die "未找到 Anaconda，停止写入频道配置。"
    as_root install -d -m 0755 /etc/conda
    as_root tee /etc/conda/.condarc >/dev/null <<'CONDARC'
channels:
  - defaults
default_channels:
  - https://mirrors.nju.edu.cn/anaconda/pkgs/main
  - https://mirrors.nju.edu.cn/anaconda/pkgs/r
custom_channels:
  conda-forge: https://mirrors.nju.edu.cn/anaconda/cloud
show_channel_urls: true
auto_activate_base: false
CONDARC
    as_root install -d -m 0755 /etc/profile.d
    as_root tee /etc/profile.d/anaconda.sh >/dev/null <<'PROFILE'
# Make conda available without replacing Ubuntu's Python (required by ROS).
if [ -r /opt/anaconda3/etc/profile.d/conda.sh ]; then
    . /opt/anaconda3/etc/profile.d/conda.sh
fi
PROFILE
}

install_anaconda() (
    set -Eeuo pipefail
    local suffix version filename tmp_dir install_path
    case "$ARCH" in
        amd64) suffix=x86_64 ;;
        arm64) suffix=aarch64 ;;
        *) warn "Anaconda 官方 Linux 构建不支持 $ARCH，跳过。"; return "$SKIP_STATUS" ;;
    esac
    apt_install curl ca-certificates
    version="${ANACONDA_VERSION:-2024.10-1}"
    # NJU serves installer bytes; TUNA may reject GET requests even when HEAD
    # succeeds. Only use domestic mirrors, and validate the downloaded format.
    filename="Anaconda3-${version}-Linux-${suffix}.sh"
    local urls=(
        "https://mirrors.nju.edu.cn/anaconda/archive/$filename"
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/archive/$filename"
    )
    if [[ -n "${ANACONDA_MIRROR_BASE_URL:-}" ]]; then
        urls=("${ANACONDA_MIRROR_BASE_URL%/}/$filename" "${urls[@]}")
    fi
    install_path=/opt/anaconda3
    if (( ! ${UPDATE_ARCHIVE:-0} )) && [[ -x "$install_path/bin/conda" && -x "$install_path/bin/python" ]] && \
        env -u PYTHONPATH -u PYTHONHOME "$install_path/bin/python" -c 'import conda, numpy, pandas' >/dev/null 2>&1; then
        configure_anaconda_channels
        say "Anaconda 已安装，跳过。"
        return 0
    fi
    if (( DRY_RUN )); then
        say "通过南京大学/清华国内镜像安装官方 Anaconda $version（下载失败自动切换）"
        return 0
    fi
    if [[ -e "$install_path" && ! -f "$install_path/.ubuntu-autoconfig-owned" ]]; then
        die "$install_path 已存在但不完整，且不是本脚本创建的环境；已保留，请先检查。"
    fi
    tmp_dir=$(mktemp -d)
    trap 'rm -rf -- "$tmp_dir"' EXIT
    download_from_mirrors "$tmp_dir/installer.sh" "${urls[@]}"
    as_root install -d -m 0755 "$install_path"
    as_root touch "$install_path/.ubuntu-autoconfig-owned"
    # The 2024 installer can fail in parallel extraction on a partial/large
    # installation. A clean single-thread extraction was verified on 26.04.
    as_root env -u PYTHONPATH -u PYTHONHOME -u http_proxy -u https_proxy -u all_proxy \
        -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY CONDA_EXTRACT_THREADS=1 \
        CONDA_REGISTER_ENVS=false bash "$tmp_dir/installer.sh" -b -u -p "$install_path"
    [[ -x "$install_path/bin/conda" ]] || die "Anaconda 安装器未生成 conda。"
    env -u PYTHONPATH -u PYTHONHOME "$install_path/bin/python" -c 'import conda, numpy, pandas'
    printf '%s\n' "$version" | as_root tee "$install_path/.ubuntu-autoconfig-version" >/dev/null
    configure_anaconda_channels
)

install_spark_store() {
    if (( DRY_RUN )); then
        say "直连星火商店 Gitee 官方发布下载 $ARCH deb"
        return 0
    fi
    if (( ! ${CLEAN_REINSTALL:-0} )) && has_pkg spark-store; then return 0; fi
    apt_install ca-certificates
    local url
    url=$(/usr/bin/python3 "${NETWORK_HELPER%/*}/vendor_release.py" spark-store "$ARCH")
    DOWNLOAD_TIMEOUT=300 EXPECTED_DEB_PACKAGE=spark-store install_downloaded_deb "$url"
}
