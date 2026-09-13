"""Synthetic transport tests: real Requests preparation, in-memory adapters only."""

import io
import json
import traceback
from unittest.mock import Mock

import pytest
import requests

from cmu_cli import web_session
from cmu_cli.canvas_client import CanvasClient, CanvasEndpointUnavailable, CanvasError
from cmu_cli.gradescope_client import GradescopeClient, GradescopeError
from cmu_cli.models import Course
from cmu_cli.piazza_client import PiazzaClient, PiazzaError
from cmu_cli.public_transport import PublicHTTPSAdapter

REAL_REQUEST = requests.Session.request
BASE = "https://canvas.example.invalid"


class MemoryAdapter(PublicHTTPSAdapter):
    def __init__(self, replies):
        self.replies = iter(replies)
        self.seen = []

    def send(self, request, **kwargs):
        self.seen.append(request)
        status, headers, body = next(self.replies)
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers)
        response.url = request.url
        response.request = request
        response.raw = io.BytesIO(body)
        return response

    def close(self):
        pass


def transport(monkeypatch, replies):
    # Restore request preparation only; both protocols use a non-network adapter.
    monkeypatch.setattr(requests.Session, "request", REAL_REQUEST)
    session = web_session.configured_session()
    adapter = MemoryAdapter(replies)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session, adapter


def payload(rows, link=None):
    headers = {"Content-Type": "application/json"}
    if link:
        headers["Link"] = f'<{link}>; rel="next"'
    return 200, headers, json.dumps(rows).encode()


@pytest.mark.parametrize(
    "target",
    [
        "http://canvas.example.invalid/x",
        "https://sibling.example.invalid/x",
        "https://canvas.example.invalid:444/x",
        "https://u:p@canvas.example.invalid/x",
    ],
)
def test_authenticated_redirect_never_sends_to_other_origin(monkeypatch, target):
    session, adapter = transport(monkeypatch, [(302, {"Location": target}, b"")])
    session.headers["Authorization"] = "Bearer SYNTHETIC"
    session.headers["CSRF-Token"] = "SYNTHETIC"
    session.cookies.set("session_id", "SYNTHETIC", domain=".example.invalid")
    with pytest.raises(CanvasError):
        CanvasClient(BASE, session).list_courses()
    assert len(adapter.seen) == 1


def test_same_origin_redirect_and_complete_pagination(monkeypatch):
    session, adapter = transport(
        monkeypatch,
        [
            (302, {"Location": "/next"}, b""),
            payload([{"id": 1}], "/page2"),
            payload([{"id": 2}], "/page3"),
            payload([{"id": 3}]),
        ],
    )
    assert CanvasClient(BASE, session).list_courses() == [
        {"id": 1},
        {"id": 2},
        {"id": 3},
    ]
    assert len(adapter.seen) == 4
    assert "enrollment_state" not in adapter.seen[-1].url


@pytest.mark.parametrize(
    "link",
    ["http://canvas.example.invalid/x", "https://evil.invalid/x", "/api/v1/courses"],
)
def test_pagination_rejects_origin_and_loops(monkeypatch, link):
    session, adapter = transport(monkeypatch, [payload([1], link)])
    with pytest.raises(CanvasError):
        CanvasClient(BASE, session).list_courses()
    assert len(adapter.seen) == 1


@pytest.mark.parametrize("status", [401, 403, 404])
@pytest.mark.parametrize("method", ["files", "quizzes"])
def test_unavailable_not_empty(monkeypatch, status, method):
    session, _ = transport(monkeypatch, [(status, {}, b"")])
    with pytest.raises(CanvasEndpointUnavailable) as error:
        getattr(CanvasClient(BASE, session), method)(1)
    assert error.value.status_code == status


def test_real_empty_collection(monkeypatch):
    session, _ = transport(monkeypatch, [payload([])])
    assert CanvasClient(BASE, session).files(1) == []


@pytest.mark.parametrize(
    "target", ["http://cdn.invalid/file", "https://user:pass@cdn.invalid/file"]
)
def test_anonymous_redirect_validated(monkeypatch, target):
    session, adapter = transport(monkeypatch, [(302, {"Location": target}, b"")])
    monkeypatch.setattr("cmu_cli.canvas_client.configured_session", lambda: session)
    with pytest.raises(CanvasError):
        CanvasClient(BASE, Mock()).download_bytes("https://public.invalid/file")
    assert len(adapter.seen) == 1


