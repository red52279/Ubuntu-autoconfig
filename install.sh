#!/usr/bin/env bash
# Ubuntu 20.04-26.04 x86/ARM installer.
# Features are independent, resumable and can be selected from config.json or
# an optional whiptail menu.
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/scripts/lib/common.sh"
source "$SCRIPT_DIR/scripts/lib/state.sh"
source "$SCRIPT_DIR/scripts/lib/lifecycle.sh"
source "$SCRIPT_DIR/scripts/lib/planning.sh"
ORIGINAL_ARGS=("$@")
if (( EUID == 0 )) && [[ -z ${SUDO_USER:-} && ${PKEXEC_UID:-} =~ ^[0-9]+$ ]]; then
    SUDO_USER=$(getent passwd "$PKEXEC_UID" | cut -d: -f1)
    export SUDO_USER
fi

CONFIG_FILE="$SCRIPT_DIR/config.json"
INTERFACE=auto
RESET_STATE=0
LIST_ONLY=0
CHECK_ONLY=0
ARCH_OVERRIDE=
CLEAN_REINSTALL=0
NO_REBOOT=1
NO_REBOOT_EXPLICIT=0
FORCE=0
REINSTALL=0
INPUT_METHOD=${INPUT_METHOD:-}
BACKGROUND=0
ONLY_FEATURES=
LOG_FILE=
STATE_DIR=
SUCCESS_LOG=
ERROR_LOG=
CURRENT_STEP=
FAILURES=()
SKIPPED=()
LOCK_FD=9

FEATURES=()
declare -A FEATURE_LABEL=() DETECTED=() DETECTION_REASON=()
load_catalog

usage() {
    cat <<'HELP'
用法: sudo ./install.sh [选项]

选项:
  --config FILE       使用另一个 JSON 配置文件
  --gui               使用 whiptail 图形化（终端）勾选功能
  --cli               只使用配置文件，不显示菜单
  --reset             清除断点状态，重新执行已完成步骤（日志保留）
  --fresh             --reset 的别名
  --list              列出当前平台的功能及禁用原因
  --check             只检测实际安装/配置状态，可非 root 运行
  --arch ARCH         与 --check/--list/--dry-run 配合检查 amd64/arm64/armhf
  --dry-run           只显示动作，不修改系统（可非 root 运行）
  --no-reboot         完成后不重启（默认行为）
  --background        选好功能后交给 systemd 后台运行，终端关闭不影响任务
  --only LIST         只选择逗号分隔的功能（如 steam,wechat）；sources 表示仅换源
  --reinstall         重新检查/修复选中项目的配置，保留已安装包
  --clean-reinstall   对 --only 指定的独立应用先卸载再装（保留用户数据）
  --input-method MODE auto（默认）优先系统输入法；ibus 或 sogou 指定方案
  -h, --help          显示帮助

默认读取 config.json 并显示 GUI 菜单；无交互终端时请显式使用 --cli。
每一步成功后写入 /var/lib/ubuntu-autoconfig/state，
中断后再次运行会自动跳过成功步骤；失败步骤会重试并写入 /var/log/ubuntu-autoconfig/。
HELP
}

while (($#)); do
    case "$1" in
        --config) CONFIG_FILE=${2:?--config 需要文件路径}; shift 2 ;;
        --gui) INTERFACE=gui; shift ;;
        --cli) INTERFACE=cli; shift ;;
        --reset|--fresh) RESET_STATE=1; shift ;;
        --list) LIST_ONLY=1; shift ;;
        --check) CHECK_ONLY=1; shift ;;
        --arch) ARCH_OVERRIDE=${2:?--arch 需要架构}; shift 2 ;;
        --clean-reinstall) CLEAN_REINSTALL=1; REINSTALL=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --no-reboot) NO_REBOOT=1; NO_REBOOT_EXPLICIT=1; shift ;;
        --background) BACKGROUND=1; shift ;;
        --only) ONLY_FEATURES=${2:?--only 需要功能列表}; shift 2 ;;
        --reinstall) REINSTALL=1; shift ;;
        --input-method) INPUT_METHOD=${2:?--input-method 需要 auto/ibus/sogou}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf '未知选项: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

