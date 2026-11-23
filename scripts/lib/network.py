#!/usr/bin/env python3
"""Direct HTTPS downloads from approved domestic mirrors and publishers."""
import argparse
import fcntl
import hashlib
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from posixpath import normpath
import re
import sys
import time
from urllib.parse import urlsplit, parse_qsl, unquote, urlencode
from urllib.request import (Request, ProxyHandler, HTTPRedirectHandler,
                            build_opener)

ALLOWED_HOSTS = {
    'mirrors.tuna.tsinghua.edu.cn', 'mirrors.bfsu.edu.cn',
    'mirrors.ustc.edu.cn', 'mirrors.nju.edu.cn', 'mirror.nju.edu.cn',
    'mirrors.aliyun.com', 'repo.huaweicloud.com', 'mirrors.huaweicloud.com',
    'mirror.sjtu.edu.cn', 'mirrors.sjtug.sjtu.edu.cn',
    'cdn-go.cn', 'qq-web.cdn-go.cn', 'im.qq.com', 'qqdl.gtimg.cn', 'dldir1.qq.com',
    'dldir1v6.qq.com', 'ime-sec.gtimg.com', 'ime.gtimg.com', 'shurufa.sogou.com',
    'typoraio.cn', 'downloads.typoraio.cn',
}
PUBLISHER_PATHS = {
    'www.jetbrains.com.cn': ('/clion/download',),
    'gitee.com': ('/api/v5/repos/spark-store-project/spark-store/releases',
                  '/spark-store-project/spark-store/releases/download',
                  '/spark-store-project/spark-store/raw/master/tool/spark-store.asc'),
    'raw.giteeusercontent.com': ('/spark-store-project/spark-store/raw/master/tool/spark-store.asc',),
    'mirrors.sdu.edu.cn': ('/spark-store',),
    'foruda.gitee.com': ('/attach_file',),
    'www.hikrobotics.com': ('/cn/Api/Foreground/Support/v2/DownCenter',
                            '/cn2/source/support/software'),
}
SPARK_RELEASE_ATTACHMENT = re.compile(
    r'/spark-store-project/spark-store/attach_files/[0-9]+/download/'
    r'spark-store_[0-9]+(?:\.[0-9]+)*_(?:amd64|arm64)\.deb'
)
SAMPLE_BYTES = 256 * 1024


def public_url(url):
    parsed = urlsplit(url)
    clean = parsed._replace(netloc=parsed.netloc.rsplit('@', 1)[-1], query='', fragment='').geturl()
    return clean + ('?[redacted]' if parsed.query else '')


def resource_identity(url):
    parsed = urlsplit(url)
    if parsed.hostname == 'qqdl.gtimg.cn' and parsed.path.endswith('.deb'):
        # Public QQ signatures expire; the immutable versioned file path and
        # fresh server validator identify its bytes independently of sign/t.
        query = urlencode([(k, v) for k, v in parse_qsl(parsed.query) if k not in ('sign', 't')])
        return parsed._replace(query=query, fragment='').geturl()
    return url


