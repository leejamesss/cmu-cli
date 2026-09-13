"""Inspect prepared wire requests without sockets, credentials, or live accounts."""

import io

import requests

from cmu_cli.canvas_client import CanvasClient
from cmu_cli.public_transport import PublicHTTPSAdapter
from tests.test_ed_client import synthetic_session


class DownloadTransport(PublicHTTPSAdapter):
    def __init__(self, replies):
        self.replies = iter(replies)
        self.sent = []

    def send(self, request, **kwargs):
        self.sent.append(request)
        status, headers, body = next(self.replies)
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers)
        response.raw = io.BytesIO(body)
        response.url = request.url
        return response

    def close(self):
        pass


def test_canvas_signed_redirect_scrubs_prepared_headers_and_cookies(monkeypatch):
    authed, anon = synthetic_session(), synthetic_session()
    signed = "https://files.example.invalid/file?synthetic-signature=yes"
    first = DownloadTransport([(302, {"Location": signed}, b"")])
    second = DownloadTransport(
        [
            (
                302,
                {
                    "Location": "https://cdn.example.invalid/file",
                    "Set-Cookie": "injected=secret; Path=/",
                },
                b"",
            ),
            (200, {"Content-Type": "application/pdf"}, b"%PDF-synthetic"),
        ]
    )
    authed.mount("https://", first)
    anon.mount("https://", second)
    authed.headers["Authorization"] = "Bearer synthetic-token"
    authed.cookies.set("session", "synthetic-cookie", domain="canvas.example.invalid")
    anon.headers["Authorization"] = "Bearer injected-token"
    anon.headers["Cookie"] = "injected=header"
    anon.cookies.set("injected", "jar-cookie", domain="files.example.invalid")
    monkeypatch.setattr("cmu_cli.canvas_client.configured_session", lambda: anon)
    assert (
        CanvasClient("https://canvas.example.invalid", authed).download_bytes(
            "https://canvas.example.invalid/files/1"
        )
        == b"%PDF-synthetic"
    )
    assert len(first.sent) == 1 and len(second.sent) == 2
    assert first.sent[0].headers["Authorization"] == "Bearer synthetic-token"
    assert "synthetic-cookie" in first.sent[0].headers["Cookie"]
    for request in second.sent:
        assert "Authorization" not in request.headers
        assert "Cookie" not in request.headers
