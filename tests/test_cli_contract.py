import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cmu_cli import cli
from cmu_cli.models import Config, Course, load_config


@pytest.fixture(autouse=True)
def context():
    cli._CONTEXT.update(command="test", warnings=[], sources=[])


@pytest.mark.parametrize(
    "status,expected",
    [
        ("No Submission", False),
        ("Not Submitted", False),
        ("Submitted", True),
        ("unsubmitted", False),
        ("Unknown", None),
        (None, None),
    ],
)
def test_submission_state(status, expected):
    assert cli.submission_state(status) is expected


def test_date_normalization():
    assert cli.timestamp("2030-01-01T01:00:00+01:00") == "2030-01-01T00:00:00Z"
    assert cli.timestamp("2030-01-01 01:00:00") is None
    assert cli.timestamp("invalid") is None


def test_quiz_deadlines_and_id():
    client = Mock()
    client.quizzes.return_value = [
        {
            "id": 4,
            "title": "quiz",
            "due_at": "2030-01-02T00:00:00Z",
            "unlock_at": "2030-01-01T00:00:00Z",
            "lock_at": "2030-01-03T00:00:00Z",
        }
    ]
    row = cli.quiz_rows(client, [Course("A", 1, "A", "a")])[0]
    assert row["id"] == "4"
    assert row["starts_at"] == row["unlock_at"] != row["due_at"]
    assert row["lock_at"] == "2030-01-03T00:00:00Z"


def test_partial_envelope(capsys):
    assert (
        cli.fetch("canvas.files", "A", Mock(side_effect=RuntimeError("secret"))) == []
    )
    cli.print_json([])
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "1.0"
    assert result["status"] == "partial" and not result["complete"]
    assert result["sources"][0]["status"] == "error"
    assert "secret" not in json.dumps(result)


def test_limit_is_explicit():
    assert cli.parser().parse_args(["materials"]).limit is None
    assert cli.limited(list(range(40)), None) == list(range(40))
    assert cli.limited(list(range(40)), 1) == [0]
    assert cli._CONTEXT["warnings"][0]["code"] == "TRUNCATED"


def test_error_envelope(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cmu-cli", "courses", "--json"])
    monkeypatch.setattr(
        cli, "load_config", Mock(side_effect=FileNotFoundError("secret"))
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error" and result["data"] is None
    assert result["error"]["code"] == "CONFIG_NOT_FOUND"
    assert "secret" not in json.dumps(result)


def test_init_and_validate_offline(tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.json"
    monkeypatch.setattr(
        sys, "argv", ["cmu-cli", "config", "init", "--output", str(path), "--json"]
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert load_config(path).courses[0].canvas_id == 1
    monkeypatch.setattr(
        cli, "CanvasClient", Mock(side_effect=AssertionError("must not authenticate"))
    )
    monkeypatch.setattr(
        sys, "argv", ["cmu-cli", "--config", str(path), "doctor", "--json"]
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["data"]["network_used"] is False


def test_open_without_auth(monkeypatch):
    browser = Mock()
    monkeypatch.setattr(cli, "EdgeBrowser", lambda: browser)
    cli.command_open(
        SimpleNamespace(course=None, platform="canvas"),
        Config("https://canvas.example.invalid", None, ()),
        None,
    )
    browser.open.assert_called_once_with("https://canvas.example.invalid")


def test_status_failure_not_success(capsys):
    client = Mock(auth_method="injected")
    client.list_courses.return_value = [{"id": 1}]
    client.tabs.side_effect = cli.CanvasError("secret")
    code = cli.command_status(
        SimpleNamespace(json=True),
        Config("https://canvas.example.invalid", None, (Course("A", 1, "A", "a"),)),
        client,
    )
    assert code == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "partial"
    assert "secret" not in json.dumps(result)
