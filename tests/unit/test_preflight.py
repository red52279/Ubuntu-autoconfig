import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from run_files import temporary_directory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / 'scripts/lib'
sys.path.insert(0,str(LIB))
from catalog import FEATURES, support
from preflight import Inventory, binary_arch
from qt_packages import qt_packages_from_index
from summary import render


class ArchitectureTests(unittest.TestCase):
    def test_arm64_enables_native_apps_and_disables_x86_paths(self):
        for name in ('base_tools','language_input','storage_tools','ros','qq','anaconda','chrome'):
            self.assertTrue(support(name,'arm64','resolute')[0],name)
        for name in ('steam','wechat','virtualization_tools','gpu_driver','cuda','edge'):
            self.assertFalse(support(name,'arm64','resolute')[0],name)

    def test_armhf_disables_vendor_binaries_but_keeps_native_input(self):
        for name in ('qq','wechat','typora','anaconda','ros','rosdep','steam','cuda','chrome'):
            self.assertFalse(support(name,'armhf','jammy')[0],name)
        self.assertTrue(support('language_input','armhf','jammy','auto')[0])
        self.assertFalse(support('language_input','armhf','jammy','sogou')[0])

    def test_lts_release_constraints_are_explicit(self):
        for suite in ('focal','jammy','noble','resolute'):
            self.assertTrue(support('ros','arm64',suite)[0])
        self.assertFalse(support('ros','arm64','plucky')[0])
        self.assertFalse(support('desktop_tools','amd64','focal')[0])

    def test_vendor_binary_architecture_is_checked_from_elf(self):
        with temporary_directory() as tmp:
            path=Path(tmp)/'binary'
            for code,name in ((62,'amd64'),(183,'arm64'),(40,'armhf')):
                header=bytearray(20);header[:4]=b'\x7fELF';header[5]=1;header[18:20]=code.to_bytes(2,'little')
                path.write_bytes(header)
                self.assertEqual(binary_arch(path),name)
            path.write_text('<html>not an executable</html>')
            self.assertIsNone(binary_arch(path))

    def test_spark_and_mvs_only_offer_supported_architectures(self):
        for name in ('spark_store', 'hikcamera'):
            for arch in ('amd64', 'arm64'):
                self.assertTrue(support(name, arch, 'resolute')[0])
            self.assertFalse(support(name, 'armhf', 'resolute')[0])


class InventoryTests(unittest.TestCase):
    def inventory(self, packages, arch='amd64'):
        with patch('preflight.output',return_value='amd64'):
            return Inventory(arch=arch,suite='resolute',packages=packages)

    def test_installed_software_is_ready_without_state_file(self):
        inventory=self.inventory({'linuxqq':'3.2.35'})
        result=inventory.inspect('qq')
        self.assertEqual(result['status'],'ready')

    def test_missing_package_is_not_hidden_by_any_checkpoint(self):
        inventory=self.inventory({})
        self.assertEqual(inventory.inspect('qq')['status'],'missing')

    def test_qualified_native_package_names_count_as_installed(self):
        inventory=self.inventory({'wine:amd64':'10'})
        self.assertEqual(inventory.inspect('virtualization_tools')['status'],'ready')

    def test_qt_index_selects_only_official_dev_extensions_and_base_tools(self):
        index = """Package: qt6-base-dev
Source: qt6-base (6.10.2-1)
Architecture: amd64
Homepage: https://www.qt.io/developers/

Package: qt6-base-dev-dbgsym
Source: qt6-base
Architecture: amd64
Homepage: https://www.qt.io/developers/

Package: qt6-base-doc
Source: qt6-base
Architecture: all
Homepage: https://www.qt.io/developers/

Package: qt6-base-doc-dev
Source: qt6-base
Architecture: all
Homepage: https://www.qt.io/developers/

Package: qml6-module-qtquick
Source: qt6-declarative
Architecture: amd64
Homepage: https://www.qt.io/developers/

Package: qt6-webengine-dev
Source: qt6-webengine
Architecture: arm64
Homepage: https://www.qt.io/developers/

Package: qt6-style-kvantum-dev
Architecture: amd64
Homepage: https://github.com/tsujan/Kvantum

Package: qtcreator
Architecture: amd64

Package: qt6-tools-dev-tools
Source: qt6-tools
Architecture: amd64
Homepage: https://www.qt.io/developers/

Package: libpyside6-dev
Source: pyside6
Architecture: amd64
Homepage: https://wiki.qt.io/Qt_for_Python"""
        self.assertEqual(qt_packages_from_index(index.splitlines(keepends=True), 'amd64'),
                         ['libpyside6-dev', 'qt6-base-dev', 'qt6-base-doc-dev', 'qt6-tools-dev-tools', 'qtcreator'])

    def test_qt_base_installation_needs_missing_extensions(self):
        inventory = self.inventory({'qt6-base-dev': '6.10.2', 'qt6-tools-dev-tools': '6.10.2',
                                    'qtcreator': '18.0.2'})
        available = [*inventory.packages, 'qt6-multimedia-dev']
        with patch('preflight.available_qt_packages', return_value=available):
            result = inventory.inspect('desktop_tools')
            self.assertEqual(result['status'], 'missing')
            self.assertIn('qt6-multimedia-dev', result['reason'])
            inventory.packages['qt6-multimedia-dev'] = '6.10.2'
            self.assertEqual(inventory.inspect('desktop_tools')['status'], 'ready')

    def test_cross_arch_check_does_not_reuse_host_installed_binaries(self):
        inventory=self.inventory({'linuxqq':'3.2.35'},arch='arm64')
        self.assertEqual(inventory.inspect('qq')['status'],'unknown')
        self.assertEqual(inventory.inspect('cuda')['status'],'disabled')

    def test_package_exists_but_configuration_missing_requires_repair(self):
        packages={'chrony':'4'}
        inventory=self.inventory(packages)
        with patch.object(inventory,'configuration',return_value=(False,'RTC 未配置')):
            self.assertEqual(inventory.inspect('time_sync')['status'],'configure')

    def test_chrome_inventory_checks_the_package_and_update_settings(self):
        inventory = self.inventory({'chromium': '156.0'})
        self.assertEqual(inventory.inspect('chrome')['status'], 'missing')
        inventory.packages['google-chrome-stable'] = '155.0'
        with patch('preflight.read', return_value='repo_add_once="true"'):
            self.assertEqual(inventory.inspect('chrome')['status'], 'configure')
        disabled_updates = 'repo_add_once="false"\nrepo_reenable_on_distupgrade="false"\n'
        with patch('preflight.read', return_value=disabled_updates):
            self.assertEqual(inventory.inspect('chrome')['status'], 'ready')
        with patch('preflight.read', return_value=disabled_updates + 'repo_add_once="true"\n'):
            self.assertEqual(inventory.inspect('chrome')['status'], 'configure')

    def test_microsoft_apps_offer_domestic_mirror_on_supported_platform(self):
        inventory=self.inventory({})
        with temporary_directory() as tmp, patch('preflight.ROOT', Path(tmp)):
            self.assertEqual(inventory.inspect('edge')['status'], 'missing')
            self.assertEqual(inventory.inspect('vscode')['status'], 'missing')
            inventory.suite = 'jammy'
            self.assertEqual(inventory.inspect('edge')['status'], 'disabled')
            self.assertEqual(inventory.inspect('vscode')['status'], 'disabled')
            folder = Path(tmp) / 'packages.local'
            folder.mkdir()
            (folder / 'code_amd64.deb').touch()
            self.assertEqual(inventory.inspect('vscode')['status'], 'missing')


