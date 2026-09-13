import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from cmu_cli import cli, web_session
from cmu_cli.canvas_client import CanvasClient, CanvasError
from cmu_cli.demo import run_demo
from cmu_cli.models import Config, Course, load_config
from cmu_cli.storage import sync_canvas_file


def test_demo_runs_real_storage_without_auth():
    result = run_demo()
    assert result["statuses"] == ["downloaded", "unchanged"]
    assert result["synthetic"] and not result["network_used"]


def test_config_paths_and_selection(tmp_path, monkeypatch):
    p = tmp_path / "custom.json"
    p.write_text(
        json.dumps(
            {
                "canvas_base_url": "https://canvas.example.invalid",
                "storage_root": "files",
                "courses": [
                    {
                        "code": "DEMO-101",
                        "canvas_id": 1,
                        "name": "Example",
                        "directory": "demo",
                    }
                ],
                "term": "Test",
            }
        )
    )
    monkeypatch.setenv("CMU_CLI_CONFIG", str(p))
    config = load_config()
    assert config.storage_root == tmp_path / "files"
    assert config.find_course("demo101").canvas_id == 1
    assert config.find_course("example").code == "DEMO-101"
    with pytest.raises(ValueError):
        config.find_course("missing")
    raw = json.loads(p.read_text())
    raw["courses"][0]["directory"] = "../escape"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_config(p)


def test_cookies_are_opt_in(monkeypatch):
    with pytest.raises(web_session.SessionError, match="opt-in"):
        web_session.edge_cookie_session("piazza.com")
    monkeypatch.setenv("CMU_CLI_ALLOW_BROWSER_COOKIES", "1")
    with pytest.raises(web_session.SessionError, match="opt-in"):
        web_session.edge_cookie_session("piazza.com")


def test_cookie_scope_and_no_value_output(tmp_path, monkeypatch, capsys):
    p = tmp_path / "synthetic.db"
    p.touch()
    monkeypatch.setenv("CMU_CLI_ALLOW_BROWSER_COOKIES", "1")
    monkeypatch.setenv("CMU_CLI_COOKIE_HOSTS", "piazza.com")
    monkeypatch.setenv("CMU_CLI_EDGE_COOKIE_FILE", str(p))
    jar = requests.cookies.RequestsCookieJar()
    for domain in [".piazza.com", "notpiazza.com", "other.example.invalid"]:
        jar.set("session_id", "synthetic-marker", domain=domain)
    loader = Mock(return_value=jar)
    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(edge=loader))
    with pytest.raises(web_session.SessionError, match="opt-in"):
        web_session.edge_cookie_session("piazza.com")
    loader.assert_not_called()
    session = web_session.browser_cookie_session(
        "piazza.com",
        {
            "enabled": True,
            "browser": "edge",
            "cookie_file": str(p),
            "hosts": ["piazza.com"],
        },
    )
    loader.assert_called_once_with(cookie_file=str(p), domain_name="piazza.com")
    assert {c.domain for c in session.cookies} == {"piazza.com"}
    assert all(c.secure for c in session.cookies)
    assert not capsys.readouterr().out


def test_environment_token_never_probes_browser(monkeypatch, capsys):
    monkeypatch.setenv("CMU_CLI_CANVAS_TOKEN", "synthetic-marker")
    session, method = web_session.canvas_api_session("https://canvas.example.invalid")
    assert method == "environment_token"
    assert session.headers["Authorization"].endswith("synthetic-marker")
    assert not capsys.readouterr().out


def test_api_exception_does_not_echo_server_or_credentials():
    session = Mock()
    session.get.side_effect = requests.RequestException("synthetic-private-marker")
    client = CanvasClient("https://canvas.example.invalid", session=session)
    with pytest.raises(CanvasError) as exc:
        client.list_courses()
    assert "synthetic-private-marker" not in str(exc.value)
    session.get.side_effect = None
    session.get.return_value = SimpleNamespace(
        status_code=500,
        url="https://canvas.example.invalid",
        text="synthetic-private-marker",
    )
    with pytest.raises(CanvasError) as exc:
        client.list_courses()
    assert "synthetic-private-marker" not in str(exc.value)


def test_api_rejects_cross_origin():
    session = Mock()
    client = CanvasClient("https://canvas.example.invalid", session=session)
    with pytest.raises(CanvasError):
        client.get("https://other.example.invalid/api")
    session.get.assert_not_called()


def test_public_download_never_uses_token_session(monkeypatch):
    authenticated = Mock()
    from tests.test_network_security import transport

    anonymous, adapter = transport(monkeypatch, [(200, {}, b"synthetic")])
    monkeypatch.setattr("cmu_cli.canvas_client.configured_session", lambda: anonymous)
    client = CanvasClient("https://canvas.example.invalid", session=authenticated)
    assert client.download_bytes("https://public.example.invalid/file") == b"synthetic"
    authenticated.get.assert_not_called()
    assert len(adapter.seen) == 1
    assert "Authorization" not in adapter.seen[0].headers
    assert "Cookie" not in adapter.seen[0].headers


@pytest.mark.parametrize("path", ["../outside.txt", "/outside.txt"])
def test_manifest_cannot_escape(tmp_path, path):
    manifest = {"1": {"relative_path": path}}
    with pytest.raises(ValueError):
        sync_canvas_file(
            {"id": 1, "display_name": "hw1.txt"}, tmp_path, manifest, Mock()
        )


def test_assignments_canvas_only_needs_no_browser():
    course = Course("DEMO-101", 1, "Example", "demo")
    client = Mock()
    client.assignments.return_value = [
        {"name": "Synthetic Task", "due_at": None, "submission": {}}
    ]
    rows = cli.assignment_rows(client, [course])
    assert rows[0]["source"] == "canvas"


def test_cli_failure_redacts_message(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cmu-cli", "courses", "--json"])
    monkeypatch.setattr(
        cli, "load_config", lambda _: Config("https://canvas.example.invalid", None, ())
    )
    monkeypatch.setattr(cli, "CanvasClient", lambda _: Mock())
    monkeypatch.setattr(
        cli,
        "command_courses",
        Mock(side_effect=RuntimeError("synthetic-private-marker")),
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "synthetic-private-marker" not in output.err
    result = json.loads(output.out)
    from tests.test_final_integration import VALIDATOR

    VALIDATOR.validate(result)
    assert result["status"] == "error" and result["complete"] is False
    assert result["data"] is None
    assert result["error"]["code"] == "CONFIG_OR_OPERATION_FAILED"
    assert "synthetic-private-marker" not in output.out


def test_quiz_public_parser_configurable(monkeypatch):
    from cmu_cli.official_quizzes import public_quizzes

    response = Mock(text="<table><tr><td>Sep 3</td><td>Quiz 1</td></tr></table>")
    monkeypatch.setattr(
        "cmu_cli.official_quizzes.public_document",
        lambda *a, **k: response.text.encode(),
    )
    rows = public_quizzes("DEMO-101", "https://courses.example.invalid", 2030, 9)
    assert rows[0]["starts_at"].startswith("2030-09-03T09:00")
    assert public_quizzes("DEMO-101") == []
