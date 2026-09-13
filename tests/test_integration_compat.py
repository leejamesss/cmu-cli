"""Offline regression contracts for the combined contributor release."""

import argparse
import io
import json
from importlib import resources

import pytest
from rich.console import Console

from cmu_cli import cli, models, style, web_session
from cmu_cli.ed_client import EdClient
from tests.test_ed_client import Transport, ok, synthetic_session


def test_default_and_environment_config_precedence(tmp_path, monkeypatch):
    new, old = tmp_path / "new.json", tmp_path / "old.json"
    monkeypatch.setattr(models, "CONFIG_PATH", new)
    monkeypatch.setattr(models, "LEGACY_CONFIG_PATH", old)
    template = json.loads(
        resources.files("cmu_cli").joinpath("config.example.json").read_text()
    )
    template["storage_root"] = "materials"
    old.write_text(json.dumps(template))
    assert models.default_config_path() == old
    assert models.load_config().storage_root == tmp_path / "materials"
    assert models.load_provider_config() == models.provider_config(template)
    new.write_text(json.dumps({**template, "term": "new"}))
    assert models.load_config().term == "new"
    monkeypatch.setenv("CMUCW_CONFIG", str(old))
    assert models.load_config().term == template.get("term", "current")
    monkeypatch.setenv("CMU_CLI_CONFIG", str(new))
    assert models.load_config().term == "new"
    assert models.load_config(old).term == template.get("term", "current")


@pytest.mark.parametrize("modern", [False, True])
def test_canvas_legacy_token_and_new_precedence(monkeypatch, modern):
    monkeypatch.setenv("CMUCW_CANVAS_TOKEN", "synthetic-old")
    if modern:
        monkeypatch.setenv("CMU_CLI_CANVAS_TOKEN", "synthetic-new")
    session, _ = web_session.canvas_api_session("https://canvas.example.invalid")
    assert session.headers["Authorization"] == "Bearer synthetic-" + (
        "new" if modern else "old"
    )
    session.close()


@pytest.mark.parametrize("modern", [False, True])
def test_ed_legacy_token_and_new_precedence(monkeypatch, modern):
    monkeypatch.setenv("CMUCW_ED_TOKEN", "synthetic-old")
    if modern:
        monkeypatch.setenv("CMU_CLI_ED_TOKEN", "synthetic-new")
    session = synthetic_session()
    transport = Transport([ok({"courses": []})])
    session.mount("https://", transport)
    monkeypatch.setattr("cmu_cli.ed_client.configured_session", lambda: session)
    assert EdClient().courses() == []
    assert transport.sent[0].headers["Authorization"] == "Bearer synthetic-" + (
        "new" if modern else "old"
    )


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
