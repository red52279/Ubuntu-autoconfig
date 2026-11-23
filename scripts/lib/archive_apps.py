"""Version and archive handling for applications installed under /opt."""
import argparse
from contextlib import ExitStack
import fcntl
from http.client import HTTPException
import json
import os
from pathlib import Path, PurePosixPath
from posixpath import normpath
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import urlsplit

from network import download, file_sha256, open_direct, public_url
from desktop_locale import write_file

ROOT = Path(__file__).resolve().parents[2]
CLION_PATH = Path('/opt/clion')
ANACONDA_ARCHIVE = 'https://mirrors.nju.edu.cn/anaconda/archive/'
MACHINE = {'amd64': 'x86_64', 'arm64': 'aarch64'}
CLION_VERSION = r'[0-9]+(?:\.[0-9]+){1,3}'
CLION_ORIGIN = 'download.jetbrains.com'
CLION_CHINA_CDN = 'download-cdn.clf.jetbrains.com.cn'
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


def clion_archive(arch, package_dir=None, version=None):
    package_dir = package_dir or ROOT / 'packages.local'
    suffix = r'(?:-x86_64|-amd64)?' if arch == 'amd64' else r'(?:-aarch64|-arm64)'
    pattern = re.compile(rf'clion-({CLION_VERSION})' + suffix + r'\.tar\.gz', re.I)
    releases = []
    for path in package_dir.iterdir() if package_dir.is_dir() else ():
        match = pattern.fullmatch(path.name)
        if match and (version is None or version == match[1]) and path.is_file():
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


def clion_filename(arch, version):
    if not re.fullmatch(CLION_VERSION, version):
        raise ValueError('无效的 CLion 稳定版版本号')
    suffix = '-aarch64' if arch == 'arm64' else ''
    return f'CLion-{version}{suffix}.tar.gz'


def open_clion_download(url, **kwargs):
    path = urlsplit(url).path
    checksum = path.endswith('.sha256')

    def check(target, hosts):
        parts = urlsplit(target)
        if (parts.scheme != 'https' or parts.hostname not in hosts or parts.path != path
                or parts.username or parts.password or parts.port not in (None, 443)):
            raise ValueError(f'CLion 响应未指向同一安装包的官方中国 CDN: {public_url(target)}')

    if not re.fullmatch(rf'/cpp/CLion-{CLION_VERSION}(?:-aarch64)?\.tar\.gz(?:\.sha256)?', path):
        raise ValueError('无效的 CLion 官方安装包路径')
    hosts = (CLION_CHINA_CDN, CLION_ORIGIN) if checksum else (CLION_CHINA_CDN,)
    return open_direct(url, request_check=lambda target: check(target, (CLION_ORIGIN,)),
                       redirect_check=lambda target: check(target, hosts), **kwargs)


def clion_checksum(arch, version):
    filename = clion_filename(arch, version)
    with open_clion_download(f'https://{CLION_ORIGIN}/cpp/{filename}.sha256') as response:
        fields = response.read(4096).decode('ascii').split()
    if not fields or not re.fullmatch(r'[a-fA-F0-9]{64}', fields[0]):
        raise ValueError('JetBrains 未返回有效的 SHA-256')
    if len(fields) > 1 and fields[1].lstrip('*') != filename:
        raise ValueError('JetBrains 校验文件对应的安装包名称不匹配')
    return fields[0].lower()


