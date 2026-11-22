"""Version and archive handling for applications installed under /opt."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile

from network import file_sha256, open_direct
from desktop_locale import write_file

ROOT = Path(__file__).resolve().parents[2]
CLION_PATH = Path('/opt/clion')
ANACONDA_ARCHIVE = 'https://mirrors.nju.edu.cn/anaconda/archive/'
MACHINE = {'amd64': 'x86_64', 'arm64': 'aarch64'}
DESKTOP_DIR = Path('/usr/share/applications')
V2RAYN_DESKTOP = """[Desktop Entry]
Name=v2rayN
Comment=v2rayN
Exec=/opt/v2rayN/v2rayN
Terminal=false
Type=Application
Categories=Network;
"""


def newer(version, installed):
    return subprocess.run(['dpkg', '--compare-versions', version, 'gt', installed]).returncode == 0


def v2rayn_launcher(path):
    try:
        line = next(line for line in path.read_text().splitlines() if line.startswith('Exec='))
        command = shlex.split(line.partition('=')[2])
        for item in command:
            if Path(item).name in ('v2rayN', 'v2rayn'):
                executable = shutil.which(item)
                if executable:
                    return executable
        return None
    except (OSError, StopIteration, ValueError):
        return None


def v2rayn_managed_launcher(path):
    return path.is_file() and not path.is_symlink() and path.read_text() == V2RAYN_DESKTOP


def v2rayn_desktop_ready(directory=DESKTOP_DIR):
    managed = directory / 'v2rayn.desktop'
    if v2rayn_launcher(directory / 'v2rayN.desktop'):
        return not v2rayn_managed_launcher(managed)
    return bool(v2rayn_launcher(managed))


def configure_v2rayn_desktop(directory=DESKTOP_DIR):
    managed = directory / 'v2rayn.desktop'
    if v2rayn_launcher(directory / 'v2rayN.desktop'):
        if v2rayn_managed_launcher(managed):
            managed.unlink()
    elif not managed.exists():
        write_file(managed, V2RAYN_DESKTOP)
    elif not v2rayn_launcher(managed):
        raise ValueError(f'现有 v2rayN 启动器不可执行，请检查 {managed}')


def clion_version(directory):
    try:
        info = json.loads((directory / 'product-info.json').read_text())
        version = info.get('version', '')
        if info.get('productCode') == 'CL' and isinstance(version, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', version):
            return version
        return None
    except (OSError, ValueError):
        return None


def clion_archive(arch, package_dir=None):
    package_dir = package_dir or ROOT / 'packages.local'
    suffix = r'(?:-x86_64|-amd64)?' if arch == 'amd64' else r'(?:-aarch64|-arm64)'
    pattern = re.compile(r'clion-([0-9]+(?:\.[0-9]+)+)' + suffix + r'\.tar\.gz', re.I)
    releases = []
    for path in package_dir.iterdir() if package_dir.is_dir() else ():
        match = pattern.fullmatch(path.name)
        if match and path.is_file():
            releases.append((tuple(map(int, match[1].split('.'))), match[1], path))
    if not releases:
        raise ValueError(f'请将 JetBrains CLion 稳定版 Linux {arch} 的原始 tar.gz 放入 packages.local/')
    _, version, archive = max(releases)
    return version, archive


def clion_latest():
    with open_direct('https://www.jetbrains.com.cn/clion/download/') as response:
        page = response.read(3 * 1024 * 1024)
    releases = re.findall(rb'/cpp/CLion-([0-9]+(?:\.[0-9]+){1,3})(?:-aarch64)?\.tar\.gz', page, re.I)
    if not releases:
        raise ValueError('JetBrains 中国站未返回可识别的 CLion 稳定版')
    return max((release.decode() for release in releases), key=lambda x: tuple(map(int, x.split('.'))))


def install_clion(arch, directory=CLION_PATH, package_dir=None):
    version, archive = clion_archive(arch, package_dir)
    if directory.exists() and not clion_version(directory):
        raise ValueError(f'{directory} 已存在且无法识别为 CLion 安装目录')
    if directory.is_symlink():
        raise ValueError(f'{directory} 指向另一位置，请通过现有安装渠道更新')
    current = clion_version(directory)
    if current and newer(current, version):
        raise ValueError('本地 CLion 包早于当前安装版本，请使用版本回退命令或提供更新的归档')
    checksum = archive.with_name(archive.name + '.sha256')
    if checksum.is_file():
        fields = checksum.read_text().split()
        if not fields or fields[0].lower() != file_sha256(archive):
            raise ValueError('CLion 归档和提供的 SHA-256 校验文件不匹配')
    with tempfile.TemporaryDirectory(prefix='.clion-', dir=directory.parent) as stage:
        new = Path(stage) / 'application'
        new.mkdir(mode=0o755)
        # The local archive must contain one root directory and no paths
        # that can escape it when unpacked.
        with tarfile.open(archive, 'r:gz') as stream:
            top = None
            for entry in stream:
                path = Path(entry.name)
                if path.is_absolute() or '..' in path.parts or not path.parts:
                    raise ValueError('CLion 归档中存在无效路径')
                top = top or path.parts[0]
                if path.parts[0] != top or not (entry.isfile() or entry.isdir() or entry.issym() or entry.islnk()):
                    raise ValueError('CLion 归档布局或文件类型无效')
                if entry.issym() or entry.islnk():
                    target = Path(entry.linkname)
                    if target.is_absolute() or '..' in target.parts:
                        raise ValueError('CLion 归档中的链接指向未知位置')
        subprocess.run(['tar', '-xzf', str(archive), '--strip-components=1', '--no-same-owner',
                        '--no-same-permissions', '-C', str(new)], check=True)
        if clion_version(new) != version or not os.access(new / 'bin/clion.sh', os.X_OK):
            raise ValueError('CLion 文件名版本与产品元数据不一致，或包内缺少启动程序')
        info = json.loads((new / 'product-info.json').read_text())
        if not any(item.get('os') == 'Linux' and item.get('arch') == MACHINE[arch] for item in info.get('launch', [])):
            raise ValueError('CLion 包的目标系统或架构与本机不匹配')
        previous = Path(stage) / 'previous'
        if directory.exists():
            directory.rename(previous)
        try:
            new.rename(directory)
        except OSError:
            if previous.exists():
                previous.rename(directory)
            raise


def anaconda_latest(arch):
    with open_direct(ANACONDA_ARCHIVE) as response:
        page = response.read(2 * 1024 * 1024).decode(errors='replace')
    versions = re.findall(rf'Anaconda3-(\d{{4}}\.\d+-\d+)-Linux-{MACHINE[arch]}\.sh', page)
    if not versions:
        raise ValueError('南京大学镜像未返回 Anaconda 版本列表')
    return max(versions, key=lambda x: tuple(map(int, re.split(r'[.-]', x))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('arch', choices=MACHINE)
    parser.add_argument('--configure-v2rayn', action='store_true')
    try:
        arguments = parser.parse_args()
        if arguments.configure_v2rayn:
            configure_v2rayn_desktop()
        else:
            install_clion(arguments.arch)
    except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'{exc}\n')
