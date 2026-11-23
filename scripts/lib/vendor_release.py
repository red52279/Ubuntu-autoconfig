#!/usr/bin/env python3
"""Resolve the selected architecture's download from a publisher's release API."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlencode, urlsplit

from network import check_url, open_direct

SPARK_API = 'https://gitee.com/api/v5/repos/spark-store-project/spark-store/releases/latest'
HIK_API = 'https://www.hikrobotics.com/cn/Api/Foreground/Support/v2/DownCenter'
SPARK_MIRROR = 'https://mirrors.sdu.edu.cn/spark-store'
TYPORA_CDN = 'https://downloads.typoraio.cn'


def release_json(url):
    for attempt in range(3):
        try:
            with open_direct(url, request_headers={'Accept': 'application/json'}) as response:
                return json.load(response)
        except (OSError, ValueError) as exc:
            if attempt == 2:
                raise ValueError(f'读取厂商发布信息失败（{urlsplit(url).hostname}）: {exc}') from exc
            time.sleep(2 ** attempt)


def spark_release(arch):
    release = release_json(SPARK_API)
    prefix = 'https://gitee.com/spark-store-project/spark-store/releases/download/'
    pattern = rf'spark-store_[\d.]+_{arch}\.deb'
    if not release.get('prerelease') and not release.get('draft'):
        for asset in release['assets']:
            name = asset['name']
            url = asset['browser_download_url']
            if (re.fullmatch(pattern, name) and url.startswith(prefix)
                    and urlsplit(url).path.rsplit('/', 1)[-1] == name):
                return check_url(url)
    raise ValueError('厂商发布中没有匹配架构和格式的正式安装包')


def signed_packages(release, index):
    """Read Packages only after its SHA-256 is matched to the verified Release."""
    expected = re.search(r'^SHA256:\n((?:[ \t]+[^\n]+\n)+)', release.read_text(), re.M)
    data = index.read_bytes()
    actual = [hashlib.sha256(data).hexdigest(), str(len(data)), 'Packages']
    if not expected or not any(row.split() == actual for row in expected[1].splitlines()):
        raise ValueError('Packages 与签名索引不一致，请稍后重试')
    for stanza in data.split(b'\n\n'):
        yield dict(line.split(': ', 1) for line in stanza.decode('utf-8', errors='replace').splitlines()
                   if line and not line[0].isspace() and ': ' in line)


def spark_archive(arch, package, release, index):
    for fields in signed_packages(release, index):
        if fields.get('Package') != package or fields.get('Architecture') != arch:
            continue
        filename = fields['Filename']
        digest = fields['SHA256']
        if (not re.fullmatch(r'\./[a-zA-Z0-9._+-]+/[a-zA-Z0-9._+-]+/[a-zA-Z0-9._+~%-]+\.deb', filename)
                or any(part in ('.', '..') for part in filename[2:].split('/'))
                or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise ValueError('星火镜像安装包路径或 SHA-256 无效')
        return check_url(f'{SPARK_MIRROR}/{arch}-store/{filename[2:]}'), digest
    raise ValueError(f'星火国内镜像没有 {arch} 的 {package} 包')


def typora_archive(arch, release, index):
    selected = None
    for fields in signed_packages(release, index):
        if fields.get('Package') != 'typora' or fields.get('Architecture') != arch:
            continue
        if selected is None or subprocess.run(
            ['dpkg', '--compare-versions', fields['Version'], 'gt', selected['Version']], check=False
        ).returncode == 0:
            selected = fields
    if not selected:
        raise ValueError(f'Typora 签名索引没有 {arch} 安装包')
    filename, digest = selected['Filename'], selected['SHA256']
    if (not re.fullmatch(rf'linux/typora_[0-9][a-zA-Z0-9.+~-]*_{arch}\.deb', filename)
            or not re.fullmatch(r'[0-9a-f]{64}', digest)):
        raise ValueError('Typora 安装包路径或 SHA-256 无效')
    return check_url(f'{TYPORA_CDN}/{filename}'), digest


def managed_typora_source(text):
    return bool(re.fullmatch(
        r'deb \[arch=(?:amd64|arm64) signed-by=/usr/share/keyrings/typora-archive-keyring\.gpg\] '
        r'(?:https://typoraio\.cn/linux \./|https://downloads\.typoraio\.cn linux/)',
        text.strip()))


def hik_release(arch):
    machine = {'amd64': 'x86_64', 'arm64': 'aarch64'}[arch]
    params = urlencode(dict(category=0, module=0, size=40, page=1, fileType=2,
                            title='MVS', operateSystem='Linux', timestamp=int(time.time() * 1000)))
    result = release_json(f'{HIK_API}?{params}')
    matches = []
    prefix = 'https://www.hikrobotics.com/cn2/source/support/software/'
    if result.get('status') == 'success':
        for row in result['data']['records']:
            url = row.get('extLinks') or ''
            if not url.startswith(prefix):
                continue
            name = urlsplit(url).path.rsplit('/', 1)[-1]
            match = re.fullmatch(rf'MVS-(\d+(?:\.\d+)+)_Linux_{machine}_(\d{{8}})\.zip', name)
            if match:
                matches.append((tuple(map(int, match[1].split('.'))), match[2], check_url(url)))
    if not matches:
        raise ValueError(f'海康官网下载中心没有匹配 {machine} 的 MVS 安装包')
    return max(matches)[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', choices=('spark-store', 'spark-archive', 'typora-archive', 'typora-legacy-source', 'hikcamera'))
    parser.add_argument('arch', choices=('amd64', 'arm64'))
    parser.add_argument('--package', choices=('google-chrome-stable', 'microsoft-edge-stable', 'code', 'v2rayn'))
    parser.add_argument('--release', type=Path)
    parser.add_argument('--index', type=Path)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    try:
        if args.app == 'spark-store':
            print(spark_release(args.arch))
        elif args.app == 'spark-archive':
            if not (args.package and args.release and args.index):
                parser.error('spark-archive requires --package, --release and --index')
            print(*spark_archive(args.arch, args.package, args.release, args.index), sep='\t')
        elif args.app == 'typora-archive':
            if not (args.release and args.index):
                parser.error('typora-archive requires --release and --index')
            print(*typora_archive(args.arch, args.release, args.index), sep='\t')
        elif args.app == 'typora-legacy-source':
            if not args.source:
                parser.error('typora-legacy-source requires --source')
            raise SystemExit(0 if managed_typora_source(args.source.read_text()) else 1)
        else:
            print(hik_release(args.arch))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f'{args.app}: {exc}\n')


if __name__ == '__main__':
    main()
