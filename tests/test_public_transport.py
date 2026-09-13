"""Public adapter integration: real Requests/urllib3, blocked before socket."""

import socket

import pytest
import requests

from cmu_cli import public_transport
from cmu_cli.web_session import SessionError, safe_request

REAL_REQUEST = requests.Session.request


def test_public_request_rejects_dns_private_before_connect(monkeypatch):
    monkeypatch.setattr(requests.Session, "request", REAL_REQUEST)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    monkeypatch.setattr(
        public_transport,
        "create_connection",
        lambda *a, **kw: pytest.fail("private connect"),
    )
    with (
        requests.Session() as session,
        pytest.raises(SessionError, match="transport failed"),
    ):
        safe_request(session, "https://files.example.invalid/file", anonymous=True)


def test_mixed_public_private_dns_fails_closed(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
            for ip in ("8.8.8.8", "10.0.0.1")
        ],
    )
    monkeypatch.setattr(
        public_transport,
        "create_connection",
        lambda *a, **kw: pytest.fail("mixed DNS connect"),
    )
    from urllib3.exceptions import NewConnectionError

    with pytest.raises(NewConnectionError):
        public_transport.PublicHTTPSConnection("files.example.invalid")._new_conn()
