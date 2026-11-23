#!/usr/bin/env python3
"""Record changes and conservatively roll back one installer run."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile
import time

import tensorrt

ROOT = Path(__file__).resolve().parents[2]
STATE = Path('/var/lib/ubuntu-autoconfig')
NVIDIA_REPO_PATHS = [tensorrt.SOURCE, tensorrt.PREFERENCES,
                     *dict.fromkeys(tensorrt.keyring(suite) for suite in tensorrt.RELEASES)]
PATHS = {
    'sources': ['/etc/apt/sources.list', '/etc/apt/sources.list.d'],
    'language_input': ['/etc/default/locale', '/etc/default/keyboard', '/etc/console-setup',
                       '/etc/locale.conf', '/etc/profile.d/fcitx5.sh',
                       '/etc/ubuntu-autoconfig/input-method', '/etc/ubuntu-autoconfig/input-fallback',
                       '/usr/share/im-config/data/22_fcitx.conf', '/usr/share/im-config/data/22_fcitx.rc'],
    'time_sync': ['/etc/chrony/chrony.conf', '/etc/adjtime', '/etc/localtime', '/etc/timezone'],
    'ros': ['/etc/apt/sources.list.d/ros2.list', '/usr/share/keyrings/ros-archive-keyring.gpg', '/etc/profile.d/ros.sh'],
    'rosdep': ['/etc/apt/sources.list.d/ros2.list', '/usr/share/keyrings/ros-archive-keyring.gpg', '/etc/ros/rosdep/sources.list.d', '/etc/profile.d/rosdep.sh'],
    'steam': ['/etc/apt/sources.list', '/etc/apt/sources.list.d/ubuntu-i386.list'],
    'chrome': ['/etc/default/google-chrome', '/etc/apt/sources.list.d/google-chrome.list',
               '/etc/apt/sources.list.d/google-chrome.sources', '/usr/share/keyrings/google-chrome.gpg',
               '/etc/apt/trusted.gpg.d/google-chrome.gpg'],
    'edge': ['/etc/apt/sources.list.d/microsoft-edge.sources', '/etc/apt/sources.list.d/microsoft-edge.list',
             '/usr/share/keyrings/microsoft-edge.gpg', '/etc/apt/trusted.gpg.d/microsoft-edge.gpg', '/etc/default/microsoft-edge'],
    'vscode': ['/etc/apt/sources.list.d/vscode.sources', '/etc/apt/sources.list.d/vscode.list',
               '/usr/share/keyrings/microsoft.gpg', '/etc/apt/trusted.gpg.d/microsoft.gpg'],
    'typora': ['/etc/apt/sources.list.d/typora.list', '/usr/share/keyrings/typora-archive-keyring.gpg'],
    'tensorrt': NVIDIA_REPO_PATHS,
    'cuda': NVIDIA_REPO_PATHS,
    'anaconda': ['/etc/conda/.condarc', '/etc/profile.d/anaconda.sh'],
    'v2rayn': ['/opt/v2rayN', '/usr/share/applications/v2rayn.desktop'],
    'spark_store': ['/etc/apt/sources.list.d/sparkstore.list', '/etc/apt/trusted.gpg.d/spark-store.gpg'],
    'hikcamera': ['/opt/MVS', '/usr/share/applications/MVS.desktop',
                  '/etc/udev/rules.d/70-ubuntu-autoconfig-mvs.rules'],
    'clion': ['/opt/clion', '/usr/share/applications/jetbrains-clion.desktop'],
}


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with temp.open('w') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def fingerprint(path):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return None
    digest = hashlib.sha256()
    entries = [path, *sorted(path.rglob('*'))] if path.is_dir() and not path.is_symlink() else [path]
    for entry in entries:
        stat = entry.lstat()
        digest.update(str(entry.relative_to(path)).encode())
        digest.update(f'{stat.st_mode}:{stat.st_uid}:{stat.st_gid}'.encode())
        if entry.is_symlink():
            digest.update(os.readlink(entry).encode())
        elif entry.is_file():
            with entry.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
    return digest.hexdigest()


def packages():
    result = subprocess.check_output(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${db:Status-Status}\n'], text=True)
    return {name: version for name, version, status in (row.split('\t') for row in result.splitlines()) if status == 'installed'}


def query(command):
    result = subprocess.run(command, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def user_settings_command(user, *arguments):
    uid = pwd.getpwnam(user).pw_uid
    return ['sudo', '-H', '-u', user, 'env', f'XDG_RUNTIME_DIR=/run/user/{uid}',
            f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus', 'gsettings', *arguments]


def language_snapshot(user):
    uid = pwd.getpwnam(user).pw_uid
    result = {}
    for name in ('Language', 'FormatsLocale'):
        value = query(['busctl', '--system', 'get-property', 'org.freedesktop.Accounts',
                       f'/org/freedesktop/Accounts/User{uid}', 'org.freedesktop.Accounts.User', name])
        try:
            result[name] = json.loads(value[2:]) if value and value.startswith('s ') else None
        except ValueError:
            result[name] = None
    result['Region'] = query(user_settings_command(user, 'get', 'org.gnome.system.locale', 'region'))
    for schema, key in (
        ('org.gnome.desktop.input-sources', 'sources'),
        ('org.gnome.desktop.input-sources', 'mru-sources'),
        ('org.gnome.desktop.input-sources', 'per-window'),
        ('org.gnome.desktop.wm.keybindings', 'switch-input-source'),
        ('org.gnome.desktop.interface', 'gtk-im-module'),
        ('org.freedesktop.ibus.general', 'preload-engines'),
        ('org.freedesktop.ibus.general', 'engines-order'),
        ('org.freedesktop.ibus.general', 'enable-by-default'),
        ('org.freedesktop.ibus.general', 'use-global-engine'),
        ('org.freedesktop.ibus.general.hotkey', 'triggers'),
    ):
        result['setting|' + schema + '|' + key] = query(user_settings_command(user, 'get', schema, key))
    return result


def copy_path(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.copying')
    if temporary.is_dir() and not temporary.is_symlink(): shutil.rmtree(temporary)
    else: temporary.unlink(missing_ok=True)
    subprocess.run(['cp', '-a', '--', str(source), str(temporary)], check=True)
    if destination.is_dir() and not destination.is_symlink(): shutil.rmtree(destination)
    else: destination.unlink(missing_ok=True)
    os.replace(temporary, destination)


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.file = self.path / 'manifest.json'
        self.data = json.loads(self.file.read_text()) if self.file.exists() else {}

    def save(self):
        atomic_json(self.file, self.data)

    def begin(self, user):
        self.path.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.data = dict(version=1, user=user, started=time.time(), status='running',
                         packages_before=packages(), files={}, steps={}, active=None,
                         architectures_before=query(['dpkg', '--print-foreign-architectures']).split(),
                         timezone=query(['timedatectl', 'show', '-p', 'Timezone', '--value']),
                         local_rtc=query(['timedatectl', 'show', '-p', 'LocalRTC', '--value']),
                         chrony_active=query(['systemctl', 'is-active', 'chrony']),
                         chrony_enabled=query(['systemctl', 'is-enabled', 'chrony']))
        self.save()

    def capture(self, path):
        path = Path(path)
        if str(path) in self.data['files']:
            return
        # Parent directories already captured cover their descendants.
        if any(Path(name) in path.parents for name in self.data['files']):
            return
        original = fingerprint(path)
        backup = str(len(self.data['files']))
        if original is not None:
            copy_path(path, self.path / 'files' / backup)
        self.data['files'][str(path)] = dict(before=original, backup=backup, after=original)
        self.save()

    def prepare(self, feature):
        touched = []
        for name in PATHS.get(feature, []):
            self.capture(name)
            touched.append(name)
        if feature == 'language_input':
            home = Path(pwd.getpwnam(self.data['user']).pw_dir)
            before = self.data.setdefault('language_before', {})
            for key, value in language_snapshot(self.data['user']).items():
                before.setdefault(key, value)
            for suffix in ('.xinputrc', '.pam_environment', '.dmrc', '.config/fcitx/profile', '.config/fcitx/config',
                           '.config/systemd/user/ubuntu-autoconfig-english-input.service',
                           '.config/systemd/user/gnome-session-pre.target.wants/ubuntu-autoconfig-english-input.service',
                           '.config/autostart/ubuntu-autoconfig-fcitx.desktop',
                           '.config/autostart/sogoupinyin-service.desktop', '.config/autostart/sogoupinyin-watchdog.desktop',
                           '.config/environment.d/90-ubuntu-autoconfig-input.conf',
                           '.config/user-dirs.dirs', '.config/user-dirs.locale', '.config/user-dirs.conf',
                           '.config/autostart/user-dirs-update-gtk.desktop'):
                self.capture(home / suffix)
                touched.append(str(home / suffix))
        if feature == 'anaconda' and (self.data.get('version_update') or not Path('/opt/anaconda3').exists()):
            self.capture('/opt/anaconda3')
            touched.append('/opt/anaconda3')
        self.data['active'] = feature
        self.data['steps'][feature] = 'running'
        # A crash before checkpoint must not authorize deleting later user edits.
        self.data['active_paths'] = [name for name in self.data['files']
                                     if any(name == item or Path(name) in Path(item).parents for item in touched)]
        for name in self.data['active_paths']:
            record = self.data['files'][name]
            if not record.get('uncertain') and fingerprint(name) != record.get('after'):
                record['externally_modified'] = True
            record['uncertain'] = True
        self.save()

    def checkpoint(self, feature, result):
        self.data['steps'][feature] = result
        self.data['packages_after'] = packages()
        if feature == 'time_sync':
            self.data['runtime_after'] = dict(
                timezone=query(['timedatectl', 'show', '-p', 'Timezone', '--value']),
                local_rtc=query(['timedatectl', 'show', '-p', 'LocalRTC', '--value']),
                chrony_active=query(['systemctl', 'is-active', 'chrony']),
                chrony_enabled=query(['systemctl', 'is-enabled', 'chrony']))
        if feature == 'language_input':
            self.data['language_after'] = language_snapshot(self.data['user'])
        for name in self.data.get('active_paths', self.data['files']):
            record = self.data['files'][name]
            record.update(after=fingerprint(name), uncertain=record.get('externally_modified', False))
        self.data['active'] = None
        self.save()

    def finish(self, result):
        self.data['status'] = result
        self.data['finished'] = time.time()
        self.save()

    def plan(self, current=None):
        current = packages() if current is None else current
        before = self.data['packages_before']
        after = self.data.get('packages_after', before)
        added = sorted(name for name in after if name not in before and current.get(name) == after[name])
        removed = [f'{name}={version}' for name, version in before.items() if name not in after and name not in current]
        changed = sorted(name for name in before if name in after and before[name] != after[name])
        conflicts, restore = [], []
        for name, record in self.data['files'].items():
            present = fingerprint(name)
            if present == record['before']:
                continue
            if record.get('uncertain') or present != record.get('after'):
                conflicts.append(name)
            else:
                restore.append(name)
        runtime_conflicts = []
        if 'language_after' in self.data:
            current_language = language_snapshot(self.data['user'])
            runtime_conflicts.extend('language.' + key for key, value in self.data['language_after'].items()
                                     if current_language.get(key) != value)
        for key, command in {
            'timezone': ['timedatectl', 'show', '-p', 'Timezone', '--value'],
            'local_rtc': ['timedatectl', 'show', '-p', 'LocalRTC', '--value'],
            'chrony_active': ['systemctl', 'is-active', 'chrony'],
            'chrony_enabled': ['systemctl', 'is-enabled', 'chrony'],
        }.items():
            if key in self.data.get('runtime_after', {}) and query(command) != self.data['runtime_after'][key]:
                runtime_conflicts.append(key)
        return dict(remove_packages=added, restore_packages=removed, restore_files=restore,
                    preserve_modified_files=conflicts, upgraded_packages_not_downgraded=changed,
                    preserve_runtime_changes=runtime_conflicts, incomplete_step=self.data.get('active'))

    def rollback(self, execute=False):
        plan = self.plan()
        print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
        if not execute:
            return
        if self.data['status'] == 'rolled-back':
            return
        added, removed = plan['remove_packages'], plan['restore_packages']
        for item in added + removed:
            if not re.fullmatch(r'[a-z0-9][a-z0-9+.:~-]*(?:=[^\s]+)?', item):
                raise ValueError(f'非法软件包记录: {item}')
        if added or removed:
            args = removed + [name + '-' for name in added]
            common = ROOT / 'scripts/lib/common.sh'
            prefix = ['bash', '-c', 'set -Eeuo pipefail; source "$1"; shift; apt_get "$@"', '_', str(common)]
            simulation = subprocess.run(prefix + ['-s', 'install', '--no-install-recommends', *args], capture_output=True, text=True)
            if simulation.returncode:
                raise RuntimeError('回滚依赖解析失败，未删除软件包:\n' + simulation.stdout + simulation.stderr)
            removals = re.findall(r'^Remv (\S+)', simulation.stdout, re.M)
            permitted = {name.split(':')[0] for name in added}
            if any(name.split(':')[0] not in permitted for name in removals):
                raise RuntimeError('回滚会删除其他软件依赖，已停止。请检查 --dry-run 输出。')
            subprocess.run(prefix + ['install', '-y', '--no-install-recommends', *args], check=True)
        for name in sorted(plan['restore_files'], key=lambda x: len(Path(x).parts), reverse=True):
            target, record = Path(name), self.data['files'][name]
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            if record['before'] is not None:
                copy_path(self.path / 'files' / record['backup'], target)
        if 'language_before' in self.data:
            user = self.data['user']
            home = pwd.getpwnam(user).pw_dir
            changed_user_files = any(name.startswith(home + '/') for name in plan['preserve_modified_files'])
            if not changed_user_files:
                for key, value in self.data['language_before'].items():
                    if value is None or value == self.data.get('language_after', {}).get(key) or 'language.' + key in plan['preserve_runtime_changes']:
                        continue
                    if key.startswith('setting|'):
                        _, schema, setting = key.split('|', 2)
                        subprocess.run(user_settings_command(user, 'set', schema, setting, value), check=True)
                    elif key == 'Region':
                        subprocess.run(user_settings_command(user, 'set', 'org.gnome.system.locale', 'region', value), check=True)
                    else:
                        uid = pwd.getpwnam(user).pw_uid
                        subprocess.run(['busctl', '--system', 'call', 'org.freedesktop.Accounts',
                                        f'/org/freedesktop/Accounts/User{uid}', 'org.freedesktop.Accounts.User',
                                        'Set' + key, 's', value], check=True)
        if 'time_sync' in self.data['steps'] and not any(name.startswith('/etc/chrony') for name in plan['preserve_modified_files']):
            for key, command in [('timezone', 'set-timezone'), ('local_rtc', 'set-local-rtc')]:
                if key in plan['preserve_runtime_changes'] or any(name in plan['preserve_modified_files'] for name in ('/etc/localtime', '/etc/timezone', '/etc/adjtime')):
                    continue
                value = self.data.get(key)
                if value is not None:
                    if key == 'local_rtc': value = '1' if value == 'yes' else '0'
                    subprocess.run(['timedatectl', command, value], check=True)
            if shutil.which('chronyc') and not any(key.startswith('chrony_') for key in plan['preserve_runtime_changes']):
                enabled = self.data.get('chrony_enabled')
                if enabled in ('enabled', 'disabled'):
                    subprocess.run(['systemctl', 'enable' if enabled == 'enabled' else 'disable', 'chrony'], check=True)
                subprocess.run(['systemctl', 'restart' if self.data.get('chrony_active') == 'active' else 'stop', 'chrony'], check=True)
        if {'steam', 'spark_store'}.intersection(self.data['steps']) and 'i386' not in self.data.get('architectures_before', []):
            architectures = query(['dpkg', '--print-foreign-architectures']) or ''
            if 'i386' in architectures.split() and not any(name.endswith(':i386') for name in packages()):
                subprocess.run(['dpkg', '--remove-architecture', 'i386'], check=True)
        if '/etc/fstab' in plan['restore_files']:
            subprocess.run(['systemctl', 'daemon-reload'], check=True)
        if '/etc/udisks2/mount_options.conf' in plan['restore_files'] and shutil.which('udisksctl'):
            subprocess.run(['systemctl', 'restart', 'udisks2'], check=True)
        if plan['preserve_modified_files'] or plan['preserve_runtime_changes'] or plan['incomplete_step'] or plan['upgraded_packages_not_downgraded']:
            self.data['status'] = 'rollback-partial'
        else:
            self.data['status'] = 'rolled-back'
        self.data['rollback_plan'] = plan
        self.save()
        # Mark affected features retryable. Never restore old false-success flags.
        state = STATE / 'state'
        if state.exists():
            affected = set(self.data['steps'])
            state.write_text(''.join(line + '\n' for line in state.read_text().splitlines() if line not in affected))
        if self.data['status'] != 'rolled-back':
            raise RuntimeError('回滚完成可安全恢复的部分；存在后来修改/中断未确认的配置，已保留并记录。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['begin', 'prepare', 'checkpoint', 'finish', 'rollback', 'status'])
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--user', default='root')
    parser.add_argument('--feature')
    parser.add_argument('--result', default='complete')
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('需要 root 读取/写入系统回滚记录。')
    journal = Journal(args.run)
    if args.action == 'begin': journal.begin(args.user)
    elif args.action == 'prepare': journal.prepare(args.feature)
    elif args.action == 'checkpoint': journal.checkpoint(args.feature, args.result)
    elif args.action == 'finish': journal.finish(args.result)
    elif args.action == 'rollback':
        if journal.data.get('version_update'):
            raise SystemExit('这是版本更新记录，请用 manage.sh rollback-version --run 指定该记录。')
        journal.rollback(args.yes)
    else: print(json.dumps(journal.data, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
