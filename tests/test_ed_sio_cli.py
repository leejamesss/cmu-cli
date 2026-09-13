"""Synthetic provider CLI and schema regressions; no account access."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import jsonschema
import pytest

from cmu_cli import cli, ed_client, models, sio_client


@pytest.fixture(autouse=True)
def isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "CONFIG_PATH", tmp_path / "absent.json")
    monkeypatch.delenv("CMU_CLI_ED_TOKEN", raising=False)
    monkeypatch.setattr(
        cli, "CanvasClient", Mock(side_effect=AssertionError("Canvas forbidden"))
    )
    monkeypatch.setattr(
        cli, "load_config", Mock(side_effect=AssertionError("Canvas config forbidden"))
    )


def invoke(monkeypatch, capsys, argv, expected):
    monkeypatch.setattr(sys, "argv", ["cmu-cli", *argv, "--json"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == expected
    result = json.loads(capsys.readouterr().out)
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "docs/result.schema.json").read_text()
    )
    jsonschema.validate(result, schema)
    return result


@pytest.mark.parametrize(
    "action,flags,method,expected_args,kwargs",
    [
        ("courses", [], "courses", (), {}),
        (
            "threads",
            ["--course-id", "12", "--page-size", "2", "--max-pages", "3"],
            "threads",
            ("12",),
            {"page_size": 2, "max_pages": 3},
        ),
        (
            "thread",
            ["--id", "34", "--course-id", "12"],
            "thread",
            ("34",),
            {"course_id": "12"},
        ),
        ("replies", ["--id", "34"], "replies", ("34",), {"course_id": None}),
        (
            "search",
            ["--course-id", "12", "--query", "deadline"],
            "search",
            ("12", "deadline"),
            {"page_size": 100, "max_pages": 100},
        ),
    ],
)
def test_dispatch(monkeypatch, capsys, action, flags, method, expected_args, kwargs):
    mock = Mock(
        return_value=[] if action == "courses" else {"complete": True, "items": []}
    )
    monkeypatch.setattr(ed_client.EdClient, method, mock)
    result = invoke(monkeypatch, capsys, ["ed", action, *flags], 0)
    mock.assert_called_once_with(*expected_args, **kwargs)
    assert result["command"] == "ed " + action
    assert result["sources"][0]["source"] == "ed." + action


def test_missing_token(monkeypatch, capsys):
    result = invoke(monkeypatch, capsys, ["ed", "courses"], 2)
    assert result["error"]["code"] == "ED_AUTH_REQUIRED"
    assert result["data"] is None


@pytest.mark.parametrize("failure", [False, True])
def test_partial_search(monkeypatch, capsys, failure):
    partial = {
        "complete": False,
        "items": [{"id": 1}],
        "scope": "course thread listings",
    }
    mock = (
        Mock(side_effect=ed_client.EdAuthError("secret", partial=partial))
        if failure
        else Mock(return_value={**partial, "search_mode": "local"})
    )
    monkeypatch.setattr(ed_client.EdClient, "search", mock)
    result = invoke(
        monkeypatch, capsys, ["ed", "search", "--course-id", "12", "--query", "x"], 3
    )
    assert result["complete"] is False
    assert "secret" not in json.dumps(result)
    if failure:
        assert result["data"] == {"partial_listing": partial}
        assert result["sources"][0]["status"] == "error"


@pytest.mark.parametrize(
    "status",
    ["auth_redirect_blocked", "login_required", "unavailable", "discovery_required"],
)
def test_sio_statuses(monkeypatch, capsys, status):
    payload = sio_client._result(
        sio_client.SIO_URL, "https_get", status, ["READ_API_UNVERIFIED"]
    )
    monkeypatch.setattr(sio_client.SIOClient, "probe", Mock(return_value=payload))
    result = invoke(monkeypatch, capsys, ["sio", "probe"], 3)
    assert result["sources"][0]["status"] == status
    assert not result["complete"]
    assert result["data"]["registered_courses"] is None


def test_sio_error(monkeypatch, capsys):
    monkeypatch.setattr(
        sio_client.SIOClient, "probe", Mock(side_effect=sio_client.SIOError("secret"))
    )
    result = invoke(monkeypatch, capsys, ["sio", "probe"], 2)
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("bad", ["01", "0", " 12", "12/", True, 0])
def test_config_ids(bad):
    with pytest.raises(ValueError):
        models.Course("DEMO", 1, "Demo", "demo", ed_course_id=bad)


@pytest.mark.parametrize(
    "flags",
    [
        ["ed", "thread", "--id", "01"],
        ["ed", "threads", "--course-id", "2", "--page-size", "101"],
        ["sio", "enrolled"],
        ["sio", "waitlist"],
    ],
)
def test_rejected_flags(flags):
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(flags)
    assert exc.value.code == 2


def test_schedule_without_auth(monkeypatch, capsys):
    result = invoke(monkeypatch, capsys, ["sio", "schedule"], 3)
    assert result["data"]["status"] == "login_required"
    assert result["data"]["provenance"]["method"] == "not_requested"


@pytest.mark.parametrize("complete", [True, False])
def test_schedule_dispatch(monkeypatch, capsys, complete):
    payload = sio_client._result(
        sio_client.SEMESTER_SCHEDULE_URL,
        "https_get",
        "ok" if complete else "rendered_snapshot_required",
        [],
    )
    payload["complete"] = complete
    monkeypatch.setattr(
        sio_client.SIOClient, "semester_schedule", Mock(return_value=payload)
    )
    result = invoke(monkeypatch, capsys, ["sio", "schedule"], 0 if complete else 3)
    assert result["complete"] is complete
    assert result["command"] == "sio schedule"


def test_optional_config(monkeypatch, tmp_path, capsys):
    assert models.load_provider_config().ed_api_base_url == "https://us.edstem.org/api"
    path = tmp_path / "provider.json"
    path.write_text(json.dumps({"ed_api_base_url": "https://edstem.org/api"}))
    assert models.load_provider_config(path).ed_api_base_url == "https://edstem.org/api"
    mock = Mock(return_value=[])
    monkeypatch.setattr(ed_client.EdClient, "courses", mock)
    invoke(monkeypatch, capsys, ["--config", str(path), "ed", "courses"], 0)
    path.write_text(json.dumps({"ed_api_base_url": "https://edstem.org/api/"}))
    with pytest.raises(ValueError):
        models.load_provider_config(path)
    result = invoke(
        monkeypatch, capsys, ["--config", str(tmp_path / "missing"), "ed", "courses"], 2
    )
    assert result["error"]["code"] == "CONFIG_NOT_FOUND"
