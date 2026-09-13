"""Independent synthetic reviewer probes; no external I/O."""

import argparse
import contextlib
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

from cmu_cli import cli, style
from cmu_cli.models import Course
from cmu_cli.storage import announcements_markdown, assignments_markdown


def test_materials_command_all_categories_and_cache(tmp_path):
    from cmu_cli.storage import canvas_material_folder

    c = Course("A", 1, "A", "a")
    root = tmp_path / "a" / "term"
    names = [
        "OLD - slides.pdf",
        "syllabus.pdf",
        "recitation1.pdf",
        "hw2.pdf",
        "homework.pdf",
        "lecture.pdf",
    ]
    for name in names:
        folder = canvas_material_folder(root, name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"fixture")
    cache = root / ".cmucw" / "versions"
    cache.mkdir(parents=True)
    (cache / "old.pdf").write_bytes(b"fixture")
    client = Mock()
    client.files.return_value = []
    config = SimpleNamespace(storage_root=tmp_path, term="term", courses=[c])
    with contextlib.redirect_stdout(io.StringIO()) as out:
        cli.command_materials(
            argparse.Namespace(course=None, limit=None, json=True), config, client
        )
    rows = json.loads(out.getvalue())["data"]
    assert sorted(r["name"] for r in rows) == sorted(names)


def test_public_exam_time_label_evidence(monkeypatch):
    c = Course(
        "A",
        1,
        "A",
        "a",
        public_site_url="https://example.invalid",
        assessment_year=2030,
    )
    client = Mock()
    client.quizzes.return_value = []
    monkeypatch.setattr(
        cli,
        "public_quizzes",
        lambda *a: [
            {
                "source": "course_site",
                "course": "A",
                "name": "Final Exam",
                "kind": "exam",
                "starts_at": "2030-12-01T09:00:00-05:00",
                "starts_local": "2030-12-01 09:00 EST",
            }
        ],
    )
    row = cli.quiz_rows(client, [c])[0]
    assert row["unlock_at"] is None and row["time_inferred"]
    assert cli.quiz_time(row) == "starts (inferred) 2030-12-01 09:00 EST"


def test_status_and_platforms_lti_command(monkeypatch):
    c = Course("A", 1, "A", "a")
    config = SimpleNamespace(courses=[c])
    client = Mock()
    client.base_url = "https://canvas.example.invalid"
    client.auth_method = "synthetic"
    client.list_courses.return_value = [{"id": 1}]
    client.tabs.return_value = [
        {"label": "Piazza", "html_url": "/courses/1/external_tools/2"}
    ]
    with contextlib.redirect_stdout(io.StringIO()) as out:
        cli.command_status(argparse.Namespace(json=True), config, client)
    course = json.loads(out.getvalue())["data"]["courses"][0]
    assert not course["piazza_configured"] and course["piazza_in_canvas"]
    with contextlib.redirect_stdout(io.StringIO()) as out:
        cli.command_platforms(
            argparse.Namespace(json=False, course=None), config, client
        )
    assert (
        "piazza: not configured" in out.getvalue()
        and "opens from Canvas: https://" in out.getvalue()
    )


def test_incomplete_real_fetch_and_limit():
    old = cli._CONTEXT
    cli._CONTEXT = {"warnings": [], "sources": [], "command": "quizzes"}
    try:

        def fail():
            raise ValueError("must not leak")

        assert cli.fetch("canvas.quizzes", "A", fail) == []
        cli.limited([1, 2, 3], 1)
        text = "\n".join(cli.incomplete_report(cli._CONTEXT["warnings"]))
        assert (
            "canvas.quizzes unavailable for A" in text
            and "showing 1 of 3" in text
            and "must not leak" not in text
        )
    finally:
        cli._CONTEXT = old


def test_index_offsets_stability_and_no_input_mutation():
    c = Course("A", 1, "A", "a")
    rows = [
        {
            "name": "later",
            "title": "later",
            "due_at": "2030-01-01T07:00:00Z",
            "posted_at": "2030-01-01T07:00:00Z",
        },
        {
            "name": "earlier",
            "title": "earlier",
            "due_at": "2030-01-01T08:00:00+02:00",
            "posted_at": "2030-01-01T08:00:00+02:00",
        },
    ]
    before = json.dumps(rows)
    a = assignments_markdown(c, rows)
    b = announcements_markdown(c, rows)
    assert (
        a.index("earlier") < a.index("later")
        and b.index("later") < b.index("earlier")
        and json.dumps(rows) == before
    )


def test_help_every_subcommand_parses():
    parser = cli.parser()
    choices = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    ).choices
    for _name, subparser in choices.items():
        assert "usage:" in subparser.format_help()
    assert parser.parse_args(["quizzes", "--json"]).json


def test_force_render_literal_and_no_color(monkeypatch):
    for force, no_color, dumb, expected in [
        ("1", "", False, True),
        ("0", "", False, False),
        ("", "", False, False),
        ("1", "1", False, False),
        ("1", "", True, False),
    ]:
        monkeypatch.setenv("CLICOLOR_FORCE", force)
        monkeypatch.setenv("NO_COLOR", no_color)
        monkeypatch.setenv("TERM", "dumb" if dumb else "xterm-256color")
        style.console.cache_clear()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            style.render(
                "T",
                [("Value", "v")],
                [{"v": "[green]literal[/]"}],
                lambda: print("[green]literal[/]"),
            )
        assert ("\x1b[" in out.getvalue()) == expected
        assert "[green]literal[/]" in out.getvalue()
    style.console.cache_clear()