case "${INPUT_METHOD:-auto}" in auto|ibus|sogou) ;; *) die "无效输入法选项: $INPUT_METHOD" ;; esac

if [[ -n "$ONLY_FEATURES" ]]; then
    IFS=',' read -ra requested_features <<< "$ONLY_FEATURES"
    for requested_index in "${!requested_features[@]}"; do
        requested_feature=${requested_features[$requested_index]}
        if [[ "$requested_feature" == chromium ]]; then
            requested_feature=chrome
            requested_features[$requested_index]=$requested_feature
        fi
        [[ "$requested_feature" == sources ]] && continue
        [[ "$requested_feature" =~ ^[a-z][a-z0-9_]*$ && -n ${FEATURE_LABEL[$requested_feature]+present} ]] || die "未知功能: $requested_feature"
    done
    ONLY_FEATURES=$(IFS=,; printf '%s' "${requested_features[*]}")
fi

if [[ -n "$ARCH_OVERRIDE" ]]; then
    case "$ARCH_OVERRIDE" in amd64|arm64|armhf) ;; *) die "无效目标架构: $ARCH_OVERRIDE" ;; esac
    (( DRY_RUN || CHECK_ONLY || LIST_ONLY )) || die "--arch 仅用于检测或预演；实际安装自动检测本机架构。"
fi
if (( CLEAN_REINSTALL )); then
    [[ -n "$ONLY_FEATURES" ]] || die "卸载后重装必须用 --only 明确指定独立应用。"
    /usr/bin/python3 - "$SCRIPT_DIR/scripts/lib" "$ONLY_FEATURES" <<'PYSAFE'
import sys
sys.path.insert(0,sys.argv[1])
from catalog import FEATURES
for name in sys.argv[2].split(','):
    if name not in FEATURES or not FEATURES[name]['clean']:
        raise SystemExit(f'{name} 不支持卸载后重装；系统组件请用 --reinstall 修复配置。')
PYSAFE
fi
if (( CHECK_ONLY || LIST_ONLY )); then
    detect_system
    exec /usr/bin/python3 "$SCRIPT_DIR/scripts/lib/preflight.py" --arch "$ARCH" \
        --suite "$UBUNTU_CODENAME" --input-method "${INPUT_METHOD:-auto}"
fi
# Reject explicitly requested unsupported platforms before sudo or source edits.
if [[ -n "$ONLY_FEATURES" ]]; then
    detect_system
    for requested_feature in "${requested_features[@]}"; do
        [[ "$requested_feature" == sources ]] && continue
        requested_support=$(/usr/bin/python3 "$SCRIPT_DIR/scripts/lib/catalog.py" support \
            --feature "$requested_feature" --arch "$ARCH" --suite "$UBUNTU_CODENAME" --input-method "${INPUT_METHOD:-auto}")
        [[ "$requested_support" != disabled* ]] || die "$requested_feature 已禁用: ${requested_support#*$'\t'}"
    done
fi

if [ ! -f "$CONFIG_FILE" ]; then
    die "配置文件不存在: $CONFIG_FILE"
fi

# Previews use the checkout, or a writable XDG state directory if specified.
# Formal runs keep their scratch files and journal under the system runs directory.
if (( DRY_RUN )); then
    RUNS_DIR=$SCRIPT_DIR/runs
    if [[ -n ${XDG_STATE_HOME:-} ]]; then RUNS_DIR=$XDG_STATE_HOME/ubuntu-autoconfig/runs; fi
    STATE_DIR=$RUNS_DIR
    LOG_DIR=$RUNS_DIR/logs
else
    STATE_DIR=/var/lib/ubuntu-autoconfig
    RUNS_DIR=$STATE_DIR/runs
    LOG_DIR=/var/log/ubuntu-autoconfig
fi

# Parse before sudo so --help/--list never need privileges. All mutating runs
# are relaunched once as root with the original arguments.
if (( EUID != 0 && ! DRY_RUN )); then
    if ! command -v sudo >/dev/null 2>&1; then die "需要 root 权限或 sudo。"; fi
    exec sudo -- bash "$SCRIPT_DIR/install.sh" "${ORIGINAL_ARGS[@]}"
fi

