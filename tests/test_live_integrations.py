"""Enabled integrations exercised only against synthetic responses/loaders."""

import json
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from cmu_cli.gradescope_client import GradescopeClient, GradescopeError
from cmu_cli.models import Course
from cmu_cli.piazza_client import PiazzaClient, PiazzaError
from cmu_cli.web_session import SessionError, browser_cookie_session, canvas_api_session
from tests.test_network_security import payload, transport

A = Course(
    "CS-101",
    1,
    "CS",
    "cs",
    piazza_url="https://piazza.com/class/alpha",
    gradescope_url="https://www.gradescope.com/courses/1",
)
B = Course("MATH-101", 2, "Math", "math", piazza_url="https://piazza.com/class/beta")


def piazza_session(monkeypatch, replies):
    session, adapter = transport(monkeypatch, replies)
    session.cookies.set("session_id", "SYNTHETIC_SESSION", domain="piazza.com")
    return session, adapter


def test_live_piazza_filtered_pagination_csrf(monkeypatch):
    session, adapter = piazza_session(
        monkeypatch,
        [
            payload({"result": {"feed": [{"id": "p1"}], "more": True}}),
            payload({"result": {"feed": [{"id": "p2"}], "more": False}}),
        ],
    )
    rows = PiazzaClient(session, (A, B)).open_class_posts("CS-101")
    assert len(rows) == 2 and all(r["course"] == "CS-101" for r in rows)
    bodies = [json.loads(req.body) for req in adapter.seen]
    assert [b["params"]["offset"] for b in bodies] == [0, 100]
    assert all(b["params"]["nid"] == "alpha" for b in bodies)
    assert all(r.headers["CSRF-Token"] == "SYNTHETIC_SESSION" for r in adapter.seen)
    assert not session.trust_env


@pytest.mark.parametrize("provider", ["piazza", "gradescope"])
@pytest.mark.parametrize(
    "target",
    ["https://evil.invalid/collect", "http://piazza.com/x", "https://piazza.com:444/x"],
)
def test_live_provider_redirect_exfiltration_blocked(monkeypatch, provider, target):
    session, adapter = piazza_session(monkeypatch, [(307, {"Location": target}, b"")])
    session.headers["Authorization"] = "Bearer SYNTHETIC"
    with pytest.raises((PiazzaError, GradescopeError)):
        if provider == "piazza":
            PiazzaClient(session, (A,)).open_class_posts()
        else:
            GradescopeClient(session).assignments(A)
    assert len(adapter.seen) == 1


def test_live_gradescope_parser(monkeypatch):
    document = b'<table id="assignments-student-table"><tbody><tr><th scope="row">Homework</th><td><span class="submissionStatus--text">Not Submitted</span></td></tr></tbody></table>'
    session, adapter = transport(
        monkeypatch, [(200, {"Content-Type": "text/html"}, document)]
    )
    assert GradescopeClient(session).assignments(A)[0]["name"] == "Homework"
    assert adapter.seen[0].url == A.gradescope_url


def test_pagination_repeated_not_complete(monkeypatch):
    response = payload({"result": {"feed": [{"id": "p1"}], "more": True}})
    session, _ = piazza_session(monkeypatch, [response, response])
    with pytest.raises(PiazzaError, match="repeated"):
        PiazzaClient(session, (A,)).open_class_posts()


@pytest.mark.parametrize("browser", ["edge", "chrome"])
def test_explicit_loader_and_canvas_fallback(monkeypatch, browser):
    jar = requests.cookies.RequestsCookieJar()
    jar.set("session_id", "SYNTHETIC", domain=".example.invalid")
    loader = Mock(return_value=jar)
    monkeypatch.setitem(
        sys.modules, "browser_cookie3", SimpleNamespace(**{browser: loader})
    )
    opts = {
        "enabled": True,
        "browser": browser,
        "cookie_file": "/synthetic/profile/Cookies",
        "hosts": ["canvas.example.invalid"],
    }
    session, method = canvas_api_session("https://canvas.example.invalid", opts)
    assert method == "browser_cookie"
    assert {c.domain for c in session.cookies} == {"canvas.example.invalid"}
    loader.assert_called_once_with(
        cookie_file=opts["cookie_file"], domain_name="canvas.example.invalid"
    )
    monkeypatch.setenv("CMU_CLI_CANVAS_TOKEN", "SYNTHETIC")
    assert (
        canvas_api_session("https://canvas.example.invalid", opts)[1]
        == "environment_token"
    )
    assert loader.call_count == 1


def test_no_opt_in_or_host_consent_never_loads(monkeypatch):
    loader = Mock(side_effect=AssertionError("must not read"))
    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(edge=loader))
    for options in [
        None,
        {},
        {"enabled": False},
        {"enabled": True, "hosts": ["notpiazza.com"]},
    ]:
        with pytest.raises(SessionError):
            browser_cookie_session("piazza.com", options)
    loader.assert_not_called()


def test_loader_diagnostics_redacted(monkeypatch):
    loader = Mock(side_effect=RuntimeError("SYNTHETIC_SECRET"))
    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(edge=loader))
    with pytest.raises(SessionError) as error:
        browser_cookie_session(
            "piazza.com",
            {
                "enabled": True,
                "hosts": ["piazza.com"],
                "cookie_file": "/synthetic/Cookies",
            },
        )
    assert "SYNTHETIC_SECRET" not in "".join(traceback.format_exception(error.value))
