"""Rich rendering for interactive output, plain text everywhere else.

The split matters more than the styling. Every existing test captures stdout and the
JSON contract is compared byte for byte, so Rich is used *only* when stdout is a
terminal. Piped, redirected, under pytest, or with NO_COLOR set, output falls back to
exactly the lines this tool printed before -- Rich never sees them, so it cannot
re-wrap or re-colour them.

`--json` paths return before reaching here at all.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import Any


def interactive(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("CLICOLOR_FORCE", "0") not in ("", "0"):
        return True
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


@lru_cache(maxsize=1)
def console():
    """Built once, and only if something actually renders through it."""
    from rich.console import Console

    return Console(highlight=False, soft_wrap=False)


STATUS_STYLE = {
    "submitted": "green",
    "graded": "green",
    "late": "green",
    "pending review": "green",
    "not submitted": "yellow",
    "unsubmitted": "yellow",
    "missing": "yellow",
    "no submission": "yellow",
}


def status_markup(label: Any) -> str:
    """Colour the status without altering its text.

    Canvas writes both "pending review" and "pending_review"; submissions.py already
    normalises underscores for the tri-state logic, so do the same here rather than
    letting one spelling fall through to the unknown style.
    """
    from rich.markup import escape

    key = str(label).strip().lower().replace("_", " ")
    return f"[{STATUS_STYLE.get(key, 'dim')}]{escape(str(label))}[/]"


def render(
    title: str,
    columns: Sequence[tuple[str, str]],
    rows: Sequence[dict[str, Any]],
    plain: Callable[[], None],
    empty: str = "Nothing to show.",
    styles: dict[str, Callable[[Any], str]] | None = None,
) -> None:
    """A table when someone is watching; the original lines when something is parsing.

    `plain` is the caller's existing printing, called verbatim in non-interactive mode
    so that byte-for-byte output is preserved.
    """
    if not interactive():
        plain()
        return
    if not rows:
        console().print(f"[dim]{empty}[/dim]")
        return
    from rich.markup import escape
    from rich.table import Table

    table = Table(
        title=title, header_style="bold cyan", expand=True, title_justify="left"
    )
    for header, _ in columns:
        table.add_column(header, overflow="fold")
    styles = styles or {}
    for row in rows:
        cells = []
        for _, key in columns:
            value = row.get(key)
            if key in styles:
                cells.append(styles[key](value))
            elif value is None:
                cells.append("[dim]—[/dim]")
            else:
                cells.append(escape(str(value)))
        table.add_row(*cells)
    console().print(table)


def note(message: str, kind: str = "info") -> None:
    if not interactive():
        print(message)
        return
    from rich.markup import escape

    colour = {"ok": "green", "warn": "yellow", "error": "red"}.get(kind, "")
    body = escape(message)
    console().print(f"[{colour}]{body}[/]" if colour else body)
