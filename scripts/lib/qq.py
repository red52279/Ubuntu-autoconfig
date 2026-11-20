#!/usr/bin/env python3
"""Resolve a current signed Linux QQ URL through the official site's public API."""
import argparse
import json
from urllib.parse import urlsplit, parse_qs
from network import check_url, open_direct

CONFIG_URLS = (
    'https://qq-web.cdn-go.cn/im.qq.com_new/latest/rainbow/pcConfig.json',
    'https://im.qq.com/proxy/domain/qq-web.cdn-go.cn/im.qq.com_new/latest/rainbow/pcConfig.json',
)
SIGN_URL = 'https://im.qq.com/http2rpc/gotrpc/noauth/trpc.qqntv2.urlsign.UrlSign/GetSign'
HOSTS = {'qqdl.gtimg.cn', 'dldir1.qq.com', 'dldir1v6.qq.com'}


def validate_qq_url(url):
    check_url(url)
    parsed = urlsplit(url)
    if parsed.hostname not in HOSTS or not parsed.path.endswith('.deb'):
        raise ValueError('QQ 官网返回的下载地址不是预期的官方 deb。')
    return url


def choose_url(data, arch='amd64'):
    key = {'amd64': 'x64DownloadUrl', 'arm64': 'armDownloadUrl'}[arch]
    return validate_qq_url(data['Linux'][key]['deb'])


def read_json(url, **kwargs):
    with open_direct(url, **kwargs) as response:
        return json.loads(response.read(1024 * 1024))


def signed_url(url):
    validate_qq_url(url)
    data = read_json(SIGN_URL, data=json.dumps({'url': url}).encode(), request_headers={
        'Content-Type': 'application/json',
        'Origin': 'https://im.qq.com', 'Referer': 'https://im.qq.com/index/',
        'x-oidb': json.dumps({'uint32_command': '0x9b8e', 'uint32_service_type': 1}, separators=(',', ':')),
    })
    if data.get('retcode') != 0 or not data.get('data', {}).get('url'):
        raise ValueError(f"QQ 官网签名接口失败，retcode={data.get('retcode')}")
    result = validate_qq_url(data['data']['url'])
    if urlsplit(result).path != urlsplit(url).path:
        raise ValueError('QQ 签名接口返回的文件路径与发布配置不一致。')
    query = parse_qs(urlsplit(result).query)
    if urlsplit(result).hostname == 'qqdl.gtimg.cn' and not (query.get('sign') and query.get('t')):
        raise ValueError('QQ 下载地址缺少必要的 sign/t 参数。')
    return result


def resolve(arch='amd64'):
    errors = []
    for endpoint in CONFIG_URLS:
        try:
            return signed_url(choose_url(read_json(endpoint), arch))
        except (OSError, ValueError, KeyError) as exc:
            errors.append(str(exc))
    raise RuntimeError('无法取得 QQ 官网的有效下载签名: ' + '; '.join(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', choices=['amd64', 'arm64'], default='amd64')
    args = parser.parse_args()
    print(resolve(args.arch))


if __name__ == '__main__':
    main()