class SummaryTests(unittest.TestCase):
    def test_summary_counts_and_retry_command_follow_actual_results(self):
        steps=[dict(feature='qq',label='QQ',status='existing',reason='已安装',exit_code=0),
               dict(feature='wechat',label='微信',status='failed',reason='下载失败',exit_code=1)]
        report,text=render(steps,1,'/tmp/test.log')
        self.assertEqual(report['counts'],{'existing':1,'failed':1})
        self.assertIn('--only wechat',report['retry_command'])
        self.assertIn('有失败项目',text)
        self.assertNotIn('新安装 2',text)

    def test_interrupted_run_has_persistent_exit_code(self):
        report,text=render([dict(feature='qq',label='QQ',status='interrupted',reason='SIGTERM',exit_code=130)],130,'x.log')
        self.assertEqual(report['exit_code'],130)
        self.assertIn('已中断',text)

    def test_failed_prerequisite_reports_unstarted_features_and_valid_retry(self):
        report,text=render([dict(feature='package_recovery',label='包恢复',status='failed',reason='dpkg interrupted',exit_code=1)],
                           1,'run.log',config='configs/repair.json',selected=['qq','wechat'])
        self.assertEqual(report['counts']['not_started'],2)
        self.assertNotIn('--only package_recovery',report['retry_command'])
        self.assertIn('--config configs/repair.json',report['retry_command'])


class CliPlatformTests(unittest.TestCase):
    def command(self,*args):
        with temporary_directory() as state:
            return subprocess.run(['bash',str(ROOT/'install.sh'),*args], capture_output=True, text=True,
                                  cwd=ROOT, env={**os.environ, 'XDG_STATE_HOME': state})

    def test_legacy_chromium_cli_and_configs_select_chrome(self):
        result = self.command('--dry-run', '--cli', '--only', 'chromium', '--clean-reinstall')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Google Chrome 官方稳定版', result.stdout)
        with temporary_directory() as directory:
            config = Path(directory) / 'legacy.json'
            config.write_text(json.dumps({'features': {'chromium': 'Y', 'base_tools': 'N', 'language_input': 'N'}}))
            result = self.command('--dry-run', '--cli', '--config', str(config))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Google Chrome 官方稳定版', result.stdout)

    def test_explicit_unsupported_arm_request_fails_before_install(self):
        result=self.command('--dry-run','--cli','--arch','arm64','--only','steam')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('已禁用',result.stderr)
        self.assertNotIn('日志:',result.stdout)

    def test_target_architecture_cannot_override_real_install(self):
        result=self.command('--arch','arm64','--cli')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('实际安装自动检测',result.stderr)

    def test_clean_reinstall_requires_explicit_safe_app(self):
        for arguments in [('--clean-reinstall','--dry-run'),('--clean-reinstall','--only','base_tools','--dry-run')]:
            result=self.command(*arguments)
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn('日志:',result.stdout)

    def test_arm_list_reports_disabled_features_without_privileges(self):
        result=self.command('--list','--arch','arm64')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('steam',result.stdout)
        self.assertIn('已禁用',result.stdout)

    def test_only_accepts_catalog_feature_with_a_digit(self):
        result = self.command('--dry-run', '--cli', '--only', 'v2rayn')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('v2rayN', result.stdout)


