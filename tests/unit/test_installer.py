"""Offline regressions for failures seen in log.txt; never install packages."""
import json
import os
from pathlib import Path
import subprocess
from run_files import temporary_directory
import unittest

ROOT = Path(__file__).resolve().parents[2]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory(prefix='autoconfig-test-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.env = dict(os.environ, TEST_ROOT=str(ROOT), TEST_WORK=str(self.work))
        self.setup = r'''
set -Eeuo pipefail
source "$TEST_ROOT/scripts/lib/common.sh"
source "$TEST_ROOT/scripts/lib/state.sh"
source "$TEST_ROOT/scripts/modules/official_apps.sh"
source "$TEST_ROOT/scripts/modules/ros.sh"
source "$TEST_ROOT/scripts/modules/v2rayn.sh"
source "$TEST_ROOT/scripts/modules/language_input.sh"
source "$TEST_ROOT/scripts/modules/time_sync.sh"
STATE_DIR=$TEST_WORK
SUCCESS_LOG=$STATE_DIR/success.log
ERROR_LOG=$STATE_DIR/error.log
ARCH=amd64
INPUT_METHOD=sogou
UBUNTU_CODENAME=resolute
DRY_RUN=0
FORCE=0
FAILURES=()
SKIPPED=()
CURRENT_STEP=
export TMPDIR=$TEST_WORK
'''

    def shell(self, script, expected=0):
        result = subprocess.run(['bash', '-c', self.setup + script], env=self.env,
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_failure_stops_function_and_next_step_runs(self):
        self.shell(r'''
trap 'echo unexpected >> "$TEST_WORK/trap"' ERR
bad() { false; touch "$TEST_WORK/should-not-run"; }
good() { :; }
run_step bad bad bad
run_step good good good
[[ ${FAILURES[*]} == bad ]]
[[ $(cat "$STATE_DIR/state") == good ]]
[[ $(cat "$STATE_DIR/failed-step") == bad ]]
[[ ! -e "$TEST_WORK/should-not-run" && ! -e "$TEST_WORK/trap" ]]
''')

    def test_recorded_failure_keeps_exit_status_without_a_second_error(self):
        result = self.shell(r'''
source "$TEST_ROOT/scripts/lib/planning.sh"
SCRIPT_DIR=$TEST_ROOT
CONFIG_FILE=$TEST_ROOT/config.json
LOG_DIR=$TEST_WORK
LOG_FILE=$LOG_DIR/install.log
SUMMARY_EVENTS=$LOG_DIR/events.jsonl
declare -A FEATURE_LABEL=([spark_store]='星火应用商店')
trap 'echo "unexpected ERR" >&2' ERR
trap summary_on_exit EXIT
summary_event spark_store failed '下载失败' 1
exit 1
''', expected=1)
        self.assertNotIn('unexpected ERR', result.stderr)
        self.assertIn('--only spark_store', result.stdout)
        report = json.loads((self.work / 'install.summary.json').read_text())
        self.assertEqual(report['exit_code'], 1)
        self.assertEqual(report['counts'], {'failed': 1})

    def test_failure_in_command_substitution_is_not_masked(self):
        self.shell(r'''
bad() { value=$(false; echo hidden); touch "$TEST_WORK/should-not-run"; }
run_step bad bad bad
[[ ${FAILURES[*]} == bad && ! -e "$TEST_WORK/should-not-run" ]]
''')

    def test_failure_in_pipeline_is_not_masked(self):
        self.shell(r'''
bad() { false | cat; touch "$TEST_WORK/should-not-run"; }
run_step bad bad bad
[[ ${FAILURES[*]} == bad && ! -e "$TEST_WORK/should-not-run" ]]
''')

    def test_skip_is_not_success(self):
        self.shell(r'''
skip() { return "$SKIP_STATUS"; }
run_step unavailable unavailable skip
[[ ${SKIPPED[*]} == unavailable && ${#FAILURES[@]} == 0 ]]
[[ ! -e "$STATE_DIR/state" && ! -e "$SUCCESS_LOG" ]]
''')

    def test_dry_run_does_not_create_checkpoints(self):
        self.shell(r'''
DRY_RUN=1
good() { :; }
run_step good good good
[[ ! -e "$STATE_DIR/state" && ! -e "$SUCCESS_LOG" ]]
''')

    def test_state_migration_preserves_healthy_steps_and_runs_once(self):
        self.shell(r'''
printf '%s\n' sources base_tools ros rosdep qq wechat v2rayn anaconda edge > "$STATE_DIR/state"
migrate_state
[[ $(cat "$STATE_DIR/state") == $'base_tools\nros' ]]
printf 'qq\n' >> "$STATE_DIR/state"
migrate_state
state_has qq
[[ $(cat "$STATE_DIR/state-version") == 4 ]]
compgen -G "$STATE_DIR/state.before-v4-*" >/dev/null
''')

    def test_failed_download_never_invokes_apt_and_cleans_temporary_files(self):
        self.shell(r'''
download_from_mirrors() { printf partial > "$1"; return 22; }
apt_install() { touch "$TEST_WORK/apt-called"; }
install_test() { install_downloaded_deb https://example.invalid/qq.deb; }
run_step qq qq install_test
[[ ${FAILURES[*]} == qq && ! -e "$TEST_WORK/apt-called" ]]
[[ -z $(find "$TEST_WORK" -name package.deb) ]]
''')

    def test_html_download_is_rejected_before_apt(self):
        self.shell(r'''
download_from_mirrors() { printf '<html>error</html>' > "$1"; }
apt_install() { touch "$TEST_WORK/apt-called"; }
install_test() { install_downloaded_deb https://example.invalid/qq.deb; }
run_step qq qq install_test
[[ ${FAILURES[*]} == qq && ! -e "$TEST_WORK/apt-called" ]]
''')

    def test_apt_update_failure_does_not_install_or_set_cache(self):
        self.shell(r'''
as_root() { return 42; }
run_step apt apt apt_update
[[ ${FAILURES[*]} == apt && $APT_UPDATED == 0 ]]
''')

    def test_chrome_install_uses_stable_package_and_keeps_google_feed_disabled(self):
        self.shell(r'''
as_root() {
    [[ $1 == tee && $2 == /etc/default/google-chrome ]]
    cat > "$TEST_WORK/google-chrome"
}
has_pkg() { [[ $1 == chromium ]]; }
install_vendor_deb() {
    grep -Fxq 'repo_add_once="false"' "$TEST_WORK/google-chrome"
    printf '%s\n' "$*" > "$TEST_WORK/installed"
    printf '%s\n' 'repo_add_once="true"' > "$TEST_WORK/google-chrome"
}
archive_foreign_app_sources() { printf '%s\n' "$@" > "$TEST_WORK/sources"; }
run_step chrome chrome install_chrome
state_has chrome
grep -Fxq google-chrome-stable "$TEST_WORK/installed"
grep -Fxq 'repo_add_once="false"' "$TEST_WORK/google-chrome"
grep -Fxq 'repo_reenable_on_distupgrade="false"' "$TEST_WORK/google-chrome"
grep -Fxq /etc/apt/sources.list.d/google-chrome.sources "$TEST_WORK/sources"
''')

    def test_chrome_reuses_existing_package_while_disabling_foreign_repo(self):
        self.shell(r'''
has_pkg() { [[ $1 == google-chrome-stable ]]; }
configure_chrome_updates() { touch "$TEST_WORK/google-chrome"; }
archive_foreign_app_sources() { :; }
install_vendor_deb() { return 99; }
run_step chrome chrome install_chrome
state_has chrome
[[ -f "$TEST_WORK/google-chrome" ]]
''')

    def test_steam_enables_i386_source_before_install(self):
        self.shell(r'''
as_root() {
    case $1 in
        dpkg) printf '%s\n' "$*" >> "$TEST_WORK/actions" ;;
        install|rm) : ;;
        tee) cat > "$TEST_WORK/i386.list" ;;
        *) return 99 ;;
    esac
}
apt_install() {
    grep -Fxq 'dpkg --add-architecture i386' "$TEST_WORK/actions"
    [[ $(grep -c '^deb ' "$TEST_WORK/i386.list") == 4 ]]
    grep -Fq 'deb [arch=amd64,i386] https://mirrors.tuna.tsinghua.edu.cn/ubuntu resolute multiverse' <(sed 's/main restricted universe //' "$TEST_WORK/i386.list")
    [[ $APT_UPDATED == 0 && $1 == steam-installer ]]
}
APT_UPDATED=1
run_step steam steam install_steam
[[ ${#FAILURES[@]} == 0 ]]
state_has steam
''')

    def test_rosdep_update_failure_is_recorded(self):
        self.shell(r'''
apt_install() { :; }
configure_ros_source() { :; }
as_root() { if [[ $1 == tee ]]; then cat >> "$TEST_WORK/writes"; fi; }
as_user() { shift; "$@"; }
rosdep() { return 31; }
env() { while [[ $1 != rosdep ]]; do shift; done; "$@"; }
export SUDO_USER=installer-test
run_step rosdep rosdep install_rosdep
[[ ${FAILURES[*]} == rosdep ]]
grep -Fq 'https://mirrors.tuna.tsinghua.edu.cn/rosdistro/rosdep/base.yaml' "$TEST_WORK/writes"
! grep -q '/master/' "$TEST_WORK/writes"
''')

    def test_v2rayn_http_error_is_failure(self):
        self.shell(r'''
apt_install() { :; }
v2rayn_existing_binary() { return 1; }
V2RAYN_DOMESTIC_URLS=https://mirrors.nju.edu.cn/releases/v2rayN-linux-64.zip
download_from_mirrors() { printf '%s\n' "${@:2}" > "$TEST_WORK/url"; return 22; }
run_step v2rayn v2rayn install_v2rayn
[[ ${FAILURES[*]} == v2rayn ]]
grep -Fq '/releases/v2rayN-linux-64.zip' "$TEST_WORK/url"
! grep -q 'api.github.com' "$TEST_WORK/url"
''')

    def test_v2rayn_reuses_existing_install_without_download(self):
        self.shell(r'''
v2rayn_existing_binary() { return 0; }
v2rayn_desktop_entry() { touch "$TEST_WORK/desktop"; }
download_from_mirrors() { return 99; }
run_step v2rayn v2rayn install_v2rayn
state_has v2rayn
[[ -e "$TEST_WORK/desktop" && ${#FAILURES[@]} == 0 ]]
''')

    def test_v2rayn_uses_vendor_package_install_path(self):
        self.shell(r'''
v2rayn_existing_binary() { return 1; }
unset V2RAYN_DOMESTIC_URLS
download_from_mirrors() { return 99; }
install_vendor_deb() {
    printf '%s\n' "$1" > "$TEST_WORK/selected"
}
run_step v2rayn v2rayn install_v2rayn
state_has v2rayn
grep -Fxq v2rayn "$TEST_WORK/selected"
''')

    def test_failed_repository_update_restores_previous_source(self):
        self.shell(r'''
printf 'old source\n' > "$TEST_WORK/app.list"
as_root() { "$@"; }
apt_update() {
    grep -Fxq 'new source' "$TEST_WORK/app.list"
    return 100
}
if apt_configure_source "$TEST_WORK/app.list" <<< 'new source'; then exit 99; fi
grep -Fxq 'old source' "$TEST_WORK/app.list"
''')

    def test_sogou_metadata_provides_both_official_https_cdn_urls(self):
        data = {'data': {'downloadConfig': json.dumps({'linux': [
            {'name': 'x86_64', 'link': 'https://ime-sec.gtimg.com/pc/sogoupinyin.deb'}]})}}
        (self.work / 'sogou.html').write_text('<script>window.IFE = ' + json.dumps(data) + ';</script>')
        self.shell(r'''urls=$(sogou_download_urls "$TEST_WORK/sogou.html")
[[ "$urls" == *https://ime-sec.gtimg.com/pc/sogoupinyin.deb* ]]
[[ "$urls" == *https://ime.gtimg.com/pc/sogoupinyin.deb* ]]
''')

    def test_fcitx4_profile_for_new_im_config(self):
        self.shell(r'''
as_root() { "$@"; }
ensure_fcitx4_im_config "$TEST_WORK/im-config"
IM_CONFIG_PHASE=1
source "$TEST_WORK/im-config/22_fcitx.rc"
[[ $GTK_IM_MODULE == fcitx && $QT_IM_MODULE == fcitx && $XMODIFIERS == @im=fcitx ]]
grep -Fq 'package_status fcitx' "$TEST_WORK/im-config/22_fcitx.conf"
printf '# preserved\n' >> "$TEST_WORK/im-config/22_fcitx.conf"
ensure_fcitx4_im_config "$TEST_WORK/im-config"
grep -Fxq '# preserved' "$TEST_WORK/im-config/22_fcitx.conf"
''')

    def test_sogou_download_failure_keeps_existing_input_method(self):
        self.shell(r'''
has_pkg() { return 1; }
tmp_download() { return 22; }
apt_install() { touch "$TEST_WORK/apt-called"; }
run_step language_input language_input install_language_input
[[ ${FAILURES[*]} == language_input && ! -e "$TEST_WORK/apt-called" ]]
''')

    def test_time_sync_checks_ntp_and_sets_windows_local_rtc(self):
        self.shell(r'''
apt_install() { :; }
as_root() {
    printf '%s\n' "$*" >> "$TEST_WORK/actions"
    if [[ $1 == tee ]]; then cat > "$TEST_WORK/chrony.conf"; fi
}
timedatectl() { printf yes; }
tail() { printf LOCAL; }
run_step time_sync time_sync install_time_sync
state_has time_sync
grep -Fxq 'timedatectl set-local-rtc 1' "$TEST_WORK/actions"
grep -Fxq 'chronyc waitsync 15 0.5 0 2' "$TEST_WORK/actions"
grep -Fq 'server ntp.aliyun.com iburst' "$TEST_WORK/chrony.conf"
! grep -q 'ntp.ubuntu.com' "$TEST_WORK/chrony.conf"
''')

    def test_failed_time_sync_is_not_recorded_as_success(self):
        self.shell(r'''
apt_install() { :; }
as_root() {
    if [[ $1 == tee ]]; then cat >/dev/null; fi
    if [[ $1 == chronyc && $2 == waitsync ]]; then return 1; fi
}
run_step time_sync time_sync install_time_sync
[[ ${FAILURES[*]} == time_sync ]]
[[ ! -e "$STATE_DIR/state" ]]
''')

    def test_all_features_cli_dry_run(self):
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--dry-run', '--cli',
                                 '--config', str(ROOT / 'configs/all.json')],
                                env=dict(self.env, XDG_STATE_HOME=str(self.work / 'state')),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('安装结果总结', result.stdout)
        runs = self.work / 'state/ubuntu-autoconfig/runs'
        reports = list((runs / 'logs').glob('*.summary.json'))
        self.assertEqual(len(reports), 1)
        self.assertFalse(list(runs.glob('tmp.*')))
        report = json.loads(reports[0].read_text())
        recorded = {row['feature'] for row in report['steps']}
        self.assertTrue(set(json.loads((ROOT / 'configs/all.json').read_text())['features']).issubset(recorded))
        self.assertTrue(report['dry_run'])

    def test_architecture_rejections_are_explicit_skips(self):
        self.shell(r'''
ARCH=armhf
DRY_RUN=1
source "$TEST_ROOT/scripts/modules/gpu_driver.sh"
source "$TEST_ROOT/scripts/modules/cuda.sh"
for feature in qq wechat steam language_input ros cuda gpu_driver anaconda v2rayn; do
    run_step "$feature" "$feature" "install_$feature"
done
[[ ${#SKIPPED[@]} == 9 && ${#FAILURES[@]} == 0 ]]
''')

    def test_help_never_requests_privileges(self):
        binary = self.work / 'bin'
        binary.mkdir()
        sudo = binary / 'sudo'
        sudo.write_text('#!/bin/sh\ntouch "$TEST_WORK/sudo-called"\nexit 99\n')
        sudo.chmod(0o755)
        env = dict(self.env, PATH=str(binary) + ':' + os.environ['PATH'])
        for name in ('install.sh', 'manage.sh', 'uninstall.sh'):
            result = subprocess.run(['bash', str(ROOT / name), '--help'], env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.work / 'sudo-called').exists())

    def test_package_recovery_runs_again_despite_previous_success(self):
        self.shell(r'''
printf 'package_recovery\n' > "$STATE_DIR/state"
recover() { touch "$TEST_WORK/recovery-executed"; }
run_step package_recovery recovery recover
[[ -e "$TEST_WORK/recovery-executed" ]]
''')

    def test_auto_input_prefers_an_installed_native_engine(self):
        self.shell(r'''
INPUT_METHOD=auto
apt_update() { :; }
has_pkg() { [[ $1 == ibus-rime ]]; }
apt_has_candidate() { return 1; }
install_native_input() { printf '%s' "$1" > "$TEST_WORK/provider"; }
install_sogou_input() { touch "$TEST_WORK/sogou"; }
as_root() { if [[ $1 == tee ]]; then cat >/dev/null; fi; }
install_language_input
[[ $(cat "$TEST_WORK/provider") == ibus-rime && ! -e "$TEST_WORK/sogou" ]]
''')

    def test_auto_input_falls_back_only_when_native_engine_unavailable(self):
        self.shell(r'''
INPUT_METHOD=auto
apt_update() { :; }
has_pkg() { return 1; }
apt_has_candidate() { return 1; }
install_sogou_input() { touch "$TEST_WORK/sogou"; }
as_root() { if [[ $1 == tee ]]; then cat >/dev/null; fi; }
install_language_input
[[ -e "$TEST_WORK/sogou" ]]
''')

    def test_native_download_error_never_triggers_framework_replacement(self):
        self.shell(r'''
INPUT_METHOD=auto
apt_update() { :; }
has_pkg() { return 1; }
apt_has_candidate() { [[ $1 == ibus-libpinyin ]]; }
install_native_input() { return 42; }
install_sogou_input() { touch "$TEST_WORK/sogou"; }
as_root() { if [[ $1 == tee ]]; then cat >/dev/null; fi; }
run_step language_input native install_language_input
[[ ${FAILURES[*]} == language_input && ! -e "$TEST_WORK/sogou" ]]
''')

    def test_confirmed_native_runtime_unavailability_allows_auto_fallback(self):
        self.shell(r'''
INPUT_METHOD=auto
apt_update() { :; }
native_chinese_engine() { printf ibus-libpinyin; }
install_native_input() { return 21; }
install_sogou_input() { touch "$TEST_WORK/sogou"; }
as_root() { if [[ $1 == tee ]]; then cat >/dev/null; fi; }
install_language_input
[[ -e "$TEST_WORK/sogou" ]]
''')

    def test_state_upgrade_only_retries_input_method_on_v3(self):
        self.shell(r'''
printf 'base_tools\nqq\nlanguage_input\n' > "$STATE_DIR/state"
printf '3\n' > "$STATE_DIR/state-version"
migrate_state
[[ $(cat "$STATE_DIR/state") == $'base_tools\nqq' ]]
[[ $(cat "$STATE_DIR/state-version") == 4 ]]
''')

    def test_all_shell_files_parse(self):
        for script in [*ROOT.glob('*.sh'), *sorted((ROOT / 'scripts').rglob('*.sh')), *sorted((ROOT / 'tests').rglob('*.sh'))]:
            result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f'{script}: {result.stderr}')

    def test_repair_cli_dry_run_can_be_repeated(self):
        env = dict(self.env, XDG_STATE_HOME=str(self.work / 'state'))
        for _ in range(2):
            result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--dry-run', '--cli',
                                     '--config', str(ROOT / 'configs/repair.json')],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('安装结果总结', result.stdout)
            self.assertIn('已存在，跳过', result.stdout)
        self.assertFalse((self.work / 'state/ubuntu-autoconfig/runs/state').exists())

    def test_run_temporary_directory_used_by_shell_and_python(self):
        self.shell(r'''
runs="$TEST_WORK/runs"
mkdir -p "$runs"
setup_run_temp "$runs"
[[ $TMPDIR == "$runs"/tmp.* && $TMP == "$TMPDIR" && $TEMP == "$TMPDIR" ]]
[[ $(mktemp) == "$TMPDIR/"* ]]
python3 -c 'import os, tempfile; assert tempfile.gettempdir() == os.environ["TMPDIR"]'
cleanup_run_temp
[[ ! -e "$RUN_TEMP_DIR" ]]
''')


if __name__ == '__main__':
    unittest.main(verbosity=2)