# Keep the system awake during an actual run; screen lock remains available.
if (( ! DRY_RUN )) && [[ -z ${AUTOCONFIG_INHIBITED:-} ]] && command -v systemd-inhibit >/dev/null; then
    export AUTOCONFIG_INHIBITED=1
    exec systemd-inhibit --what=sleep --who=ubuntu-autoconfig \
        --why='软件安装进行中，避免休眠中断下载或 dpkg' --mode=block \
        bash "$SCRIPT_DIR/install.sh" "${ORIGINAL_ARGS[@]}"
fi

if (( DRY_RUN )); then
    mkdir -p "$STATE_DIR" "$LOG_DIR"
else
    as_root install -d -m 0755 "$STATE_DIR" "$RUNS_DIR" "$LOG_DIR"
fi
exec {LOCK_FD}>"$STATE_DIR/install.lock"
flock -n "$LOCK_FD" || die "已有另一个安装进程运行（锁: $STATE_DIR/install.lock）。"
RUN_TEMP_DIR=
trap cleanup_run_temp EXIT
setup_run_temp "$RUNS_DIR"

LOG_FILE="$LOG_DIR/install-$(date +%Y%m%d-%H%M%S)-$$.log"
SUCCESS_LOG="$STATE_DIR/success.log"
ERROR_LOG="$STATE_DIR/error.log"
if (( DRY_RUN )); then
    export AUTOCONFIG_CACHE_DIR="$RUNS_DIR/cache"
else
    setup_download_cache "$STATE_DIR"
fi
export CLEAN_REINSTALL
SUMMARY_EVENTS="${LOG_FILE%.log}.events.jsonl"
SUMMARY_STARTED=$SECONDS
trap summary_on_exit EXIT
ln -sfn "$LOG_FILE" "$LOG_DIR/latest.log"
# tee is deliberately installed after argument parsing so every module action
# and error is available for troubleshooting.
exec {UI_FD}>&1
exec > >(tee -a "$LOG_FILE") 2>&1

trap 'rc=$?; if [[ -n "${CURRENT_STEP:-}" ]]; then printf "%s\n" "$CURRENT_STEP" > "$STATE_DIR/failed-step"; fi; printf "[%s] ERROR step=%s rc=%s\n" "$(date --iso-8601=seconds)" "${CURRENT_STEP:-none}" "$rc" >> "$ERROR_LOG"; printf "[%s] 未捕获错误，步骤=%s，退出码=%s\n" "$(date --iso-8601=seconds)" "${CURRENT_STEP:-none}" "$rc" >&2' ERR
trap 'interrupt_run INT' INT
trap 'interrupt_run TERM' TERM
trap 'interrupt_run HUP' HUP

say "日志: $LOG_FILE"
say "Ubuntu 自动配置启动（$(date --iso-8601=seconds)）"

# Read OS information without lsb_release. VERSION_CODENAME is present on
# supported Ubuntu releases; lsb_release is used only as a fallback.

configure_domestic_sources() {
    local mirror="$MIRROR_BASE"; local ports="$MIRROR_PORTS"; local suite="$UBUNTU_CODENAME"
    local component='main restricted universe multiverse'
    local base
    if [[ "$ARCH" == amd64 ]]; then base="$mirror"; else base="$ports"; fi
    if (( DRY_RUN )); then
        say "将配置国内 Ubuntu 镜像: $base ($suite/$ARCH)"
        return 0
    fi
    local backup=/var/lib/ubuntu-autoconfig/source-backups/$(date +%Y%m%d-%H%M%S)-$$
    as_root install -d -m 0755 "$backup" /etc/apt/sources.list.d
    if [ -f /etc/apt/sources.list ]; then
        as_root cp -a /etc/apt/sources.list "$backup/sources.list"
    fi
    # Keep audited application repositories across Ubuntu mirror changes.
    # Back up unrelated sources before changing the Ubuntu archive.
    local source_file
    shopt -s nullglob
    for source_file in /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
        case "$(basename "$source_file")" in
            ros2.list|typora.list|ubuntu-autoconfig-chromium.list)
                if network_tool audit-apt "$source_file" >/dev/null 2>&1; then continue; fi ;;
        esac
        as_root mv "$source_file" "$backup/$(basename "$source_file").disabled"
    done
    shopt -u nullglob
    as_root tee /etc/apt/sources.list >/dev/null <<LIST
