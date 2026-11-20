#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
STATE=/var/lib/ubuntu-autoconfig
RUNS_DIR=$STATE/runs
ACTION=${1:-status}
(($# == 0)) || shift
usage() {
    printf '用法: ./manage.sh {status|logs|summary [--json]|retry [--background]|stop|rollback [--run ID] [--yes|--dry-run]|cleanup}\n'
}
if [[ "$ACTION" == --help || "$ACTION" == -h ]]; then usage; exit 0; fi
for argument in "$@"; do
    if [[ "$argument" == --help || "$argument" == -h ]]; then usage; exit 0; fi
done
case "$ACTION" in
    summary)
        summary_file=/var/log/ubuntu-autoconfig/latest-summary.txt
        [[ "${1:-}" != --json ]] || summary_file=/var/log/ubuntu-autoconfig/latest-summary.json
        [[ -r "$summary_file" ]] || { printf '尚无本版本生成的安装总结。\n'; exit 1; }
        exec cat "$summary_file" ;;
    status)
        running=0
        for unit in ubuntu-autoconfig-install; do
            if systemctl is-active --quiet "$unit.service"; then
                systemctl --no-pager --full status "$unit.service" || true
                running=1
            fi
        done
        (( running )) || printf '当前没有后台安装/测试任务。\n'
        if [[ -f "$STATE/state" ]]; then printf '\n完成步骤记录:\n'; cat "$STATE/state"; fi
        if [[ -f "$STATE/failed-step" ]]; then printf '\n待重试步骤: '; cat "$STATE/failed-step"; fi
        if [[ -f "$STATE/latest-run" ]]; then printf '\n最近回滚记录: '; cat "$STATE/latest-run"; fi
        exit 0 ;;
    logs) exec tail -n 100 -F /var/log/ubuntu-autoconfig/latest.log ;;
    retry)
        config="$RUNS_DIR/background-config.json"
        [[ -f "$config" ]] || config="$STATE/background-config.json"
        [[ -f "$config" ]] || config="$ROOT_DIR/configs/repair.json"
        exec bash "$ROOT_DIR/install.sh" --cli --config "$config" --no-reboot "$@" ;;
    stop)
        if (( EUID != 0 )); then exec sudo bash "$0" stop; fi
        for unit in ubuntu-autoconfig-install; do
            if systemctl is-active --quiet "$unit.service"; then systemctl stop "$unit.service"; fi
        done
        printf '后台安装/测试任务已停止。\n'
        exit 0 ;;
    rollback|uninstall|cleanup) ;;
    *) usage; exit 2 ;;
esac
if (( EUID != 0 )); then exec sudo bash "$0" "$ACTION" "$@"; fi
mkdir -p "$STATE"
exec 9>"$STATE/install.lock"
flock -n 9 || { printf '安装任务仍在运行，请先 ./manage.sh stop。\n' >&2; exit 1; }
if [[ "$ACTION" == cleanup ]]; then
    python3 - "$RUNS_DIR/cache" "$STATE/cache" <<'PY'
from pathlib import Path
import shutil, sys
paths = [Path(item) for item in sys.argv[1:]]
if any(path.is_symlink() for path in paths):
    raise SystemExit('拒绝清理符号链接缓存目录。')
for path in paths:
    if path.exists(): shutil.rmtree(path)
print('下载缓存已清理；回滚备份和运行日志保留。')
PY
    exit 0
fi
RUN_ID=
EXECUTE=0
PREVIEW=0
while (($#)); do
    case "$1" in
        --run) RUN_ID=${2:?需要运行 ID}; shift 2 ;;
        --yes) EXECUTE=1; shift ;;
        --dry-run) PREVIEW=1; shift ;;
        *) printf '未知回滚选项: %s\n' "$1" >&2; exit 2 ;;
    esac
done
if [[ -z "$RUN_ID" && -s "$STATE/latest-run" ]]; then RUN_ID=$(cat "$STATE/latest-run"); fi
[[ "$RUN_ID" =~ ^[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || { printf '没有可用的本版本安装记录；不会猜测删除历史软件。\n' >&2; exit 1; }
RUN_DIR="$RUNS_DIR/$RUN_ID"
[[ -f "$RUN_DIR/manifest.json" ]] || { printf '找不到运行记录。\n' >&2; exit 1; }
source "$ROOT_DIR/scripts/lib/common.sh"
RUN_TEMP_DIR=
trap cleanup_run_temp EXIT
setup_run_temp "$RUNS_DIR"
python3 "$ROOT_DIR/scripts/lib/journal.py" rollback --run "$RUN_DIR"
(( PREVIEW )) && exit 0
if (( ! EXECUTE )); then
    if [[ -t 0 ]]; then
        read -r -p '按以上计划卸载本次新增软件并恢复未被再次修改的配置？输入 yes: ' answer
        [[ "$answer" == yes ]] || exit 0
    else
        printf '以上仅为预览。执行一键回滚请加 --yes。\n'
        exit 0
    fi
fi
python3 "$ROOT_DIR/scripts/lib/journal.py" rollback --run "$RUN_DIR" --yes
if [[ -f "$STATE/open-run" && $(cat "$STATE/open-run") == "$RUN_ID" ]]; then rm -f "$STATE/open-run"; fi
printf '回滚完成。备份记录仍保留在 %s\n' "$RUN_DIR"