def check_url(url):
    parsed = urlsplit(url)
    path = normpath(unquote(parsed.path))
    publisher_path = any((path + '/').startswith(prefix + '/')
                         for prefix in PUBLISHER_PATHS.get(parsed.hostname, ()))
    spark_attachment = parsed.hostname == 'gitee.com' and SPARK_RELEASE_ATTACHMENT.fullmatch(path)
    if (parsed.scheme != 'https'
            or (parsed.hostname not in ALLOWED_HOSTS and not (publisher_path or spark_attachment))
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError(f'禁止使用非国内白名单 HTTPS 下载地址: {public_url(url)}')
    return url


class DomesticRedirect(HTTPRedirectHandler):
    def __init__(self, redirect_check=None):
        self.redirect_check = redirect_check

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        (self.redirect_check or check_url)(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_direct(url, sample=False, offset=0, validator=None, data=None, request_headers=None,
                *, request_check=None, redirect_check=None):
    (request_check or check_url)(url)
    headers = {'User-Agent': 'ubuntu-autoconfig/3.0', 'Accept-Encoding': 'identity'}
    if urlsplit(url).hostname == 'www.hikrobotics.com':
        headers['Referer'] = 'https://www.hikrobotics.com/cn/machinevision/service/download/'
    headers.update(request_headers or {})
    if sample:
        headers['Range'] = f'bytes=0-{SAMPLE_BYTES - 1}'
    elif offset:
        headers['Range'] = f'bytes={offset}-'
        if validator:
            headers['If-Range'] = validator
    # Explicitly ignore http(s)_proxy, ALL_PROXY, desktop and system proxy
    # discovery. Each redirect is checked before making the next request.
    opener = build_opener(ProxyHandler({}), DomesticRedirect(redirect_check))
    response = opener.open(Request(url, headers=headers, data=data), timeout=12 if sample else 30)
    try:
        (redirect_check or check_url)(response.geturl())
    except ValueError:
        response.close()
        raise
    return response


def validate(data, kind, content_type=''):
    signatures = {'deb': b'!<arch>\n', 'zip': b'PK\x03\x04', 'sh': b'#!', 'gzip': b'\x1f\x8b',
                  'xz': b'\xfd7zXZ\x00', 'key': b'-----BEGIN PGP PUBLIC KEY BLOCK-----'}
    if not data:
        raise ValueError('服务器返回空文件')
    if kind in signatures and not data.startswith(signatures[kind]):
        raise ValueError(f'服务器返回的内容不是 {kind} 文件')
    if kind not in ('html', 'text') and (b'<html' in data[:1024].lower()
                                       or 'text/html' in content_type):
        raise ValueError('服务器返回 HTML 页面而非安装文件')


def probe(url, kind, *, opener=None):
    start = time.monotonic()
    result = {'url': url, 'ok': False, 'direct': True}
    try:
        with (opener or open_direct)(url, sample=True) as response:
            data = response.read(SAMPLE_BYTES)
            validate(data, kind, response.headers.get('Content-Type', ''))
            elapsed = time.monotonic() - start
            result.update(ok=True, final_url=response.geturl(), bytes=len(data),
                          etag=response.headers.get('ETag'), modified=response.headers.get('Last-Modified'),
                          seconds=round(elapsed, 4), bytes_per_second=len(data) / elapsed)
    except Exception as exc:
        result['error'] = str(exc)
        result['retryable'] = not isinstance(exc, ValueError) and getattr(exc, 'code', 0) not in (400, 401, 403, 404, 410)
    return result


def rank(urls, kind):
    urls = list(dict.fromkeys(urls))
    with ThreadPoolExecutor(max_workers=min(6, len(urls))) as executor:
        results = list(executor.map(lambda url: probe(url, kind), urls))
    for result in results:
        if result['ok']:
            print(f"[直连测速] {public_url(result['url'])}: {result['bytes_per_second']/1024:.1f} KiB/s, "
                  f"{result['seconds']:.3f}s", file=sys.stderr)
        else:
            print(f"[镜像不可用] {public_url(result['url'])}: {result['error']}", file=sys.stderr)
    return sorted(results, key=lambda r: r.get('bytes_per_second', 0), reverse=True)


def file_sha256(path):
    with Path(path).open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        return digest.hexdigest()


def save_metadata(path, data):
    temp = Path(str(path) + '.tmp')
    temp.write_text(json.dumps(data))
    temp.replace(path)


def download(url, output, kind, cache=None, expected=None, *, opener=None, expected_sha256=None):
    output = Path(output)
    identity = resource_identity(url)
    if cache:
        cache = Path(cache)
        cache.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(identity.encode()).hexdigest()
        base = cache / key
    else:
        base = output
    partial = Path(str(base) + '.part')
    metadata_file = Path(str(base) + '.json')
    complete = Path(str(base) + '.complete')
    lock_file = Path(str(base) + '.lock')
    with lock_file.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            metadata = json.loads(metadata_file.read_text())
        except (OSError, ValueError):
            metadata = {}
        current_validator = (expected or {}).get('etag') or (expected or {}).get('modified')
        stored_identity = metadata.get('identity', metadata.get('url'))
        cached_digest = metadata.get('sha256')
        trusted_cache = (cached_digest == expected_sha256 if expected_sha256 else
                         current_validator and current_validator == metadata.get('validator'))
        if (complete.exists() and stored_identity == identity and trusted_cache
                and cached_digest == file_sha256(complete)):
            shutil.copyfile(complete, output)
            print(f'[复用缓存] {public_url(url)}', file=sys.stderr)
            return
        validator = metadata.get('validator') if stored_identity == identity else None
        if current_validator and current_validator != validator:
            validator = None
        offset = partial.stat().st_size if partial.exists() and validator else 0
        if offset:
            print(f'[断点续传] 从 {offset} bytes 继续: {public_url(url)}', file=sys.stderr)
        with (opener or open_direct)(url, offset=offset, validator=validator) as response:
            append = response.status == 206 and offset > 0
            if response.status not in (200, 206):
                raise ValueError(f'下载响应错误: {response.status}')
            response_validator = response.headers.get('ETag') or response.headers.get('Last-Modified')
            if response_validator and response_validator.startswith('W/'):
                response_validator = response.headers.get('Last-Modified')
            if append:
                content_range = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                if not content_range or int(content_range[1]) != offset or response_validator != validator:
                    # Do not concatenate mismatching versions or mirror payloads.
                    partial.unlink(missing_ok=True)
                    metadata_file.unlink(missing_ok=True)
                    raise ValueError('服务器断点范围/版本不一致，将重新下载')
                expected_size = int(content_range[3])
            else:
                if response.status != 200:
                    raise ValueError('首次完整下载意外收到分段响应')
                offset = 0
                length = response.headers.get('Content-Length')
                expected_size = int(length) if length else None
            metadata = dict(url=url, identity=identity, validator=response_validator, size=expected_size)
            with partial.open('ab' if append else 'wb') as target:
                # Truncate an old version before publishing its new validator.
                # A kill between these two operations must never pair old bytes
                # with new ETag metadata and corrupt a later range request.
                save_metadata(metadata_file, metadata)
                first = response.read(64 * 1024)
                if not append:
                    validate(first, kind, response.headers.get('Content-Type', ''))
                target.write(first)
                target.flush()
                total = offset + len(first)
                progress_at = time.monotonic()
                for chunk in iter(lambda: response.read(1024 * 1024), b''):
                    target.write(chunk)
                    target.flush()
                    total += len(chunk)
                    if time.monotonic() - progress_at > 15:
                        print(f'[下载进度] {total / 1024**2:.1f} MiB' +
                              (f' / {expected_size / 1024**2:.1f} MiB' if expected_size else ''), file=sys.stderr)
                        progress_at = time.monotonic()
                os.fsync(target.fileno())
            if expected_size is not None and total != expected_size:
                raise ValueError(f'下载不完整: {total}/{expected_size} bytes，已保留断点')
        metadata['sha256'] = file_sha256(partial)
        if expected_sha256 and metadata['sha256'] != expected_sha256:
            partial.unlink(missing_ok=True)
            metadata_file.unlink(missing_ok=True)
            raise ValueError('安装包与官方 SHA-256 不一致，已丢弃错误下载')
        if cache:
            partial.replace(complete)
            save_metadata(metadata_file, metadata)
            shutil.copyfile(complete, output)
        else:
            partial.replace(output)
            metadata_file.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['rank', 'download', 'select', 'audit-apt'])
    parser.add_argument('--kind', default='text', choices=['text', 'html', 'yaml', 'deb', 'zip', 'sh', 'xz', 'key'])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--suffix', default='')
    parser.add_argument('--cache-dir', type=Path, default=os.environ.get('AUTOCONFIG_CACHE_DIR'))
    parser.add_argument('--retries', type=int, default=3)
    parser.add_argument('urls', nargs='*')
    args = parser.parse_args()
    if args.action == 'audit-apt':
        source_dir = Path('/etc/apt/sources.list.d')
        paths = list(map(Path, args.urls)) if args.urls else [
            Path('/etc/apt/sources.list'), *source_dir.glob('*.list'), *source_dir.glob('*.sources')]
        for path in paths:
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                if line.lstrip().startswith('#'):
                    continue
                for url in re.findall(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+', line):
                    try:
                        check_url(url)
                    except ValueError as exc:
                        raise SystemExit(f'{path}: {exc}') from None
        return
    if not args.urls:
        parser.error('必须提供至少一个下载地址')
    urls = [base.rstrip('/') + args.suffix for base in args.urls]
    for attempt in range(max(1, args.retries)):
        results = rank(urls, args.kind)
        if any(item['ok'] for item in results) or not any(item.get('retryable') for item in results):
            break
        if attempt + 1 < args.retries:
            print('[网络暂不可用] 稍后重试直连探测。', file=sys.stderr)
            time.sleep(min(2 ** attempt, 8))
    if args.report:
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    available = [r for r in results if r['ok']]
    if not available:
        raise SystemExit('没有可直连的下载地址。')
    if args.action == 'rank':
        print('\n'.join(r['url'] for r in available))
    elif args.action == 'select':
        best = available[0]['url']
        print(best[:-len(args.suffix)] if args.suffix else best)
    else:
        if not args.output:
            parser.error('download requires --output')
        for result in available:
            for attempt in range(max(1, args.retries)):
                try:
                    print(f"[直连下载 {attempt+1}/{args.retries}] {public_url(result['url'])}", file=sys.stderr)
                    download(result['url'], args.output, args.kind, args.cache_dir, result)
                    return
                except Exception as exc:
                    print(f"[下载失败，保留断点] {exc}", file=sys.stderr)
                    if attempt + 1 < args.retries:
                        time.sleep(min(2 ** attempt, 8))
        raise SystemExit('所有直连下载地址均失败。')


if __name__ == '__main__':
    main()
