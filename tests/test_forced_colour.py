"""CLICOLOR_FORCE must force the colour, not only the table.

`interactive` honoured CLICOLOR_FORCE, but Rich decides colour a second time from
the stream it is handed. A forced run therefore wrote the table's box drawing into
the pipe and dropped every colour inside it -- the worst of both, since the variable
exists to ask for exactly that colour.
"""

import io
import re

import pytest

from cmu_cli import style

SGR = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("NO_COLOR", "CLICOLOR_FORCE", "TERM"):
        monkeypatch.delenv(var, raising=False)
    style.console.cache_clear()
    yield
    style.console.cache_clear()


def _render(monkeypatch) -> str:
    buffer = io.StringIO()
    monkeypatch.setattr(style.sys, "stdout", buffer)
    style.console().print("[green]submitted[/]")
    return buffer.getvalue()


def test_forced_output_carries_colour(monkeypatch):
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert SGR.search(_render(monkeypatch)), "CLICOLOR_FORCE produced no colour"


def test_forced_output_is_still_interactive():
    assert style.colour_forced() is False
    import os

    os.environ["CLICOLOR_FORCE"] = "1"
    try:
        assert style.colour_forced() is True
        assert style.interactive(io.StringIO()) is True
    finally:
        del os.environ["CLICOLOR_FORCE"]


def test_zero_and_empty_do_not_force(monkeypatch):
    for value in ("0", ""):
        monkeypatch.setenv("CLICOLOR_FORCE", value)
        assert style.colour_forced() is False


def test_no_colour_still_wins_and_returns_before_rich(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    assert style.interactive(io.StringIO()) is False
    printed = []
    style.render("T", [("A", "a")], [{"a": "x"}], plain=lambda: printed.append("plain"))
    assert printed == ["plain"]


def test_an_unforced_pipe_keeps_richs_own_detection(monkeypatch):
    """Nothing forced: Rich decides, and a StringIO is not a terminal."""
    monkeypatch.setenv("TERM", "xterm-256color")
    assert not SGR.search(_render(monkeypatch))