# Managed by ubuntu-autoconfig; official Ubuntu packages via domestic mirror.
deb [arch=$ARCH] $base $suite $component
deb [arch=$ARCH] $base $suite-updates $component
deb [arch=$ARCH] $base $suite-security $component
deb [arch=$ARCH] $base $suite-backports $component
LIST
    if [[ "$ARCH" == amd64 ]] && dpkg --print-foreign-architectures | grep -Fxq i386; then
        configure_ubuntu_i386_source
    fi
    say "已备份旧 APT 源到 $backup，并切换到国内镜像。"
}

# Convert JSON values to a simple, shell-safe key=value stream. Legacy top-level
# names are accepted so an existing config does not unexpectedly enable nothing.
read_config() {
    local output
    output=$(python3 - "$CONFIG_FILE" "${FEATURES[@]}" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding='utf-8') as f:
    data = json.load(f)
features = data.get('features', {})
legacy = {
 'SouGouPinYin':'language_input', 'WindowsTimeSync':'time_sync',
 'NvidiaToolkit':'gpu_driver', 'CUDA':'cuda', 'RosHumble':'ros',
 'Rosdep':'rosdep', 'Clash':'v2rayn', 'GoogleChrome':'chrome',
 'V2rayN':'v2rayn', 'V2RayN':'v2rayn', 'WeChat':'wechat',
 'Steam':'steam', 'Edge':'edge',
 'VSCode':'vscode', 'QQ':'qq', 'Typora':'typora', 'Anaconda':'anaconda',
 'SparkStore':'spark_store', 'HikCamera':'hikcamera'
}
for key in sys.argv[2:]:
    value = features.get(key)
    if value is None and key == 'chrome':
        value = features.get('chromium')
    if value is None:
        for old, mapped in legacy.items():
            if mapped == key and old in data:
                value = data[old]
                break
    if value is None:
        value = 'N' if key not in ('base_tools','language_input') else 'Y'
    print(f"{key}={'1' if str(value).lower() in ('y','yes','true','1','on') else '0'}")
print(f"__interface__={data.get('interface','gui')}")
print(f"__reboot__={'1' if data.get('reboot', False) else '0'}")
method = data.get('input_method', 'auto')
if method not in ('auto', 'ibus', 'sogou'):
    raise SystemExit('input_method 必须是 auto、ibus 或 sogou。')
print(f"__input_method__={method}")
PY
)
    while IFS='=' read -r key value; do
        if [[ "$key" == __interface__ ]]; then
            [[ "$INTERFACE" == auto ]] && INTERFACE=$value
        elif [[ "$key" == __reboot__ ]]; then
            [[ "$value" == 1 && "$NO_REBOOT_EXPLICIT" == 0 ]] && NO_REBOOT=0
        elif [[ "$key" == __input_method__ ]]; then
            [[ -n "$INPUT_METHOD" ]] || INPUT_METHOD=$value
        else
            ENABLED[$key]=$value
        fi
    done <<< "$output"
    return 0
}

