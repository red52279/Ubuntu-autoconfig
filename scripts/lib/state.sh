#!/usr/bin/env bash
# Only source from the main installer. Run steps as ordinary commands, never
# in an if/!/&&/|| context: Bash disables errexit throughout such function calls.

state_has() { [ -f "$STATE_DIR/state" ] && grep -Fxq "$1" "$STATE_DIR/state"; }
mark_state() {
    (( DRY_RUN )) && return 0
    if ! state_has "$1"; then printf '%s\n' "$1" >> "$STATE_DIR/state"; fi
}

migrate_state() {
    (( DRY_RUN )) && return 0
    if [[ ! -f "$STATE_DIR/state-version" ]] || [[ $(cat "$STATE_DIR/state-version") != 4 ]]; then
        if [[ -f "$STATE_DIR/state" ]]; then
            cp -a "$STATE_DIR/state" "$STATE_DIR/state.before-v4-$(date +%Y%m%d-%H%M%S)"
            # These old steps could have been falsely marked successful, or
            # need their source configuration repaired. Keep healthy steps.
            if [[ -f "$STATE_DIR/state-version" && $(cat "$STATE_DIR/state-version") == 3 ]]; then
                sed -i '/^language_input$/d' "$STATE_DIR/state"
            else
                sed -i -E '/^(sources|language_input|time_sync|rosdep|v2rayn|chromium|steam|qq|wechat|anaconda|edge|vscode|spark_store|hikcamera)$/d' "$STATE_DIR/state"
            fi
            say "已保留旧断点备份，并清除受本次修复影响的完成标记。"
        fi
        printf '4\n' > "$STATE_DIR/state-version"
    fi
}

run_step() {
    local step=$1 label=$2 function=$3 rc saved_err_trap
    if (( ! DRY_RUN && ! FORCE )) && [[ "$step" != package_recovery ]] && state_has "$step"; then
        say "跳过已完成步骤: $label"
        return 0
    fi
    CURRENT_STEP=$step
    LAST_STEP_RC=0
    say "开始: $label"
    if declare -F journal_event >/dev/null; then
        journal_event prepare --feature "$step"
    fi
    saved_err_trap=$(trap -p ERR)
    trap - ERR
    set +e
    # A separate job process group lets the parent stop apt/curl descendants
    # on TERM/HUP, rather than leaving an orphan installer holding the lock.
    set -m
    (
        set -Eeuo pipefail
        trap '' TTIN TTOU
        shopt -s inherit_errexit
        "$function"
    ) </dev/null &
    STEP_PID=$!
    set +m
    wait "$STEP_PID"
    rc=$?
    LAST_STEP_RC=$rc
    STEP_PID=
    set -e
    if [[ -n "$saved_err_trap" ]]; then eval "$saved_err_trap"; fi
    if declare -F journal_event >/dev/null; then
        journal_event checkpoint --feature "$step" --result "$rc"
    fi
    if (( rc == 0 )); then
        if (( DRY_RUN )); then
            say "预演完成（未安装、未写入完成状态）: $label"
        else
            mark_state "$step"
            if [[ -f "$STATE_DIR/failed-step" ]] && [[ $(cat "$STATE_DIR/failed-step") == "$step" ]]; then
                rm -f "$STATE_DIR/failed-step"
            fi
            printf '[%s] SUCCESS step=%s label=%s\n' "$(date --iso-8601=seconds)" "$step" "$label" >> "$SUCCESS_LOG"
            say "完成: $label"
        fi
    elif (( rc == SKIP_STATUS )); then
        SKIPPED+=("$step")
        say "跳过（未写入完成状态）: $label"
    else
        if (( ! DRY_RUN )); then
            printf '%s\n' "$step" > "$STATE_DIR/failed-step"
            printf '[%s] ERROR step=%s rc=%s label=%s\n' "$(date --iso-8601=seconds)" "$step" "$rc" "$label" >> "$ERROR_LOG"
        fi
        warn "失败: $label（退出码 $rc，下次运行会重试）"
        FAILURES+=("$step")
    fi
    CURRENT_STEP=
    return 0
}
