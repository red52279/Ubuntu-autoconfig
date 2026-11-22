#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

install_time_sync() {
    if (( DRY_RUN )); then
        say "配置国内 NTP（阿里云、腾讯、国家授时中心），时区 Asia/Shanghai，并设置 RTC 为 Windows 默认的本地时间"
        return 0
    fi
    apt_install chrony
    local backup=/var/lib/ubuntu-autoconfig/time-backups/$(date +%Y%m%d-%H%M%S)
    as_root install -d -m 0755 "$backup"
    as_root cp -a /etc/chrony/chrony.conf "$backup/chrony.conf"
    [[ ! -f /etc/adjtime ]] || as_root cp -a /etc/adjtime "$backup/adjtime"
    # A standalone configuration avoids foreign pools included by Ubuntu's
    # default sources.d and DHCP-provided NTP servers.
    as_root tee /etc/chrony/chrony.conf >/dev/null <<'CHRONY'
# Managed by ubuntu-autoconfig. Domestic NTP; Windows-compatible local RTC.
server ntp.aliyun.com iburst
server ntp.tencent.com iburst
server ntp.ntsc.ac.cn iburst
driftfile /var/lib/chrony/chrony.drift
makestep 1.0 3
leapsectz right/UTC
logdir /var/log/chrony
CHRONY
    as_root timedatectl set-timezone Asia/Shanghai
    as_root systemctl enable --now chrony.service
    as_root systemctl restart chrony.service
    as_root chronyc online
    as_root chronyc waitsync 15 0.5 0 2
    # Without --adjust-system-clock, write the synchronized system time to RTC;
    # never overwrite system time with an old hardware-clock value.
    as_root timedatectl set-local-rtc 1
    [[ $(timedatectl show --property=LocalRTC --value) == yes ]] || die "RTC 本地时间设置未生效。"
    [[ $(tail -n 1 /etc/adjtime) == LOCAL ]] || die "/etc/adjtime 未设置为 LOCAL。"
    as_root chronyc tracking
    say "已启用国内网络校时，RTC 使用 Windows 默认的本地时间（Asia/Shanghai）。"
}