declare -A ENABLED=()
select_features() {
    local feature
    read_config
    if [[ -n "$ONLY_FEATURES" ]]; then
        for feature in "${FEATURES[@]}"; do ENABLED[$feature]=0; done
        local choices=() selected_feature
        IFS=',' read -ra choices <<< "$ONLY_FEATURES"
        for selected_feature in "${choices[@]}"; do
            [[ "$selected_feature" == sources ]] && continue
            [[ -v FEATURE_LABEL[$selected_feature] ]] || die "未知功能: $selected_feature"
            ENABLED[$selected_feature]=1
        done
    fi
    [[ "$INTERFACE" == auto ]] && INTERFACE=gui
    if [[ "$INTERFACE" != gui ]]; then return 0; fi
    if [[ ! -t 0 ]]; then
        if (( DRY_RUN )); then
            say "无交互终端，按配置预演；正式运行默认显示 GUI 菜单。"
            return 0
        fi
        die "GUI 模式需要交互终端；请在终端运行，或显式传入 --cli。"
    fi
    if ! command -v whiptail >/dev/null 2>&1; then
        say "安装最小图形化选择依赖 whiptail"
        ensure_domestic_sources
        apt_install whiptail
    fi
    if ! command -v whiptail >/dev/null 2>&1; then
        die "whiptail 不可用，无法显示 GUI 菜单。"
    fi
    refresh_inventory
    local args=() selected=() choice
    for feature in "${FEATURES[@]}"; do
        if [[ "${DETECTED[$feature]:-}" == disabled ]]; then
            warn "菜单禁用 $feature: ${DETECTION_REASON[$feature]}"
            continue
        fi
        local status=OFF detected_label
        [[ "${ENABLED[$feature]:-0}" == 1 ]] && status=ON
        case "${DETECTED[$feature]}" in
            ready) detected_label=已就绪 ;;
            missing) detected_label=未安装 ;;
            configure) detected_label=待配置 ;;
            *) detected_label=目标机检测 ;;
        esac
        args+=("$feature" "${FEATURE_LABEL[$feature]} [$detected_label]" "$status")
    done
    # A fixed 100-column dialog is wider than many serial consoles and small
    # desktop terminals. whiptail centers oversized dialogs, which can clip
    # the first character of every tag. Derive geometry from the active TTY.
    local columns lines dialog_width dialog_height list_height
    columns=$(tput cols 2>/dev/null || printf '%s' "${COLUMNS:-80}")
    lines=$(tput lines 2>/dev/null || printf '%s' "${LINES:-24}")
    [[ "$columns" =~ ^[0-9]+$ ]] || columns=80
    [[ "$lines" =~ ^[0-9]+$ ]] || lines=24
    (( columns < 24 )) && columns=24
    (( lines < 14 )) && lines=14
    dialog_width=$((columns - 2))
    dialog_height=$((lines - 2))
    list_height=$((dialog_height - 8))
    (( list_height < 4 )) && list_height=4
    local result
    if result=$(whiptail --output-fd 3 --title 'Ubuntu 自动配置' --checklist \
        '↑↓移动，空格切换，Tab选择按钮，回车确认' "$dialog_height" "$dialog_width" "$list_height" \
        "${args[@]}" 3>&1 1>&"$UI_FD" 2>&"$UI_FD"); then
        for feature in "${FEATURES[@]}"; do ENABLED[$feature]=0; done
        read -ra selected <<< "${result//\"/}"
        for choice in "${selected[@]}"; do ENABLED[$choice]=1; done
    else
        say "已取消功能选择。"
        summary_event selection cancelled '用户取消菜单'
        finish_run cancelled
        exit 0
    fi
}

install_system_upgrade() {
    apt_update
    say "升级已安装软件；保留其他软件的依赖，版本升级不能保证自动降级回滚。"
    (( DRY_RUN )) && return 0
    apt_get upgrade -y
}

ensure_domestic_sources() {
    [[ -n ${RUN_DIR:-} ]] || begin_run
    if [[ "$ONLY_FEATURES" != sources || "$REINSTALL" == 0 ]] && /usr/bin/python3 "$SCRIPT_DIR/scripts/lib/preflight.py" --arch "$ARCH" --suite "$UBUNTU_CODENAME" --sources-ready; then
        say "国内 APT 源已就绪，保留现有镜像。"
        summary_event sources existing '国内源与当前架构匹配'
        return 0
    fi
    if ! select_domestic_mirror; then
        summary_event sources failed '国内镜像预检失败；未安装任何软件' 1
        finish_run failed
        die "国内镜像预检失败，安装状态和下载断点已保留。"
    fi
    FORCE=1
    run_step sources '国内 APT 源' configure_domestic_sources
    FORCE=0
    if (( LAST_STEP_RC == 0 )); then
        if (( DRY_RUN )); then summary_event sources planned '将配置匹配目标架构的国内源'
        else summary_event sources configured '国内镜像源配置'; fi
    else summary_event sources failed '国内镜像源配置失败' "$LAST_STEP_RC"; fi
    if (( LAST_STEP_RC != 0 )); then finish_run failed; exit 1; fi
}

