#!/usr/bin/env bash
# Shared orchestration for inventory, safe reinstall and exit summaries.

load_catalog() {
    local rows feature label
    rows=$(/usr/bin/python3 "$SCRIPT_DIR/scripts/lib/catalog.py" shell)
    FEATURES=()
    while IFS=$'\t' read -r feature label; do
        FEATURES+=("$feature")
        FEATURE_LABEL[$feature]=$label
    done <<< "$rows"
}

probe_feature() {
    local row
    row=$(/usr/bin/python3 "$SCRIPT_DIR/scripts/lib/preflight.py" --feature "$1" \
        --arch "$ARCH" --suite "$UBUNTU_CODENAME" --input-method "${INPUT_METHOD:-auto}" \
        --user "${SUDO_USER:-$(id -un)}" --format shell)
    IFS=$'\t' read -r _ PROBE_STATUS PROBE_REASON <<< "$row"
}

refresh_inventory() {
    local rows feature status reason
    rows=$(/usr/bin/python3 "$SCRIPT_DIR/scripts/lib/preflight.py" --arch "$ARCH" \
        --suite "$UBUNTU_CODENAME" --input-method "${INPUT_METHOD:-auto}" \
        --user "${SUDO_USER:-$(id -un)}" --format shell)
    while IFS=$'\t' read -r feature status reason; do
        DETECTED[$feature]=$status
        DETECTION_REASON[$feature]=$reason
    done <<< "$rows"
}

summary_event() {
    [[ -n ${SUMMARY_EVENTS:-} ]] || return 0
    /usr/bin/python3 "$SCRIPT_DIR/scripts/lib/summary.py" event --events "$SUMMARY_EVENTS" \
        --feature "$1" --status "$2" --reason "${3:-}" --code "${4:-0}" --seconds "${5:-0}" \
        --label "${FEATURE_LABEL[$1]:-$1}"
}

summary_on_exit() {
    local code=$?
    # Report the saved status once. Returning it from an EXIT handler can
    # otherwise run the ERR trap again for an already recorded step failure.
    trap - EXIT ERR
    cleanup_run_temp || warn "无法清理本次临时目录: $RUN_TEMP_DIR"
    if [[ -n ${CURRENT_STEP:-} ]]; then
        local status=failed
        (( code == 130 || code == 143 )) && status=interrupted
        summary_event "$CURRENT_STEP" "$status" '步骤未正常结束' "$code" || true
    fi
    local options=()
    (( DRY_RUN )) && options+=(--dry-run)
    [[ -z ${RUN_DIR:-} ]] || options+=(--run "$RUN_DIR")
    options+=(--config "$CONFIG_FILE" --input-method "${INPUT_METHOD:-auto}" --elapsed "$((SECONDS-${SUMMARY_STARTED:-0}))")
    local chosen=() feature
    if declare -p ENABLED >/dev/null 2>&1; then
        for feature in "${FEATURES[@]}"; do [[ "${ENABLED[$feature]:-0}" != 1 ]] || chosen+=("$feature"); done
    fi
    options+=(--selected "$(IFS=,; printf '%s' "${chosen[*]}")")
    /usr/bin/python3 "$SCRIPT_DIR/scripts/lib/summary.py" finish --events "$SUMMARY_EVENTS" \
        --log "$LOG_FILE" --code "$code" "${options[@]}" || true
    ln -sfn "${LOG_FILE%.log}.summary.json" "$LOG_DIR/latest-summary.json"
    ln -sfn "${LOG_FILE%.log}.summary.txt" "$LOG_DIR/latest-summary.txt"
    return "$code"
}

