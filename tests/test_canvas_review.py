"""Independent review regressions, prepared requests and socket boundaries."""

import io
import json
import socket
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter
from urllib3.exceptions import NewConnectionError

from cmu_cli import cli, public_transport
from cmu_cli.canvas_client import CanvasClient
from cmu_cli.public_transport import PublicHTTPSAdapter, PublicHTTPSConnection
from cmu_cli.web_session import safe_request

REAL_REQUEST = requests.Session.request


class APIAdapter(BaseAdapter):
    def __init__(self, missing=False, denied=None):
        self.calls = []
        self.missing = missing
        self.denied = denied

    def send(self, request, **kwargs):
        self.calls.append(request)
        path = urlsplit(request.url).path
        status = self.denied if path.endswith("enrollments") and self.denied else 200
        if path.endswith("profile"):
            payload = {"id": 8}
        elif path.endswith("assignments"):
            payload = [{"id": 1, "course_id": 2, "name": "Zero", "points_possible": 10}]
        elif path.endswith("submissions"):
            payload = (
                [] if self.missing else [{"assignment_id": 1, "user_id": 8, "score": 0}]
            )
        else:
            payload = []
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.headers["Content-Type"] = "application/json"
        response.raw = io.BytesIO(json.dumps(payload).encode())
        response.request = request
        return response

    def close(self):
        pass


@pytest.mark.parametrize(
    "missing,denied",
    [(False, None), (True, None), (False, 401), (False, 403), (False, 404)],
)
def test_real_dispatch_and_prepared_transport(
    tmp_path, monkeypatch, capsys, missing, denied
):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "canvas_base_url": "https://canvas.example.invalid",
                "storage_root": str(tmp_path / "unused"),
                "courses": [
                    {"code": "A", "name": "A", "directory": "a", "canvas_id": 2}
                ],
            }
        )
    )
    monkeypatch.setattr(requests.Session, "request", REAL_REQUEST)
    transport = APIAdapter(missing, denied)
    session = requests.Session()
    session.headers["Authorization"] = "Bearer synthetic"
    session.mount("https://", transport)
    monkeypatch.setattr(
        cli, "CanvasClient", lambda *a, **kw: CanvasClient(a[0], session=session)
    )
    monkeypatch.setattr(
        "sys.argv",
        ["cmu-cli", "--config", str(config), "grades", "--details", "--json"],
    )
    with pytest.raises(SystemExit) as result:
        cli.main()
    envelope = json.loads(capsys.readouterr().out)
    assert result.value.code == (3 if missing or denied else 0)
    assert envelope["complete"] == (not missing and not denied)
    assert envelope["data"]["assignments"][0]["score"] == (None if missing else 0)
    if missing:
        assert envelope["warnings"][0]["code"] == "SUBMISSIONS_NOT_RETURNED"
    if denied:
        assert (
            envelope["warnings"][0]["unavailable_reason"]
            == {
                401: "authentication_required",
                403: "permission_denied",
                404: "not_found",
            }[denied]
        )
    assert len(transport.calls) == 4
    assert all(
        r.method == "GET" and r.headers["Authorization"] == "Bearer synthetic"
        for r in transport.calls
    )
    query = parse_qs(urlsplit(transport.calls[2].url).query)
    assert query == {
        "per_page": ["100"],
        "include[]": ["submission_comments", "rubric_assessment"],
    }
    assert not any(
        "read_status" in r.url or "student_ids" in r.url for r in transport.calls
    )
    assert parse_qs(urlsplit(transport.calls[3].url).query)["state[]"] == [
        "current_and_concluded"
    ]
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
        "0.0.0.0",
    ],
)
def test_private_socket_never_connected(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
    )
    monkeypatch.setattr(
        public_transport,
        "create_connection",
        lambda *a, **kw: pytest.fail("private socket attempted"),
    )
    with pytest.raises(NewConnectionError):
        PublicHTTPSConnection("files.example.invalid")._new_conn()


def test_dns_pin_preserves_tls_hostname(monkeypatch):
    resolved = []
    connected = []

    def resolve(*a, **kw):
        resolved.append(a)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        public_transport,
        "create_connection",
        lambda address, *a, **kw: connected.append(address) or "socket",
    )
    connection = PublicHTTPSConnection("files.example.invalid")
    assert connection._new_conn() == "socket"
    assert connected == [("8.8.8.8", 443)] and len(resolved) == 1
    assert connection.host == "files.example.invalid"


def test_anonymous_prepared_hops_strip_all_credentials(monkeypatch):
    monkeypatch.setattr(requests.Session, "request", REAL_REQUEST)
    calls = []

    def send(self, request, **kwargs):
        calls.append(request)
        response = requests.Response()
        response.status_code = 302 if len(calls) == 1 else 200
        response.url = request.url
        response.raw = io.BytesIO(b"")
        response.request = request
        response.headers["Location"] = "https://second.example.invalid/file"
        return response

    monkeypatch.setattr(PublicHTTPSAdapter, "send", send)
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": "secret",
            "Cookie": "secret",
            "Referer": "secret",
            "X-Api-Key": "secret",
        }
    )
    session.params = {"token": "secret"}
    session.auth = ("secret", "secret")
    safe_request(session, "https://files.example.invalid/file", anonymous=True).close()
    assert len(calls) == 2
    assert all("secret" not in str(dict(r.headers)) + r.url for r in calls)
    assert isinstance(session.get_adapter("https://"), PublicHTTPSAdapter)


def test_profile_failure_prevents_further_reads(tmp_path, capsys):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from cmu_cli.canvas_client import CanvasEndpointUnavailable
    from cmu_cli.canvas_grades import command_grades
    from cmu_cli.models import Config, Course

    client = Mock()
    client.get.side_effect = CanvasEndpointUnavailable(403)
    cli._CONTEXT.update(command="grades", warnings=[], sources=[])
    assert (
        command_grades(
            SimpleNamespace(course=None, details=True, json=True),
            Config(
                "https://canvas.example.invalid", tmp_path, (Course("A", 2, "A", "a"),)
            ),
            client,
        )
        == 3
    )
    assert client.get.call_count == 1
    client.assignments.assert_not_called()
    assert (
        json.loads(capsys.readouterr().out)["sources"][0]["unavailable_reason"]
        == "permission_denied"
    )


@pytest.mark.parametrize("rich", [False, True])
def test_details_visible_in_human_output(tmp_path, capsys, monkeypatch, rich):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from cmu_cli.canvas_grades import command_grades
    from cmu_cli.models import Config, Course

    monkeypatch.setenv("CLICOLOR_FORCE", "1" if rich else "0")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    from cmu_cli import style

    style.console.cache_clear()
    client = Mock()
    client.get.side_effect = [
        {"id": 8},
        [
            {
                "assignment_id": 1,
                "user_id": 8,
                "submission_comments": [{"comment": "Synthetic feedback"}],
                "rubric_assessment": {"criterion": {"points": 0}},
            }
        ],
        [],
    ]
    client.assignments.return_value = [{"id": 1, "name": "Zero"}]
    cli._CONTEXT.update(command="grades", warnings=[], sources=[])
    assert (
        command_grades(
            SimpleNamespace(course=None, details=True, json=False),
            Config(
                "https://canvas.example.invalid", tmp_path, (Course("A", 2, "A", "a"),)
            ),
            client,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Synthetic feedback" in output and '"points": 0' in output