def download_clion(arch, version, output, cache=None):
    digest = clion_checksum(arch, version)
    if output.is_file() and file_sha256(output) == digest:
        return digest
    url = f'https://{CLION_ORIGIN}/cpp/{clion_filename(arch, version)}'
    print(f'[CLion] 校验缓存或下载 {clion_filename(arch, version)}', flush=True)
    for attempt in range(3):
        try:
            download(url, output, 'gzip', cache, opener=open_clion_download, expected_sha256=digest)
            return digest
        except (OSError, ValueError, HTTPException):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def save_clion_download(arch, version):
    filename = clion_filename(arch, version)
    target = ROOT / 'packages.local' / filename
    work = ROOT / 'runs' / 'clion-downloads'
    work.mkdir(parents=True, exist_ok=True)
    with (work / (filename + '.publish.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists():
            digest = clion_checksum(arch, version)
            if file_sha256(target) != digest:
                raise ValueError(f'现有 {target.name} 校验不一致，请移开该文件后重试')
        else:
            staged = work / filename
            digest = download_clion(arch, version, staged)
            target.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(target)
        write_file(target.with_name(filename + '.sha256'), f'{digest} *{filename}\n')
    return target


def install_clion(arch, directory=CLION_PATH, package_dir=None, version=None):
    with ExitStack() as resources:
        try:
            selected, archive = clion_archive(arch, package_dir, version)
        except ValueError:
            selected = version or clion_latest()
            work = Path(resources.enter_context(tempfile.TemporaryDirectory(prefix='clion-download.')))
            archive = work / clion_filename(arch, selected)
            download_clion(arch, selected, archive, os.environ.get('AUTOCONFIG_CACHE_DIR'))
        unpack_clion(arch, directory, selected, archive)


def clion_archive_links(archive):
    entries, links, hardlinks = {}, [], []
    top = None
    with tarfile.open(archive, 'r:gz') as stream:
        for entry in stream:
            path = PurePosixPath(entry.name)
            if path.is_absolute() or '..' in path.parts or not path.parts:
                raise ValueError('CLion 归档中存在无效路径')
            top = top or path.parts[0]
            previous = entries.get(path)
            if (path.parts[0] != top or (previous and not (previous.isdir() and entry.isdir()))
                    or (len(path.parts) == 1 and not entry.isdir())
                    or not (entry.isfile() or entry.isdir() or entry.issym() or entry.islnk())):
                raise ValueError('CLion 归档布局或文件类型无效')
            entries[path] = entry
            if entry.issym():
                relative = PurePosixPath(*path.parts[1:])
                target = PurePosixPath(normpath(relative.parent / entry.linkname))
                if target.is_absolute() or '..' in target.parts:
                    raise ValueError('CLion 归档中的链接超出安装目录')
                links.append(relative)
            elif entry.islnk():
                hardlinks.append(PurePosixPath(entry.linkname))

    # Extraction must never write through an archive link. A symlink's
    # resolved target can then be validated safely in the staged tree.
    for path in entries:
        if any(parent in entries and not entries[parent].isdir() for parent in path.parents):
            raise ValueError('CLion 归档试图在链接或普通文件下写入')
    if any(target not in entries or not entries[target].isfile() for target in hardlinks):
        raise ValueError('CLion 归档包含无效的硬链接目标')
    return links


def unpack_clion(arch, directory, version, archive):
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
    links = clion_archive_links(archive)
    with tempfile.TemporaryDirectory(prefix='.clion-', dir=directory.parent) as stage:
        new = Path(stage) / 'application'
        new.mkdir(mode=0o755)
        subprocess.run(['tar', '-xzf', str(archive), '--strip-components=1', '--no-same-owner',
                        '--no-same-permissions', '-C', str(new)], check=True)
        root = new.resolve()
        for link in links:
            resolved = (new / link).resolve()
            if resolved != root and root not in resolved.parents:
                raise ValueError('CLion 归档中的链接解析后超出安装目录')
        if clion_version(new) != version or not os.access(new / 'bin/clion.sh', os.X_OK):
            raise ValueError('CLion 文件名版本与产品元数据不一致，或包内缺少启动程序')
        info = json.loads((new / 'product-info.json').read_text())
        if not any(item.get('os') == 'Linux' and item.get('arch') in (arch, MACHINE[arch])
                   for item in info.get('launch', [])):
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
    parser.add_argument('--download-clion', action='store_true', help='下载并校验 CLion，保存到 packages.local/')
    parser.add_argument('--version', help='指定 CLion 稳定版')
    try:
        arguments = parser.parse_args()
        if arguments.configure_v2rayn:
            configure_v2rayn_desktop()
        elif arguments.download_clion:
            print(save_clion_download(arguments.arch, arguments.version or clion_latest()))
        else:
            install_clion(arguments.arch, version=arguments.version)
    except (OSError, ValueError, HTTPException, tarfile.TarError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'{exc}\n')
