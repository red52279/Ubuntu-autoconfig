"""Real socket disconnect/resume tests using only a local HTTP fixture."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import os
from pathlib import Path
import socket
from run_files import temporary_directory
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('network_fixture', ROOT / 'scripts/lib/network.py')
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


class SocketRecoveryTests(unittest.TestCase):
    def test_disconnect_then_real_range_resume_ignores_proxy(self):
        payload = b'#!/bin/sh\n' + b'x' * 150000
        ranges = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                requested = self.headers.get('Range')
                ranges.append(requested)
                if requested:
                    start = int(requested[6:-1])
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes {start}-{len(payload)-1}/{len(payload)}')
                    data = payload[start:]
                else:
                    self.send_response(200)
                    data = payload
                self.send_header('Content-Length', str(len(data)))
                self.send_header('ETag', '"version-one"')
                self.end_headers()
                if len(ranges) == 1:
                    self.wfile.write(data[:32768])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                else:
                    self.wfile.write(data)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}/installer.sh'
        original_check = network.check_url
        def fixture_check(candidate):
            return candidate if candidate == url else original_check(candidate)
        with temporary_directory() as tmp, patch.object(network, 'check_url', side_effect=fixture_check), patch.dict(os.environ, {'http_proxy': 'http://127.0.0.1:1', 'ALL_PROXY': 'socks5://127.0.0.1:1'}):
            output = Path(tmp) / 'installer.sh'
            cache = Path(tmp) / 'cache'
            with self.assertRaises(ValueError): network.download(url, output, 'sh', cache)
            self.assertFalse(output.exists())
            self.assertTrue(list(cache.glob('*.part')))
            network.download(url, output, 'sh', cache)
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(ranges, [None, 'bytes=32768-'])


if __name__ == '__main__': unittest.main()
