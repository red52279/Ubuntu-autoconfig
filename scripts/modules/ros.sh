#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

ros_distro_for_system() {
    case "$UBUNTU_CODENAME" in
        focal) printf 'noetic' ;;
        jammy) printf 'humble' ;;
        noble) printf 'jazzy' ;;
        resolute) printf 'lyrical' ;;
        *) return 1 ;;
    esac
}

install_ros() {
    if [[ "$ARCH" == armhf ]]; then
        warn "ROS 官方仓库不为 armhf 提供完整桌面包，跳过 ROS 安装。"
        return "$SKIP_STATUS"
    fi
    local distro
    if ! distro=$(ros_distro_for_system); then
        warn "未找到 Ubuntu $UBUNTU_CODENAME 对应的 ROS 发行版，跳过。"
        return "$SKIP_STATUS"
    fi
    apt_install gnupg ca-certificates
    say "为 Ubuntu $UBUNTU_CODENAME/$ARCH 匹配 ROS $distro"
    configure_ros_source "$distro"
    APT_UPDATED=1
    local ros_pkg="ros-${distro}-desktop"
    if [[ "$distro" == noetic ]]; then
        if (( DRY_RUN )) || apt_has_candidate "ros-${distro}-desktop-full"; then
            ros_pkg="ros-${distro}-desktop-full"
        fi
    fi
    apt_install "$ros_pkg"
    if (( ! DRY_RUN )) && has_pkg "$ros_pkg"; then
        as_root tee /etc/profile.d/ros.sh >/dev/null <<PROFILE
# ROS $distro
. /opt/ros/$distro/setup.sh
PROFILE
    fi
}

configure_ros_source() {
    local distro=$1
    local key=/usr/share/keyrings/ros-archive-keyring.gpg
    local list=/etc/apt/sources.list.d/ros2.list
    local repository=ros2 base
    [[ "$distro" != noetic ]] || repository=ros
    if (( DRY_RUN )); then
        say "将配置 ROS 签名仓库（清华、阿里云、中科大）"
        return 0
    fi
    as_root install -d -m 0755 /usr/share/keyrings /etc/apt/sources.list.d
    install_signing_key "$key" C1CF6E31E6BADE8868B172B4F42ED6FBAB17C654 \
        https://mirrors.tuna.tsinghua.edu.cn/rosdistro/ros.asc \
        https://mirrors.ustc.edu.cn/rosdistro/ros.asc
    for base in https://mirrors.tuna.tsinghua.edu.cn https://mirrors.aliyun.com \
        https://mirrors.ustc.edu.cn; do
        if apt_configure_source "$list" <<SOURCE
deb [arch=$ARCH signed-by=$key] $base/$repository/ubuntu $UBUNTU_CODENAME main
SOURCE
        then
            return 0
        fi
        warn "ROS 仓库索引更新失败，尝试下一镜像: $base"
    done
    die "没有可用的 ROS 签名仓库: $UBUNTU_CODENAME/$ARCH"
}

install_rosdep() {
    local distro
    distro=$(ros_distro_for_system 2>/dev/null || true)
    [ -n "$distro" ] || { warn "当前系统没有可匹配的 ROS 发行版，跳过 rosdep。"; return "$SKIP_STATUS"; }
    apt_install gnupg ca-certificates
    configure_ros_source "$distro"
    APT_UPDATED=1
    apt_install python3-rosdep
    if [[ "$distro" != noetic ]]; then apt_install ros-dev-tools; fi
    if (( DRY_RUN )); then
        say "将配置清华 rosdep 索引，并为调用用户更新 $distro 缓存"
        return 0
    fi
    command -v rosdep >/dev/null 2>&1 || die "没有找到 rosdep 命令。"
    local source_list=/etc/ros/rosdep/sources.list.d/20-default.list
    local index_url=https://mirrors.tuna.tsinghua.edu.cn/rosdistro/index-v4.yaml
    local target_user=${SUDO_USER:-$(id -un)}
    local update_args=(update --rosdistro "$distro")
    [[ "$distro" != noetic ]] || update_args+=(--include-eol-distros)
    as_root install -d -m 0755 /etc/ros/rosdep/sources.list.d
    if [[ -f "$source_list" ]]; then
        as_root cp -a "$source_list" "$source_list.bak-$(date +%Y%m%d-%H%M%S)"
    fi
    as_root tee "$source_list" >/dev/null <<'ROSDEP_LIST'
# Official rosdep data through the TUNA mirror.
yaml https://mirrors.tuna.tsinghua.edu.cn/rosdistro/rosdep/base.yaml
yaml https://mirrors.tuna.tsinghua.edu.cn/rosdistro/rosdep/python.yaml
yaml https://mirrors.tuna.tsinghua.edu.cn/rosdistro/rosdep/ruby.yaml
ROSDEP_LIST
    as_root tee /etc/profile.d/rosdep.sh >/dev/null <<'PROFILE'
export ROSDISTRO_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/rosdistro/index-v4.yaml
PROFILE
    as_user "$target_user" env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
        NO_PROXY='*' no_proxy='*' ROSDISTRO_INDEX_URL="$index_url" rosdep "${update_args[@]}"
}
