"""End-to-end CLI, real Canvas transport and storage; synthetic memory HTTP only."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from cmu_cli import cli, storage
from cmu_cli.canvas_client import CanvasClient
from tests.test_network_security import BASE, payload, transport

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "docs/result.schema.json").read_text()
)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def run(monkeypatch, capsys, tmp_path, command, replies, courses=None):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "canvas_base_url": BASE,
                "storage_root": "workspace",
                "courses": courses
                or [{"code": "A", "canvas_id": 1, "name": "A", "directory": "a"}],
                "timezone": "UTC",
            }
        )
    )
    session, adapter = transport(monkeypatch, replies)
    monkeypatch.setattr(
        cli,
        "CanvasClient",
        lambda base, **kwargs: CanvasClient(base, session, **kwargs),
    )
    monkeypatch.setattr(
        sys, "argv", ["cmu-cli", "--config", str(config), *command, "--json"]
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    result = json.loads(capsys.readouterr().out)
    VALIDATOR.validate(result)
    return error.value.code, result, adapter


def test_cli_canvas_pagination(monkeypatch, capsys, tmp_path):
    courses = [
        {"code": str(i), "canvas_id": i, "name": str(i), "directory": str(i)}
        for i in (1, 2)
    ]
    code, result, adapter = run(
        monkeypatch,
        capsys,
        tmp_path,
        ["courses"],
        [payload([{"id": 1}], "/page2"), payload([{"id": 2}])],
        courses,
    )
    assert code == 0 and result["complete"] is True
    assert [r["canvas_id"] for r in result["data"]] == [1, 2]
    assert all(r["available"] for r in result["data"])
    assert len(adapter.seen) == 2


def test_cli_partial_files_skip_sync(monkeypatch, capsys, tmp_path):
    code, result, adapter = run(
        monkeypatch,
        capsys,
        tmp_path,
        ["sync"],
        [payload([]), (403, {}, b"private server detail")],
    )
    assert code == 3 and result["status"] == "partial" and result["complete"] is False
    assert result["data"] == []
    assert result["sources"] == [
        {"source": "canvas.files", "course": "A", "status": "error"}
    ]
    assert result["warnings"][0]["code"] == "SOURCE_UNAVAILABLE"
    assert not (tmp_path / "workspace").exists()
    assert len(adapter.seen) == 2
    assert "private server detail" not in json.dumps(result)


def test_cli_sync_preserves_unmanaged_file(monkeypatch, capsys, tmp_path):
    destination = tmp_path / "workspace/a/current/01_讲义/note.txt"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"USER")
    item = {
        "id": 1,
        "display_name": "note.txt",
        "url": BASE + "/download",
        "updated_at": "2030-01-01T00:00:00Z",
        "size": 6,
    }
    code, result, adapter = run(
        monkeypatch,
        capsys,
        tmp_path,
        ["sync"],
        [payload([]), payload([item]), payload([]), payload([]), (200, {}, b"REMOTE")],
    )
    assert code == 0 and result["complete"] is True
    assert destination.read_bytes() == b"USER"
    assert any(
        p.read_bytes() == b"REMOTE"
        for p in destination.parent.glob("*.txt")
        if p != destination
    )
    assert result["data"][0]["files"] == 1
    assert len(result["data"][0]["downloads"]) == 1
    assert len(adapter.seen) == 5


def test_cli_deadline_semantics(monkeypatch, capsys, tmp_path):
    quiz = {
        "id": 7,
        "title": "quiz",
        "due_at": "2030-01-02T01:00:00+01:00",
        "unlock_at": None,
        "lock_at": "2030-01-03T00:00:00Z",
    }
    code, result, _ = run(monkeypatch, capsys, tmp_path, ["quizzes"], [payload([quiz])])
    row = result["data"][0]
    assert code == 0
    assert row["due_at"] == "2030-01-02T00:00:00Z"
    assert row["starts_at"] is None and row["unlock_at"] is None
    assert row["lock_at"] == "2030-01-03T00:00:00Z"
    assert row["dates_raw"]["due_at"] == quiz["due_at"]


def test_storage_platform_rejection_without_global_os_patch(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "os", SimpleNamespace(name="nt"))
    with pytest.raises(RuntimeError, match="POSIX"):
        storage.atomic_write(tmp_path / "blocked", b"data")
    assert not (tmp_path / "blocked").exists()


@pytest.mark.parametrize("date", ["2030-01-01T00:00:00", "malformed"])
def test_human_deadline_does_not_guess_local_timezone(date):
    assert storage.format_time(date) == "Unknown"
