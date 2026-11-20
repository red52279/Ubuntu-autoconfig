#!/usr/bin/env bash
# Root journal, package recovery, and graceful interruption hooks.
RUN_DIR=${RUN_DIR:-}
STEP_PID=${STEP_PID:-}

journal_event() {
    (( DRY_RUN )) && return 0
    [[ -n ${RUN_DIR:-} ]] || return 0
    python3 "$SCRIPT_DIR/scripts/lib/journal.py" "$1" --run "$RUN_DIR" "${@:2}"
}

begin_run() {
    (( DRY_RUN )) && return 0
    local user=${SUDO_USER:-root}
    if [[ -z ${SUDO_USER:-} && -n ${PKEXEC_UID:-} ]]; then
        user=$(getent passwd "$PKEXEC_UID" | cut -d: -f1)
        export SUDO_USER=$user
    fi
    # Failed/interrupted invocations keep their original baseline for rollback.
    if [[ -s "$STATE_DIR/open-run" ]]; then
        local previous
        previous=$(cat "$STATE_DIR/open-run")
        if [[ "$previous" =~ ^[0-9]{8}-[0-9]{6}-[0-9]+$ && -f "$STATE_DIR/runs/$previous/manifest.json" ]]; then
            RUN_DIR="$STATE_DIR/runs/$previous"
            say "继续已有安装记录: $previous"
            return 0
        fi
    fi
    local id
    id="$(date +%Y%m%d-%H%M%S)-$$"
    RUN_DIR="$STATE_DIR/runs/$id"
    python3 "$SCRIPT_DIR/scripts/lib/journal.py" begin --run "$RUN_DIR" --user "$user"
    printf '%s\n' "$id" > "$STATE_DIR/open-run"
    printf '%s\n' "$id" > "$STATE_DIR/latest-run"
}

finish_run() {
    (( DRY_RUN )) && return 0
    [[ -n ${RUN_DIR:-} ]] || return 0
    journal_event finish --result "$1"
    if [[ $1 == complete || $1 == cancelled ]]; then rm -f "$STATE_DIR/open-run"; fi
}

interrupt_run() {
    local signal=$1
    trap '' INT TERM HUP
    warn "收到 $signal，正在停止当前步骤并保留下载/安装断点。"
    if [[ -n ${STEP_PID:-} ]]; then
        kill -TERM -- "-$STEP_PID" 2>/dev/null || true
        # A stopped (SIGTTIN/SIGTTOU) job must be continued to process TERM.
        kill -CONT -- "-$STEP_PID" 2>/dev/null || true
        wait "$STEP_PID" 2>/dev/null || true
    fi
    if (( ! DRY_RUN )) && [[ -n ${CURRENT_STEP:-} ]]; then
        printf '%s\n' "$CURRENT_STEP" > "$STATE_DIR/failed-step"
        journal_event checkpoint --feature "$CURRENT_STEP" --result interrupted || true
    fi
    if [[ -n ${CURRENT_STEP:-} ]] && declare -F summary_event >/dev/null; then
        summary_event "$CURRENT_STEP" interrupted "收到 $signal" 130 || true
        CURRENT_STEP=
    fi
    finish_run interrupted || true
    exit 130
}

repair_pending_packages() {
    (( DRY_RUN )) && return 0
    local audit
    audit=$(dpkg --audit)
    if [[ -n "$audit" ]]; then
        say "发现中断留下的 dpkg 状态，先继续配置已解包的软件。"
        as_root env DEBIAN_FRONTEND=noninteractive dpkg --configure -a
        apt_get -f install -y
    fi
}
