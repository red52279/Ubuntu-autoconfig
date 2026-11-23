#!/usr/bin/env python3
"""Check installed features, record updates and restore recorded versions."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from archive_apps import CLION_PATH, anaconda_latest, clion_archive, clion_latest, clion_version, newer
from catalog import FEATURES, ROS, support
from desktop_locale import write_file
from journal import Journal, ROOT, STATE, copy_path, fingerprint, packages
from network import check_url
from vendor_release import hik_release

DEB_APPS = {
    'chrome': 'google-chrome-stable', 'edge': 'microsoft-edge-stable', 'vscode': 'code',
    'v2rayn': 'v2rayn', 'qq': 'linuxqq', 'wechat': 'wechat', 'spark_store': 'spark-store',
    'language_input': 'sogoupinyin',
}
ARCHIVES = {'clion': CLION_PATH, 'hikcamera': Path('/opt/MVS'), 'anaconda': Path('/opt/anaconda3')}
EXTRAS = {
    'base_tools': ('apt-transport-https', 'software-properties-common', 'python3-setuptools'),
    'dev_tools': ('aravis-tools',),
    'desktop_tools': ('libqt6core6', 'libqt6core6t64', 'libqt6widgets6', 'libxcb-cursor0'),
    'media_tools': ('rar',),
    'language_input': ('ibus', 'ibus-libpinyin', 'ibus-pinyin', 'ibus-rime', 'fcitx', 'fcitx-bin',
                       'fcitx-ui-classic', 'fcitx-config-gtk', 'im-config', 'locales', 'language-pack-zh-hans',
                       'language-pack-gnome-zh-hans', 'language-pack-gnome-zh-hans-base',
                       'language-selector-gnome', 'fonts-noto-cjk'),
    'cuda': ('nvidia-cuda-dev', 'nvidia-cuda-gdb'),
    'rosdep': ('ros-dev-tools', 'python3-rosdep-modules', 'python3-rosdistro', 'python3-rosdistro-modules'),
    'steam': ('steam', 'steam-libs', 'steam-libs-i386'),
    'hikcamera': ('libxcb-xinerama0', 'libusb-1.0-0', 'libxkbcommon-x11-0', 'net-tools'),
}
INST = re.compile(r'^Inst (\S+)(?: \[[^\]]*\])? \((\S+)(?: .*?)?(?: \[(\w+)\])?\)$', re.M)


def output(command, **kwargs):
    return subprocess.check_output(command, text=True, env={**os.environ, 'LC_ALL': 'C'}, **kwargs)


def shell(body, *args, **kwargs):
    return subprocess.run(['bash', '-Eeuo', 'pipefail', '-c', body, '_', str(ROOT), *map(str, args)],
                          check=True, **kwargs)


def apt(*args, capture=False, cwd=None, timeout=None):
    environment = {**os.environ, 'LC_ALL': 'C'}
    if timeout:
        environment['APT_FETCH_TIMEOUT'] = str(timeout)
    result = shell('source "$1/scripts/lib/common.sh"; shift; apt_get "$@"', *args,
                   cwd=cwd, capture_output=capture, text=True, env=environment)
    return result.stdout


def apt_apply(args):
    shell('SCRIPT_DIR=$1; STATE_DIR=$2; source "$SCRIPT_DIR/scripts/modules/official_apps.sh"; shift 2; '
          'new_sources=(); for path in /etc/apt/sources.list.d/{google-chrome,microsoft-edge,vscode}.{sources,list}; do '
          '[[ -e "$path" || -L "$path" ]] || new_sources+=("$path"); done; '
          'trap \'archive_foreign_app_sources "${new_sources[@]}"\' EXIT; '
          'apt_get -y "$@"', STATE, *args)


def approved_source_text(text, deb822=False):
    records = re.split(r'(\r?\n[ \t]*\r?\n)', text) if deb822 else text.splitlines(keepends=True)
    selected, skipped = [], False
    for record in records:
        active = '\n'.join(line for line in record.splitlines() if not line.lstrip().startswith('#'))
        try:
            for url in re.findall(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+', active):
                check_url(url)
        except ValueError:
            skipped = True
        else:
            selected.append(record)
    return ''.join(selected), skipped


def prepare_apt_sources(work):
    sources = work / 'apt-sources'
    parts = sources / 'sources.list.d'
    parts.mkdir(parents=True)
    etc = Path('/etc/apt')
    for path in [etc / 'sources.list', *(etc / 'sources.list.d').glob('*.list'), *(etc / 'sources.list.d').glob('*.sources')]:
        if not path.is_file():
            continue
        selected, skipped = approved_source_text(path.read_text(), path.suffix == '.sources')
        if skipped:
            print(f'版本操作跳过 {path.name} 中的非白名单源', flush=True)
        target = sources / path.name if path == etc / 'sources.list' else parts / path.name
        target.write_text(selected)
    (sources / 'sources.list').touch(exist_ok=True)
    os.environ['AUTOCONFIG_APT_SOURCES_DIR'] = str(sources)


def installed_targets(feature, installed, suite):
    if feature == 'system_upgrade':
        return set(installed)
    wanted = set(FEATURES[feature]['packages']) | set(EXTRAS.get(feature, ()))
    if feature in DEB_APPS:
        wanted.add(DEB_APPS[feature])
    if feature == 'ros':
        prefix = f'ros-{ROS[suite]}-'
        return {name for name in installed if name.startswith(prefix)}
    if feature == 'gpu_driver':
        wanted.update(name.split(':')[0] for name in installed
                      if re.match(r'^(?:nvidia-|libnvidia-|linux-(?:modules|objects|signatures)-nvidia-)', name))
    return {name for name in installed if name.split(':')[0] in wanted}


def package_candidates(names):
    candidates = {}
    if names:
        name = ''
        options = []
        sources = os.environ.get('AUTOCONFIG_APT_SOURCES_DIR')
        if sources:
            options = ['-o', f'Dir::Etc::sourcelist={sources}/sources.list',
                       '-o', f'Dir::Etc::sourceparts={sources}/sources.list.d']
        for line in output(['apt-cache', *options, 'policy', *sorted(names)]).splitlines():
            if line and not line[0].isspace() and line.endswith(':'):
                name = line[:-1]
            elif line.strip().startswith('Candidate:'):
                version = line.split()[-1]
                if version != '(none)':
                    candidates[name] = version
    return candidates


def deb_metadata(path):
    line = output(['dpkg-deb', '-W', '--showformat=${Package}\t${Version}\t${Architecture}', str(path)],
                  stderr=subprocess.DEVNULL)
    return tuple(line.split('\t'))


def vendor_candidate(feature, package, arch, directory):
    target = directory / f'{package}.deb'
    commands = {
        'qq': 'url=$(qq_release_url); download_from_mirrors "$target" "$url"',
        'wechat': 'mapfile -t urls < <(wechat_release_urls); download_from_mirrors "$target" "${urls[@]}"',
        'spark_store': 'url=$(python3 "$SCRIPT_DIR/scripts/lib/vendor_release.py" spark-store "$ARCH"); download_from_mirrors "$target" "$url"',
        'language_input': 'source "$SCRIPT_DIR/scripts/modules/language_input.sh"; fetch_sogou_deb "$target"',
    }
    shell('SCRIPT_DIR=$1; source "$SCRIPT_DIR/scripts/modules/official_apps.sh"; '
          'ARCH=$2; package=$3; target=$4; DOWNLOAD_TIMEOUT=300; '
          + commands.get(feature, 'fetch_vendor_deb "$package" "$target"'),
          arch, package, target)
    name, version, architecture = deb_metadata(target)
    if name != package or architecture not in (arch, 'all'):
        raise ValueError(f'{package} 下载包的名称或架构不匹配')
    target.chmod(0o644)
    return version, target


def archive_versions(feature, arch):
    path = ARCHIVES[feature]
    if not path.is_dir():
        return None, None, None
    if feature == 'clion':
        current = clion_version(path)
        if not current:
            raise ValueError('无法读取本机 CLion 版本')
        try:
            version, _ = clion_archive(arch)
            if newer(version, current):
                return current, version, version
        except ValueError:
            pass
        version = clion_latest()
        return current, version, version
    if feature == 'hikcamera':
        match = re.search(r'^Version:\s*(\d+(?:\.\d+)+)', (path / 'ReleaseNote_EN.txt').read_text(errors='replace'), re.M)
        if not match:
            raise ValueError('无法读取本机 MVS 版本')
        url = hik_release(arch)
        return match[1], re.search(r'MVS-([\d.]+)_Linux', urlsplit(url).path)[1], url
    marker = path / '.ubuntu-autoconfig-version'
    if marker.is_file():
        current = marker.read_text().strip()
    elif (path / '.ubuntu-autoconfig-owned').is_file():
        history = (path / 'conda-meta/history').open()
        with history:
            match = re.match(r'==> (\d{4})-(\d\d)-', history.readline())
        if not match:
            raise ValueError('无法读取本机 Anaconda 版本')
        current = f'{match[1]}.{int(match[2]):02d}-0'
    else:
        raise ValueError('该 Anaconda 环境由其他渠道安装，无法确认其安装器版本')
    version = anaconda_latest(arch)
    return current, version, version


def scan(selected, installed, arch, suite, work):
    targets = {feature: installed_targets(feature, installed, suite) for feature in selected}
    candidates = package_candidates(set().union(*targets.values()))
    changes, archives, failures = {}, {}, []
    for feature, names in targets.items():
        available = []
        for name in sorted(names):
            version = candidates.get(name, candidates.get(name.split(':')[0]))
            if version and newer(version, installed[name]):
                available.append((name, version, f'{name}={version}'))
        package = DEB_APPS.get(feature)
        installed_name = next((name for name in names if name.split(':')[0] == package), None)
        try:
            if installed_name:
                version, file = vendor_candidate(feature, package, arch, work)
                if newer(version, installed[installed_name]):
                    available.append((installed_name, version, str(file)))
            if feature in ARCHIVES:
                current, version, source = archive_versions(feature, arch)
                if current and newer(version, current):
                    print(f'{feature}: {current} → {version}', flush=True)
                    archives[feature] = source
                else:
                    print(f'{feature}: ' + (f'已是当前可用版本 {current}' if current else '尚未安装'), flush=True)
            if feature == 'v2rayn' and not names and Path('/opt/v2rayN/v2rayN').is_file():
                raise ValueError('压缩包版 v2rayN 未登记版本，请使用具有版本信息的 deb')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f'{feature}: 版本检查未完成（{exc}）', flush=True)
            failures.append(feature)
        for name, version, argument in available:
            if name not in changes or newer(version, changes[name][0]) or (
                    version == changes[name][0] and argument.startswith('/')):
                changes[name] = (version, argument)
            print(f'{feature}: {name} {installed[name]} → {version}', flush=True)
        if feature not in ARCHIVES and not available and feature not in failures:
            print(f'{feature}: ' + ('已是当前可用版本' if names else '尚未安装'), flush=True)
    return changes, archives, failures


def package_key(name, architecture, installed):
    if name in installed or ':' in name:
        return name
    qualified = f'{name}:{architecture}'
    return qualified if qualified in installed else name


def planned_installs(simulation, installed, arch):
    return {package_key(name, machine or arch, installed): version
            for name, version, machine in INST.findall(simulation)}


def save_previous_debs(receipt, planned, installed, work):
    wanted = {name for name, version in planned.items() if name in installed and installed[name] != version}
    deb_directories = [Path('/var/cache/apt/archives'), ROOT / 'packages.local']
    download_caches = [STATE / 'runs/cache', STATE / 'cache']
    retained = {}
    backups = receipt.path / 'debs'
    backups.mkdir()

    def save(path):
        try:
            with path.open('rb') as stream:
                if stream.read(8) != b'!<arch>\n':
                    return
            name, version, architecture = deb_metadata(path)
        except (OSError, ValueError, subprocess.SubprocessError):
            return
        key = package_key(name, architecture, installed)
        if key in wanted and version == installed[key] and key not in retained:
            target = backups / f'{len(retained)}.deb'
            shutil.copy2(path, target)
            retained[key] = {'file': target.name, 'fingerprint': fingerprint(target)}

    for directory in deb_directories + download_caches:
        for path in directory.glob('*.deb' if directory in deb_directories else '*.complete'):
            if len(retained) == len(wanted):
                break
            save(path)
    for name in sorted(wanted - retained.keys()):
        with tempfile.TemporaryDirectory(dir=work) as temp:
            try:
                apt('download', f'{name}={installed[name]}', capture=True, cwd=temp, timeout=60)
            except subprocess.CalledProcessError:
                continue
            for path in Path(temp).glob('*.deb'):
                save(path)
    receipt.data['previous_debs'] = retained
    receipt.save()
    for name in sorted(wanted - retained.keys()):
        print(f'{name}: 旧版本暂不可下载，回退需旧镜像或本地旧包。', flush=True)


def apply_package_updates(receipt, changes, arch, work):
    args = ['install', '--no-remove', '--no-install-recommends', *(value[1] for value in changes.values())]
    simulation = apt('-s', *args, capture=True)
    before = receipt.data['packages_before']
    planned = planned_installs(simulation, before, arch)
    if any(planned.get(name) != spec[0] for name, spec in changes.items()):
        raise ValueError('APT 求解结果与选定更新版本不一致')
    save_previous_debs(receipt, planned, before, work)
    apt('--download-only', '-y', *args, timeout=300)
    receipt.prepare('apt_update_versions')
    try:
        apt_apply(args)
    finally:
        receipt.checkpoint('apt_update_versions', 'checked')
    if any(receipt.data['packages_after'].get(name) != spec[0] for name, spec in changes.items()):
        raise ValueError('更新后的软件包版本与计划不一致')


def apply_archive(receipt, feature, source, arch):
    receipt.prepare(feature)
    try:
        shell('SCRIPT_DIR=$1; ARCH=$2; UPDATE_ARCHIVE=1; '
              'if [[ $3 == anaconda ]]; then source "$SCRIPT_DIR/scripts/modules/official_apps.sh"; ANACONDA_VERSION=$4; '
              'else source "$SCRIPT_DIR/scripts/modules/$3.sh"; '
              'if [[ $3 == clion ]]; then CLION_VERSION=$4; else MVS_RELEASE_URL=$4; fi; fi; "install_$3"',
              arch, feature, source)
    finally:
        receipt.checkpoint(feature, 'checked')


def local_previous_deb(name, version, arch):
    package, _, installed_arch = name.partition(':')
    for path in (ROOT / 'packages.local').glob('*.deb'):
        try:
            actual_name, actual_version, actual_arch = deb_metadata(path)
        except (ValueError, OSError, subprocess.SubprocessError):
            continue
        if (actual_name, actual_version) == (package, version) and actual_arch in (installed_arch or arch, 'all'):
            return path
    return None


def rollback_versions(receipt, execute, work, arch):
    if not receipt.data.get('version_update') or receipt.data.get('active'):
        raise ValueError('该记录不是已记录完整状态的版本更新')
    before = receipt.data['packages_before']
    after = receipt.data.get('packages_after', before)
    current = packages()
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    targets, allowed = [], {}
    plan = receipt.plan(current)
    if plan['preserve_modified_files'] or plan['preserve_runtime_changes']:
        raise ValueError('应用文件已在更新后发生变化，回退已停止')
    for name in plan['restore_files']:
        record = receipt.data['files'][name]
        if record['before'] is not None:
            source = receipt.path / 'files' / record['backup']
            if fingerprint(source) != record['before']:
                raise ValueError(f'{name} 的旧版本文件备份校验失败')
    for name in sorted(changed):
        old, new, now = before.get(name), after.get(name), current.get(name)
        if now == old:
            continue
        if now != new:
            raise ValueError(f'{name} 已在本次更新后变更，回退已停止')
        print(f'{name}: {new or "未安装"} → {old or "移除"}', flush=True)
        allowed[name] = old
        if old is None:
            targets.append(f'{name}-')
            continue
        previous = receipt.data.get('previous_debs', {}).get(name)
        package = None
        if previous:
            if not re.fullmatch(r'[0-9]+\.deb', previous['file']):
                raise ValueError(f'{name} 的旧版本备份记录不完整')
            package = receipt.path / 'debs' / previous['file']
            if fingerprint(package) != previous['fingerprint']:
                raise ValueError(f'{name} 的旧版本备份校验失败')
        else:
            package = local_previous_deb(name, old, arch)
        if package:
            local = work / f'previous-{len(targets)}.deb'
            shutil.copyfile(package, local)
            local.chmod(0o644)
            targets.append(str(local))
        else:
            targets.append(f'{name}={old}')
    for name in plan['restore_files']:
        print(f'还原 {name}', flush=True)
    if targets:
        args = ['install', '--allow-downgrades', '--no-install-recommends', *targets]
        simulation = apt('-s', *args, capture=True)
        for name, version in planned_installs(simulation, {**before, **current}, arch).items():
            if name not in allowed or allowed[name] != version:
                raise ValueError(f'回退会改变计划外的软件包 {name}')
        for package in re.findall(r'^Remv (\S+)', simulation, re.M):
            name = package_key(package, arch, current)
            if name not in allowed or allowed[name] is not None:
                raise ValueError(f'回退会卸载计划外的软件包 {name}')
        if execute:
            apt('--download-only', '-y', *args, timeout=300)
            apt_apply(args)
            restored = packages()
            if any(restored.get(name) != version for name, version in allowed.items()):
                raise ValueError('回退后的软件包版本与计划不一致')
    if execute:
        for name in plan['restore_files']:
            record = receipt.data['files'][name]
            if record['before'] is None:
                path = Path(name)
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            else:
                copy_path(receipt.path / 'files' / record['backup'], name)
        receipt.finish('versions-rolled-back')
        print('版本回退已完成。', flush=True)


def execute(args, work):
    arch = output(['dpkg', '--print-architecture']).strip()
    system = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if system.get('ID', '').strip('"') != 'ubuntu':
        raise ValueError('版本管理仅适用于 Ubuntu')
    suite = system.get('VERSION_CODENAME', '').strip('"')
    prepare_apt_sources(work)
    if args.action == 'rollback-version':
        latest = STATE / 'latest-version-run'
        run_id = args.run or (latest.read_text().strip() if latest.is_file() else '')
        if not re.fullmatch(r'[0-9]{8}-[0-9]{6}-[0-9]+', run_id):
            raise ValueError('没有可用的更新记录 ID')
        rollback_versions(Journal(STATE / 'runs' / run_id), args.yes and not args.dry_run, work, arch)
        return
    selected = args.only.split(',') if args.only else [name for name in FEATURES if name != 'system_upgrade']
    selected = list(dict.fromkeys('chrome' if name == 'chromium' else name for name in selected))
    for name in selected:
        if name not in FEATURES:
            raise ValueError(f'未知功能 {name}')
    supported = []
    for name in selected:
        ok, reason = support(name, arch, suite)
        if ok:
            supported.append(name)
        else:
            print(f'{name}: {reason}', flush=True)
    if not supported:
        return 0
    installed = packages()
    if any(installed_targets(name, installed, suite) for name in supported):
        apt('-o', 'APT::Update::Error-Mode=any', 'update', timeout=180)
    changes, archives, failures = scan(supported, installed, arch, suite, work)
    if args.action == 'check-updates' or args.dry_run or not (changes or archives):
        return bool(failures)
    run_id = time.strftime('%Y%m%d-%H%M%S') + f'-{os.getpid()}'
    receipt = Journal(STATE / 'runs' / run_id)
    receipt.begin(os.environ.get('SUDO_USER') or 'root')
    if receipt.data['packages_before'] != installed:
        raise ValueError('软件包状态在版本检查期间发生变化，请重新检查')
    receipt.data['version_update'] = True
    receipt.save()
    print(f'版本更新记录 {run_id}', flush=True)
    try:
        if changes:
            apply_package_updates(receipt, changes, arch, work)
        for feature, source in archives.items():
            apply_archive(receipt, feature, source, arch)
    except BaseException:
        receipt.finish('failed')
        raise
    else:
        receipt.finish('complete')
    finally:
        if (receipt.data.get('packages_after', installed) != installed
                or any(record['before'] != record['after'] for record in receipt.data['files'].values())):
            write_file(STATE / 'latest-version-run', run_id + '\n')
    return bool(failures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check-updates', 'update', 'rollback-version'))
    parser.add_argument('--only')
    parser.add_argument('--run')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.action == 'rollback-version' and args.only is not None:
        parser.error('版本回退按整次更新记录执行，请使用 --run')
    if args.action != 'rollback-version' and args.run is not None:
        parser.error('--run 仅用于版本回退')
    if os.geteuid() != 0:
        raise ValueError('请通过 manage.sh 以 root 运行版本管理')
    with tempfile.TemporaryDirectory(prefix='versions.', dir=os.environ.get('TMPDIR', STATE / 'runs')) as tmp:
        work = Path(tmp)
        work.chmod(0o755)
        return execute(args, work)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or '').strip()
        raise SystemExit('版本操作失败' + (f'\n{detail}' if detail else f'（退出码 {exc.returncode}）')) from None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'版本管理失败: {exc}') from None
