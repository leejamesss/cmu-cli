"""Offline regression contracts for the combined contributor release."""

import argparse
import io
import json
from importlib import resources

import pytest
from rich.console import Console

from cmu_cli import cli, models, style, web_session
from cmu_cli.ed_client import EdAuthError, EdClient
from tests.test_ed_client import Transport, ok, synthetic_session


@pytest.mark.parametrize("old_env", [False, True])
def test_old_default_config_is_ignored_without_touching_files(
    tmp_path, monkeypatch, old_env
):
    canonical = tmp_path / ".config/cmu_cli/config.json"
    old = tmp_path / ".config/cmucw/config.json"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"not even valid JSON: must not be read")
    monkeypatch.setattr(models, "CONFIG_PATH", canonical)
    if old_env:
        monkeypatch.setenv("CMUCW_CONFIG", str(old))
    before = old.stat()
    assert models.default_config_path() == canonical
    with pytest.raises(FileNotFoundError):
        models.load_config()
    assert models.load_provider_config() == models.ProviderConfig()
    assert old.read_bytes() == b"not even valid JSON: must not be read"
    assert old.stat().st_mtime_ns == before.st_mtime_ns
    assert not canonical.parent.exists()


def test_default_and_environment_config_precedence(tmp_path, monkeypatch):
    new, old = tmp_path / "new.json", tmp_path / "old.json"
    monkeypatch.setattr(models, "CONFIG_PATH", new)
    template = json.loads(
        resources.files("cmu_cli").joinpath("config.example.json").read_text()
    )
    template["storage_root"] = "materials"
    old.write_text(json.dumps(template))
    assert models.default_config_path() == new
    with pytest.raises(FileNotFoundError):
        models.load_config()
    assert models.load_provider_config() == models.ProviderConfig()
    new.write_text(json.dumps({**template, "term": "new"}))
    assert models.load_config().term == "new"
    monkeypatch.setenv("CMUCW_CONFIG", str(old))
    assert models.load_config().term == "new"
    assert models.load_provider_config() == models.provider_config(template)
    assert old.read_text() == json.dumps(template)
    monkeypatch.setenv("CMU_CLI_CONFIG", str(new))
    assert models.load_config().term == "new"
    assert models.load_config(old).term == template.get("term", "current")


@pytest.mark.parametrize("modern", [False, True])
def test_canvas_ignores_old_token(monkeypatch, modern):
    monkeypatch.setenv("CMUCW_CANVAS_TOKEN", "synthetic-old")
    if modern:
        monkeypatch.setenv("CMU_CLI_CANVAS_TOKEN", "synthetic-new")
    if not modern:
        with pytest.raises(web_session.SessionError, match="opt-in"):
            web_session.canvas_api_session("https://canvas.example.invalid")
        return
    session, _ = web_session.canvas_api_session("https://canvas.example.invalid")
    assert session.headers["Authorization"] == "Bearer synthetic-new"
    session.close()


@pytest.mark.parametrize("modern", [False, True])
def test_ed_ignores_old_token(monkeypatch, modern):
    monkeypatch.setenv("CMUCW_ED_TOKEN", "synthetic-old")
    if modern:
        monkeypatch.setenv("CMU_CLI_ED_TOKEN", "synthetic-new")
    session = synthetic_session()
    transport = Transport([ok({"courses": []})])
    session.mount("https://", transport)
    monkeypatch.setattr("cmu_cli.ed_client.configured_session", lambda: session)
    if not modern:
        with pytest.raises(EdAuthError, match="CMU_CLI_ED_TOKEN"):
            EdClient().courses()
        assert not transport.sent
        return
    with EdClient() as client:
        assert client.courses() == []
    assert transport.sent[0].headers["Authorization"] == "Bearer synthetic-new"


@pytest.mark.parametrize("force,expected", [("0", False), ("", False), ("1", True)])
def test_color_force_zero_does_not_turn_a_pipe_into_a_terminal(
    monkeypatch, force, expected
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("CLICOLOR_FORCE", force)
    assert style.interactive(io.StringIO()) is expected


def test_assignment_table_preserves_actionable_url_and_literal_text(
    monkeypatch, tmp_path
):
    url = "https://canvas.example.invalid/courses/1/assignments/2"
    row = {
        "course": "DEMO",
        "name": "[red]literal[/red]",
        "due_local": "unknown",
        "status": "unknown",
        "source": "canvas",
        "url": url,
    }
    monkeypatch.setattr(cli, "assignment_rows", lambda *args: [row])
    output = io.StringIO()
    monkeypatch.setattr(style, "interactive", lambda: True)
    monkeypatch.setattr(
        style, "console", lambda: Console(file=output, width=240, color_system=None)
    )
    config = models.Config("https://canvas.example.invalid", tmp_path, ())
    assert (
        cli.command_assignments(
            argparse.Namespace(course=None, json=False), config, None
        )
        == 0
    )
    text = output.getvalue()
    assert url in text
    assert "[red]literal[/red]" in text
    assert "URL" in text
