import io
import importlib.util
from pathlib import Path
from run_files import temporary_directory
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('network', Path(__file__).resolve().parents[2] / 'scripts/lib/network.py')
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


class Response(io.BytesIO):
    status = 200
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {'Content-Type': 'application/octet-stream', 'Content-Length': str(len(data) if length is None else length)}
    def geturl(self):
        return 'https://mirrors.nju.edu.cn/test.sh'


class NetworkTests(unittest.TestCase):
    def test_foreign_host_and_plain_http_rejected(self):
        for url in ('https://github.com/a', 'https://ghfast.top/a', 'https://gh-proxy.com/a', 'http://mirrors.nju.edu.cn/a',
                    'https://mirrors.nju.edu.cn.evil.example/a', 'https://user@mirrors.nju.edu.cn/a'):
            with self.assertRaises(ValueError):
                network.check_url(url)

    def test_foreign_redirect_rejected_before_request(self):
        with self.assertRaises(ValueError):
            network.DomesticRedirect().redirect_request(None, None, 302, '', {}, 'https://github.com/a')

    def test_publisher_allowlist_limits_shared_services_to_expected_paths(self):
        expected = 'https://gitee.com/spark-store-project/spark-store/releases/download/5.3/spark-store_5.3_amd64.deb'
        self.assertEqual(network.check_url(expected), expected)
        for url in ('https://github.com/2dust/v2rayN/releases/download/7.0/package.deb',
                    'https://packages.microsoft.com/repos/code',
                    'https://gitee.com/spark-store-project/spark-store/releases/download/%2e%2e/other',
                    'https://ppa.launchpadcontent.net/xtradeb/apps/ubuntu'):
            with self.assertRaises(ValueError):
                network.check_url(url)

    def test_spark_release_follows_project_attachment_redirect(self):
        project = 'https://gitee.com/spark-store-project/spark-store'
        redirect = network.DomesticRedirect()
        for arch, attachment, cdn_id in (('amd64', 3665472, 1794399907207285376),
                                        ('arm64', 3665509, 1794400244717363352)):
            filename = f'spark-store_5.3.3_{arch}.deb'
            request = network.Request(f'{project}/releases/download/5.3.3/{filename}')
            url = f'{project}/attach_files/{attachment}/download/{filename}'
            chain = redirect.redirect_request(request, None, 302, '', {}, url)
            self.assertEqual(chain.full_url, url)
            cdn = f'https://foruda.gitee.com/attach_file/{cdn_id}/{filename}'
            self.assertEqual(redirect.redirect_request(chain, None, 302, '', {}, cdn).full_url, cdn)
        for url in (f'{project}/attach_files/3665472/download/another-package.deb',
                    'https://gitee.com/other/project/attach_files/3665472/download/spark-store_5.3.3_amd64.deb'):
            with self.assertRaises(ValueError):
                redirect.redirect_request(request, None, 302, '', {}, url)

    def test_html_is_not_an_installer(self):
        for kind in ('deb', 'zip', 'sh', 'xz'):
            with self.assertRaises(ValueError):
                network.validate(b'<html>error</html>', kind, 'text/html')

    def test_proxies_explicitly_disabled(self):
        with patch.object(network, 'ProxyHandler', wraps=network.ProxyHandler) as proxy, patch.object(network, 'build_opener') as opener:
            opener.return_value.open.return_value.geturl.return_value = 'https://mirrors.nju.edu.cn/a'
            network.open_direct('https://mirrors.nju.edu.cn/a')
            proxy.assert_called_once_with({})

    def test_truncated_download_preserves_previous_output(self):
        with temporary_directory() as tmp:
            output = Path(tmp) / 'installer.sh'
            output.write_bytes(b'old')
            with patch.object(network, 'open_direct', return_value=Response(b'#!/bin/sh\n', length=100)):
                with self.assertRaises(ValueError):
                    network.download('https://mirrors.nju.edu.cn/a', output, 'sh')
            self.assertEqual(output.read_bytes(), b'old')
            self.assertTrue(Path(str(output) + '.part').exists())

    def test_resume_checks_range_and_version(self):
        with temporary_directory() as tmp:
            output = Path(tmp) / 'installer.sh'
            first = Response(b'#!/bin/sh\n', length=15)
            first.headers['ETag'] = '"v1"'
            with patch.object(network, 'open_direct', return_value=first):
                with self.assertRaises(ValueError):
                    network.download('https://mirrors.nju.edu.cn/a', output, 'sh')
            rest = Response(b'hello', length=5)
            rest.status = 206
            rest.headers.update({'ETag': '"v1"', 'Content-Range': 'bytes 10-14/15'})
            with patch.object(network, 'open_direct', return_value=rest) as opener:
                network.download('https://mirrors.nju.edu.cn/a', output, 'sh')
                self.assertEqual(opener.call_args.kwargs['offset'], 10)
                self.assertEqual(opener.call_args.kwargs['validator'], '"v1"')
            self.assertEqual(output.read_bytes(), b'#!/bin/sh\nhello')

    def test_changed_resource_restarts_instead_of_appending(self):
        with temporary_directory() as tmp:
            output = Path(tmp) / 'installer.sh'
            first = Response(b'#!/bin/sh\n', length=100)
            first.headers['ETag'] = '"v1"'
            with patch.object(network, 'open_direct', return_value=first):
                with self.assertRaises(ValueError): network.download('https://mirrors.nju.edu.cn/a', output, 'sh')
            fresh = Response(b'#!/bin/sh\nnew-version')
            fresh.headers['ETag'] = '"v2"'
            with patch.object(network, 'open_direct', return_value=fresh):
                network.download('https://mirrors.nju.edu.cn/a', output, 'sh')
            self.assertEqual(output.read_bytes(), b'#!/bin/sh\nnew-version')

    def test_invalid_resume_range_is_rejected(self):
        with temporary_directory() as tmp:
            output = Path(tmp) / 'installer.sh'
            Path(str(output) + '.part').write_bytes(b'#!/bin/sh\n')
            Path(str(output) + '.json').write_text('{"url":"https://mirrors.nju.edu.cn/a","validator":"v1","size":15}')
            wrong = Response(b'hello')
            wrong.status = 206
            wrong.headers.update({'ETag': 'v1', 'Content-Range': 'bytes 0-4/15'})
            with patch.object(network, 'open_direct', return_value=wrong):
                with self.assertRaises(ValueError): network.download('https://mirrors.nju.edu.cn/a', output, 'sh')
            self.assertFalse(output.exists())

    def test_transient_outage_retries_mirror_probe(self):
        args = ['network.py', 'select', 'https://mirrors.nju.edu.cn/a']
        results = [[{'url': args[-1], 'ok': False, 'retryable': True}],
                   [{'url': args[-1], 'ok': True, 'bytes_per_second': 1}]]
        with patch('sys.argv', args), patch.object(network, 'rank', side_effect=results) as ranker, patch.object(network.time, 'sleep'):
            network.main()
        self.assertEqual(ranker.call_count, 2)

    def test_expiring_qq_signatures_share_file_identity_without_leaking_in_logs(self):
        base = 'https://qqdl.gtimg.cn/qqfile/QQNTV2/version/file.deb'
        a = base + '?sign=first&t=1'
        b = base + '?sign=second&t=2'
        self.assertEqual(network.resource_identity(a), network.resource_identity(b))
        self.assertNotIn('first', network.public_url(a))
        self.assertNotEqual(network.resource_identity(base + '?version=1&sign=a&t=1'),
                            network.resource_identity(base + '?version=2&sign=b&t=2'))

    def test_valid_download_replaces_output(self):
        with temporary_directory() as tmp:
            output = Path(tmp) / 'installer.sh'
            with patch.object(network, 'open_direct', return_value=Response(b'#!/bin/sh\n')):
                network.download('https://mirrors.nju.edu.cn/a', output, 'sh')
            self.assertEqual(output.read_bytes(), b'#!/bin/sh\n')

    def test_faster_valid_mirror_ranks_first(self):
        def probe(url, kind):
            return dict(url=url, ok=True, bytes_per_second=100 if url.endswith('fast') else 10, seconds=1)
        with patch.object(network, 'probe', side_effect=probe):
            result = network.rank(['https://mirrors.nju.edu.cn/slow', 'https://mirrors.nju.edu.cn/fast'], 'sh')
        self.assertTrue(result[0]['url'].endswith('fast'))


if __name__ == '__main__':
    unittest.main()