run_feature() {
    local feature=$1 before_status result_status begin=$SECONDS
    probe_feature "$feature"
    before_status=$PROBE_STATUS
    if [[ "$before_status" == disabled ]]; then
        warn "禁用: ${FEATURE_LABEL[$feature]}（$PROBE_REASON）"
        SKIPPED+=("$feature")
        summary_event "$feature" disabled "$PROBE_REASON"
        return 0
    fi
    if [[ "$before_status" == ready ]] && (( ! REINSTALL && ! CLEAN_REINSTALL )); then
        say "已满足，跳过: ${FEATURE_LABEL[$feature]}（$PROBE_REASON）"
        summary_event "$feature" existing "$PROBE_REASON"
        return 0
    fi
    say "安装前检测: ${FEATURE_LABEL[$feature]} — $PROBE_REASON"
    if (( CLEAN_REINSTALL )); then say "将先下载替换包，检查卸载依赖，再卸载并安装；用户数据保留。"; fi
    if (( ! DRY_RUN )) && [[ -f "$STATE_DIR/state" ]]; then
        sed -i "/^${feature}$/d" "$STATE_DIR/state"
    fi
    FORCE=1
    run_step "$feature" "${FEATURE_LABEL[$feature]}" execute_and_validate_feature
    FORCE=0
    if (( LAST_STEP_RC == 0 )); then
        if (( DRY_RUN )); then result_status=planned
        elif (( CLEAN_REINSTALL )) && [[ "$before_status" != missing ]]; then result_status=reinstalled
        elif [[ "$feature" == system_upgrade ]]; then result_status=maintenance
        elif [[ "$before_status" == missing ]]; then result_status=installed
        elif [[ "$before_status" == ready ]]; then result_status=checked
        else result_status=configured; fi
    elif (( LAST_STEP_RC == SKIP_STATUS )); then result_status=disabled
    else result_status=failed; fi
    summary_event "$feature" "$result_status" "$PROBE_REASON" "$LAST_STEP_RC" "$((SECONDS-begin))"
}

detect_system() {
    [ -r /etc/os-release ] || die "找不到 /etc/os-release，无法识别系统。"
    # shellcheck disable=SC1091
    source /etc/os-release
    [ "${ID:-}" = ubuntu ] || die "此脚本只支持 Ubuntu，当前系统为 ${ID:-unknown}。"
    local major=${VERSION_ID%%.*}
    [[ "$major" =~ ^[0-9]+$ ]] || die "无法识别 Ubuntu 版本: ${VERSION_ID:-unknown}"
    (( major >= 20 && major <= 26 )) || die "支持 Ubuntu 20.04-26.04，当前为 ${VERSION_ID:-unknown}。"
    UBUNTU_CODENAME=${VERSION_CODENAME:-}
    if [ -z "$UBUNTU_CODENAME" ] && command -v lsb_release >/dev/null 2>&1; then
        UBUNTU_CODENAME=$(lsb_release -sc)
    fi
    [ -n "$UBUNTU_CODENAME" ] || die "无法获取 Ubuntu 代号（如 focal/jammy/noble）。"
    UBUNTU_CODENAME=${SUITE_OVERRIDE:-$UBUNTU_CODENAME}
    ARCH=${ARCH_OVERRIDE:-$(dpkg --print-architecture)}
    case "$ARCH" in amd64|arm64|armhf) ;; *) die "支持 amd64、arm64、armhf，当前架构为 $ARCH。" ;; esac
    export UBUNTU_CODENAME ARCH
    if [[ -n ${SUITE_OVERRIDE:-} ]]; then
        say "检测目标: Ubuntu $UBUNTU_CODENAME, 架构: $ARCH ($(architecture_name))"
    else
        say "系统: Ubuntu ${VERSION_ID} ($UBUNTU_CODENAME), 架构: $ARCH ($(architecture_name))"
    fi
}


execute_and_validate_feature() {
    local feature=$CURRENT_STEP
    "install_$feature"
    if (( ! DRY_RUN )) && [[ "$feature" != system_upgrade ]]; then
        probe_feature "$feature"
        if [[ "$PROBE_STATUS" != ready ]]; then
            warn "安装后检测未通过: ${FEATURE_LABEL[$feature]}（$PROBE_REASON）"
            return 1
        fi
    fi
}