class PackageSafetyTests(unittest.TestCase):
    def shell(self,body):
        with temporary_directory() as tmp:
            env={**os.environ,'WORK':tmp,'ROOT':str(ROOT)}
            result=subprocess.run(['bash','-c','set -Eeuo pipefail\nsource "$ROOT/scripts/lib/common.sh"\n'+body],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_already_installed_packages_do_not_call_apt_or_network(self):
        self.shell('''
has_pkg() { return 0; }
apt_update() { touch "$WORK/network"; }
apt_get() { touch "$WORK/apt"; }
apt_install git curl
[[ ! -e "$WORK/network" && ! -e "$WORK/apt" ]]
''')

    def test_satisfied_preflight_never_calls_installer(self):
        self.shell('''
source "$ROOT/scripts/lib/state.sh"
source "$ROOT/scripts/lib/planning.sh"
SCRIPT_DIR=$ROOT
REINSTALL=0
CLEAN_REINSTALL=0
SUMMARY_EVENTS=$WORK/events
FEATURES=(qq)
declare -A FEATURE_LABEL=([qq]=QQ)
probe_feature() { PROBE_STATUS=ready; PROBE_REASON=installed; }
run_step() { touch "$WORK/installer-called"; }
run_feature qq
[[ ! -e "$WORK/installer-called" ]]
/usr/bin/python3 -c 'import json,sys; assert json.loads(open(sys.argv[1]).read())["status"] == "existing"' "$WORK/events"
''')

    def test_success_exit_with_missing_package_never_marks_complete(self):
        self.shell('''
source "$ROOT/scripts/lib/state.sh"
source "$ROOT/scripts/lib/planning.sh"
SCRIPT_DIR=$ROOT
STATE_DIR=$WORK
SUCCESS_LOG=$WORK/success
ERROR_LOG=$WORK/errors
SUMMARY_EVENTS=$WORK/events
DRY_RUN=0
REINSTALL=0
CLEAN_REINSTALL=0
FORCE=0
FAILURES=()
SKIPPED=()
CURRENT_STEP=
declare -A FEATURE_LABEL=([qq]=QQ)
probe_feature() { PROBE_STATUS=missing; PROBE_REASON=missing; }
install_qq() { :; }
printf 'qq\n' > "$STATE_DIR/state"
run_feature qq
[[ ${FAILURES[*]} == qq ]]
! state_has qq
/usr/bin/python3 -c 'import json,sys; assert json.loads(open(sys.argv[1]).read())["status"] == "failed"' "$WORK/events"
''')

    def test_replacement_download_failure_preserves_working_application(self):
        self.shell('''
CLEAN_REINSTALL=1
CURRENT_STEP=qq
has_pkg() { return 0; }
apt_update() { :; }
apt_get() { if [[ $1 == --download-only ]]; then return 100; fi; touch "$WORK/removed"; }
set +e
( set -e; clean_reinstall_package linuxqq linuxqq )
rc=$?
set -e
[[ $rc == 100 && ! -e "$WORK/removed" ]]
''')

    def test_dependency_removal_is_rejected(self):
        self.shell('''
CLEAN_REINSTALL=1
CURRENT_STEP=qq
has_pkg() { return 0; }
apt_update() { :; }
apt_get() {
  if [[ $1 == --download-only ]]; then return 0; fi
  if [[ $1 == -s ]]; then printf 'Remv linuxqq [3]\nRemv protected-app [1]\n'; return 0; fi
  touch "$WORK/removed"
}
set +e
( set -e; clean_reinstall_package linuxqq linuxqq )
rc=$?
set -e
[[ $rc != 0 && ! -e "$WORK/removed" ]]
''')

    def test_valid_clean_reinstall_downloads_before_removing(self):
        self.shell('''
CLEAN_REINSTALL=1
CURRENT_STEP=qq
has_pkg() { return 0; }
apt_update() { :; }
apt_get() {
  if [[ $1 == --download-only ]]; then touch "$WORK/downloaded"; return 0; fi
  if [[ $1 == -s ]]; then printf 'Remv linuxqq [3]\n'; return 0; fi
  [[ -e "$WORK/downloaded" && $1 == remove ]]
  touch "$WORK/removed"
}
clean_reinstall_package linuxqq linuxqq
[[ -e "$WORK/removed" ]]
''')


if __name__=='__main__': unittest.main()
