"""Discover Qt6 development packages and baseline tools for this Ubuntu release."""
import os
import re
import subprocess
import sys
from itertools import chain

from catalog import FEATURES


def is_qt_dev_package(name, source, homepage):
    name = name.partition(':')[0]
    if not name.endswith('-dev'):
        return False
    source = source.partition(' ')[0] or name
    return (source.startswith('qt6-') or source in ('pyside6', 'shiboken6')) and bool(re.match(
        r'https?://(?:[a-z0-9-]+\.)*(?:qt\.io|qt-project\.org)(?:/|$)', homepage, re.I))


def qt_packages_from_index(lines, arch):
    names, fields = set(), {}
    baseline = set(FEATURES['desktop_tools']['packages'])
    for line in chain(lines, ('\n',)):
        if line == '\n':
            name = fields.get('Package', '')
            if fields.get('Architecture') in (arch, 'all') and (
                name in baseline or is_qt_dev_package(name, fields.get('Source', ''), fields.get('Homepage', ''))
            ):
                names.add(name)
            fields.clear()
        elif line.startswith(('Package: ', 'Source: ', 'Homepage: ', 'Architecture: ')):
            key, _, value = line.partition(': ')
            fields[key] = value.rstrip()
    return sorted(names)


def available_qt_packages(arch):
    command = ['apt-cache']
    # Match the isolated APT source list when invoked from version management.
    sources = os.environ.get('AUTOCONFIG_APT_SOURCES_DIR')
    if sources:
        command += ['-o', f'Dir::Etc::sourcelist={sources}/sources.list',
                    '-o', f'Dir::Etc::sourceparts={sources}/sources.list.d']
    command.append('dumpavail')
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True, errors='replace') as process:
        names = qt_packages_from_index(process.stdout, arch)
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, command)
    return names


def installed_qt_dev_packages():
    rows = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${binary:Package}\t${source:Package}\t${Homepage}\t${db:Status-Status}\n'],
        text=True)
    return {name for name, source, homepage, state in (line.split('\t') for line in rows.splitlines())
            if state == 'installed' and is_qt_dev_package(name, source, homepage)}


if __name__ == '__main__':
    packages = available_qt_packages(sys.argv[1])
    if 'qt6-base-dev' not in packages:
        raise SystemExit('当前 APT 索引中没有匹配架构的 Qt6 开发包。')
    print('\n'.join(packages))