main() {
    detect_system
    local module feature needed=0
    for module in "$SCRIPT_DIR"/scripts/modules/*.sh; do source "$module"; done
    select_features
    refresh_inventory
    if (( CLEAN_REINSTALL )) && [[ "$ARCH" != amd64 ]]; then
        say "卸载后重装仍受当前架构支持范围约束。"
    fi
    if (( BACKGROUND )); then
        if (( DRY_RUN )); then
            say "将把所选功能交给 systemd 后台服务运行"
        else
            local chosen="$RUNS_DIR/background-config.json"
            /usr/bin/python3 - "$chosen" "${INPUT_METHOD:-auto}" "${!ENABLED[@]}" -- "${ENABLED[@]}" <<'PYCONFIG'
import json,sys
split=sys.argv.index('--')
features=dict(zip(sys.argv[3:split],sys.argv[split+1:]))
with open(sys.argv[1],'w') as stream:
    json.dump({'interface':'cli','reboot':False,'input_method':sys.argv[2],'features':features},stream)
PYCONFIG
            flock -u "$LOCK_FD"
            local options=(--input-method "${INPUT_METHOD:-auto}")
            if (( REINSTALL )); then options+=(--reinstall); fi
            if (( CLEAN_REINSTALL )); then options+=(--clean-reinstall --only "$ONLY_FEATURES"); fi
            systemd-run --unit=ubuntu-autoconfig-install --collect --property=Type=exec \
                --property=KillMode=control-group --setenv="SUDO_USER=${SUDO_USER:-root}" \
                /bin/bash "$SCRIPT_DIR/install.sh" --cli --config "$chosen" --no-reboot "${options[@]}"
            summary_event background background 'systemd 服务 ubuntu-autoconfig-install'
            say "已转后台。查看 ./manage.sh status 或 ./manage.sh logs。"
            return 0
        fi
    fi
    for feature in "${FEATURES[@]}"; do
        [[ "${ENABLED[$feature]:-0}" == 1 ]] || continue
        if [[ "${DETECTED[$feature]}" != ready && "${DETECTED[$feature]}" != disabled ]] || (( REINSTALL || CLEAN_REINSTALL )); then needed=1; fi
    done
    [[ "$ONLY_FEATURES" != sources ]] || needed=1
    if (( ! DRY_RUN )) && ! network_tool audit-apt >/dev/null 2>&1; then needed=1; fi
    if (( ! needed && RESET_STATE && ! DRY_RUN )); then
        rm -f "$STATE_DIR/state" "$STATE_DIR/failed-step"
        say "已清除断点标记；实际已就绪的软件仍不会重复安装。"
    fi
    if (( needed )); then
        ensure_domestic_sources
        if (( RESET_STATE && ! DRY_RUN )); then rm -f "$STATE_DIR/state" "$STATE_DIR/failed-step"; fi
        migrate_state
        FORCE=1
        if (( ! DRY_RUN )); then
            run_step package_recovery '恢复中断的包配置' repair_pending_packages
            if (( LAST_STEP_RC != 0 )); then
                summary_event package_recovery failed '请检查 dpkg 未完成配置' "$LAST_STEP_RC"
                finish_run failed
                exit 1
            fi
            summary_event package_recovery checked '中断包状态检查/恢复已通过'
            if [[ "$ONLY_FEATURES" == sources ]]; then
                run_step apt_index '更新国内 APT 索引' apt_update
                if (( LAST_STEP_RC != 0 )); then
                    summary_event apt_index failed '国内索引更新失败' "$LAST_STEP_RC"
                    finish_run failed
                    exit 1
                fi
                summary_event apt_index configured '国内 APT 索引更新完成'
            fi
        fi
        FORCE=0
    fi
    for feature in "${FEATURES[@]}"; do
        [[ "${ENABLED[$feature]:-0}" == 1 ]] || continue
        run_feature "$feature"
    done
    if ((${#FAILURES[@]})); then finish_run failed; exit 1; fi
    [[ -z ${RUN_DIR:-} ]] || finish_run complete
    say "本次处理完成；已安装且配置完整的项目没有重复安装。"
    if (( needed && ! NO_REBOOT && ! DRY_RUN )); then
        say "配置要求重启，30 秒后重启；Ctrl+C 可取消。"
        sleep 30
        as_root reboot
    fi
    return 0
}

main "$@"
