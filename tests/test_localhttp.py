"""The local GET: a body from the service, None when nothing answers, and
no host proxy in between."""

from __future__ import annotations

import http.server
import os
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from latentcrate import localhttp
from tests.support import free_port

# A proxy nothing answers on: a request routed through it fails.
UNREACHABLE_PROXY = {
    "HTTP_PROXY": "http://127.0.0.1:9",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "http_proxy": "http://127.0.0.1:9",
    "https_proxy": "http://127.0.0.1:9",
    "NO_PROXY": "",
    "no_proxy": "",
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'{"status": "ok"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


class LocalHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/system_stats"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_a_body_comes_back_and_a_refused_connection_is_none(self) -> None:
        self.assertEqual(localhttp.http_get(self.url), '{"status": "ok"}')
        self.assertIsNone(localhttp.http_get(f"http://127.0.0.1:{free_port()}/system_stats", timeout=1))

    def test_host_proxy_settings_do_not_apply(self) -> None:
        with patch.dict(os.environ, UNREACHABLE_PROXY):
            self.assertEqual(localhttp.http_get(self.url), '{"status": "ok"}')
            # The default opener would route the same request through the proxy.
            with self.assertRaises(urllib.error.URLError):
                urllib.request.build_opener().open(self.url, timeout=1)


if __name__ == "__main__":
    unittest.main()
