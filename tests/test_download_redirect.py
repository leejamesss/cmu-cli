"""Canvas file downloads redirect to pre-signed storage on another host.

The origin pin refused those outright, which enforced the credential boundary by
making downloads impossible. Handing off to an unauthenticated request enforces the
same boundary and works. The tests that matter here are the ones asserting the
credentials do not travel.
"""

import pytest
import requests

from cmu_cli.canvas_client import CanvasClient
from cmu_cli.web_session import CrossOriginRedirect, SessionError, safe_request

CANVAS = "https://canvas.example.invalid"
SIGNED = "https://files.example.invalid/f.pdf?X-Amz-Signature=abc"


class FakeResponse:
    def __init__(self, status, headers=None, content=b"", url=""):
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.raw = None
        self.url = url

    def iter_content(self, chunk_size=8192):
        yield self.content

    def close(self):
        pass


class RecordingSession(requests.Session):
    """Records the headers each hop actually went out with."""

    def __init__(self, script):
        super().__init__()
        self.script = script
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, "headers": dict(self.headers)})
        return self.script.pop(0)


def test_cross_origin_redirect_carries_its_destination():
    session = RecordingSession([FakeResponse(302, {"Location": SIGNED})])
    with pytest.raises(CrossOriginRedirect) as caught:
        safe_request(
            session,
            f"{CANVAS}/files/1",
            origin=("https", "canvas.example.invalid", 443),
        )
    assert caught.value.location == SIGNED
    # Still a SessionError, so existing handlers and tests are unaffected.
    assert isinstance(caught.value, SessionError)


def test_credentials_do_not_follow_the_redirect(monkeypatch):
    """The point of the origin pin. The signed URL must be fetched anonymously."""
    authed = RecordingSession([FakeResponse(302, {"Location": SIGNED})])
    authed.headers["Authorization"] = "Bearer secret-token"
    authed.cookies.set("session", "secret-cookie", domain="canvas.example.invalid")

    anon = RecordingSession(
        [
            FakeResponse(
                200,
                {"Content-Type": "application/pdf", "Content-Length": "5"},
                b"%PDF-",
            )
        ]
    )
    monkeypatch.setattr("cmu_cli.canvas_client.configured_session", lambda: anon)

    client = CanvasClient(CANVAS, authed)
    assert client.download_bytes(f"{CANVAS}/files/1") == b"%PDF-"

    assert anon.calls, "the signed URL was never fetched"
    for call in anon.calls:
        assert call["url"] == SIGNED
        assert "Authorization" not in call["headers"]
        assert "Cookie" not in call["headers"]
    assert not anon.cookies


def test_same_origin_download_still_uses_the_authenticated_session():
    session = RecordingSession(
        [
            FakeResponse(
                200,
                {"Content-Type": "application/pdf", "Content-Length": "5"},
                b"%PDF-",
            )
        ]
    )
    session.headers["Authorization"] = "Bearer secret-token"
    client = CanvasClient(CANVAS, session)
    assert client.download_bytes(f"{CANVAS}/files/1") == b"%PDF-"
    assert session.calls[0]["headers"].get("Authorization") == "Bearer secret-token"


def test_a_redirect_to_a_login_page_is_still_refused(monkeypatch):
    """Handing off must not turn a download into a silent fetch of an HTML login."""
    authed = RecordingSession(
        [FakeResponse(302, {"Location": "https://login.example.invalid/x"})]
    )
    anon = RecordingSession(
        [FakeResponse(200, {"Content-Type": "text/html"}, b"<!doctype html><form>")]
    )
    monkeypatch.setattr("cmu_cli.canvas_client.configured_session", lambda: anon)
    client = CanvasClient(CANVAS, authed)
    with pytest.raises(Exception) as caught:
        client.download_bytes(f"{CANVAS}/files/1")
    assert "HTML" in str(caught.value)