def test_anonymous_redirect_has_no_implicit_auth(monkeypatch):
    session, adapter = transport(
        monkeypatch,
        [(302, {"Location": "https://cdn.invalid/file"}, b""), (200, {}, b"synthetic")],
    )
    monkeypatch.setattr(
        requests.sessions, "get_netrc_auth", lambda *a, **k: pytest.fail("netrc read")
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    monkeypatch.setattr("cmu_cli.canvas_client.configured_session", lambda: session)
    assert (
        CanvasClient(BASE, Mock()).download_bytes("https://public.invalid/file")
        == b"synthetic"
    )
    assert all(
        "Authorization" not in r.headers and "Cookie" not in r.headers
        for r in adapter.seen
    )
    assert session.trust_env is False


@pytest.mark.parametrize(
    "headers,body,url",
    [
        ({"Content-Type": "text/html"}, b"login", "/file"),
        ({}, b"<!doctype html><html>login", "/file"),
        ({"Content-Type": "application/pdf"}, b"not a pdf", "/file"),
        ({}, b"not a pdf", "/file.pdf"),
        ({"Content-Length": "999999999"}, b"small", "/file"),
    ],
)
def test_download_checks(monkeypatch, headers, body, url):
    session, _ = transport(monkeypatch, [(200, headers, body)])
    with pytest.raises(CanvasError):
        CanvasClient(BASE, session).download_bytes(BASE + url)


def test_bounded_decompressed_read(monkeypatch):
    session, _ = transport(monkeypatch, [(200, {}, b"12345")])
    response = web_session.safe_request(session, BASE)
    with pytest.raises(web_session.SessionError, match="size limit"):
        web_session.bounded_content(response, 4)
    assert response.raw.closed


def test_exception_traceback_redacted():
    session = Mock()
    session.get.side_effect = requests.RequestException("SYNTHETIC-SECRET")
    with pytest.raises(CanvasError) as error:
        CanvasClient(BASE, session).list_courses()
    assert "SYNTHETIC-SECRET" not in "".join(traceback.format_exception(error.value))


def test_legacy_environment_is_not_explicit_config_consent(monkeypatch):
    monkeypatch.setenv("CMU_CLI_ALLOW_BROWSER_COOKIES", "1")
    monkeypatch.setenv("CMU_CLI_COOKIE_HOSTS", "piazza.com")
    with pytest.raises(web_session.SessionError, match="opt-in"):
        web_session.edge_cookie_session("piazza.com")
    fake = Mock()
    with pytest.raises(PiazzaError, match="read-only"):
        PiazzaClient(fake).call("user.status", {})
    with pytest.raises(GradescopeError, match="explicit"):
        GradescopeClient(fake).assignments(Course("DEMO", 1, "Demo", "demo"))
    fake.assert_not_called()
    assert not fake.method_calls


def test_piazza_offline_exact_network_selection():
    a = Course("CS-101", 1, "CS", "cs", piazza_url="https://piazza.com/class/alpha")
    b = Course(
        "MATH-101", 2, "Math", "math", piazza_url="https://piazza.com/class/beta"
    )
    client = PiazzaClient(courses=(a, b))
    networks = [{"id": "alpha", "course_number": "MATH-101"}, {"id": "beta"}]

    class SelectedFeeds(dict):
        def get(self, key, default=None):
            assert key == "alpha"
            return [{"id": "p1", "subject": "Synthetic"}]

    assert (
        client.parse_posts(networks, SelectedFeeds(), course="CS-101")[0]["course"]
        == "CS-101"
    )
    assert (
        client.normalized_course_code({"id": "other", "course_number": "CS-101"})
        is None
    )
    with pytest.raises(PiazzaError):
        client.parse_posts(networks, {}, course="101")


def test_gradescope_offline_parser():
    course = Course(
        "DEMO", 1, "Demo", "demo", gradescope_url="https://www.gradescope.com/courses/1"
    )
    html = '<table id="assignments-student-table"><tbody><tr><th scope="row" data-assignment-id="2">Synthetic</th><td><span class="submissionStatus--text">Not Submitted</span><time class="submissionTimeChart--dueDate" datetime="2030-01-01T00:00:00Z"></time></td></tr></tbody></table>'
    rows = GradescopeClient.parse_assignments(course, html)
    assert rows[0]["status"] == "Not Submitted"
    assert rows[0]["due_at"] == "2030-01-01T00:00:00Z"


def test_redirect_body_is_never_eagerly_read(monkeypatch):
    session, adapter = transport(
        monkeypatch, [(302, {"Location": "/next"}, b""), payload([])]
    )
    original_send = adapter.send

    class Unreadable(io.BytesIO):
        def read(self, *args, **kwargs):
            raise AssertionError("Redirect body must not be read")

    def send(request, **kwargs):
        response = original_send(request, **kwargs)
        if response.status_code == 302:
            response.raw = Unreadable()
        return response

    adapter.send = send
    assert CanvasClient(BASE, session).list_courses() == []


def test_public_adapters_use_isolated_transport(monkeypatch):
    from cmu_cli.official_materials import public_materials
    from cmu_cli.official_quizzes import public_quizzes

    html = b'<table><tr><td>Sep 3</td><td>Quiz 1</td><td><a href="slides/03-example.pdf">Slides</a></td></tr></table>'
    session, adapter = transport(
        monkeypatch,
        [
            (200, {"Content-Type": "text/html"}, html),
            (200, {"Content-Type": "text/html"}, html),
            (200, {"Content-Type": "application/pdf"}, b""),
        ],
    )
    monkeypatch.setattr(web_session, "configured_session", lambda: session)
    monkeypatch.setattr(
        "cmu_cli.official_materials.configured_session", lambda: session
    )
    monkeypatch.setattr(
        requests.sessions, "get_netrc_auth", lambda *a, **k: pytest.fail("netrc read")
    )
    assert public_quizzes("DEMO", BASE, 2030)[0]["name"] == "Quiz 1"
    assert public_materials("DEMO", BASE)[0]["filename"] == "03-example.pdf"
    assert len(adapter.seen) == 3
    assert all("Authorization" not in r.headers for r in adapter.seen)
