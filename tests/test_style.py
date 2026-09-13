"""Rich must be invisible to everything that is not a terminal.

Every other test captures stdout and the JSON contract is byte-compared, so the
property to protect is not that tables appear but that they appear nowhere else.
"""

import io

import pytest

from cmucw import style


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("NO_COLOR", "CLICOLOR_FORCE", "TERM"):
        monkeypatch.delenv(var, raising=False)


def test_not_interactive_when_piped(monkeypatch):
    monkeypatch.setattr(style.sys, "stdout", io.StringIO())
    assert style.interactive() is False


def test_no_color_wins_over_a_terminal(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(style.sys, "stdout", FakeTTY())
    assert style.interactive() is False


def test_dumb_terminal_is_not_interactive(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(style.sys, "stdout", FakeTTY())
    assert style.interactive() is False


def test_render_falls_back_to_the_callers_own_printing(capsys):
    # Under pytest stdout is already not a terminal; replacing it here would also
    # displace capsys's own capture and the assertions would read the wrong buffer.
    called = []
    style.render(
        "T", [("A", "a")], [{"a": 1}], plain=lambda: called.append(True) or print("◆ 1")
    )
    assert called == [True], "plain printer must be used when not interactive"


def test_render_emits_no_table_characters_when_piped(capsys):
    style.render("T", [("A", "a")], [{"a": "x"}], plain=lambda: print("◆ x"))
    out = capsys.readouterr().out
    assert out == "◆ x\n"
    assert "┏" not in out and "│" not in out


def test_empty_rows_still_use_the_plain_message_when_piped(capsys):
    style.render(
        "T", [("A", "a")], [], plain=lambda: print("No matching assignments returned.")
    )
    assert capsys.readouterr().out == "No matching assignments returned.\n"


@pytest.mark.parametrize(
    "label,colour",
    [
        ("submitted", "green"),
        ("graded", "green"),
        ("pending review", "green"),
        ("pending_review", "green"),  # Canvas uses both spellings
        ("not submitted", "yellow"),
        ("unsubmitted", "yellow"),
        ("missing", "yellow"),
        ("unknown", "dim"),
        ("", "dim"),
    ],
)
def test_status_colour_covers_both_spellings(label, colour):
    out = style.status_markup(label)
    assert out.startswith(f"[{colour}]")
    assert label in out


def test_status_markup_escapes_square_brackets():
    """A status Canvas invented must not be read back as Rich markup."""
    out = style.status_markup("[weird]")
    assert out == "[dim]\\[weird][/]"


def test_note_is_plain_when_piped(capsys):
    style.note("hello", "warn")
    assert capsys.readouterr().out == "hello\n"
