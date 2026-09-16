"""Interactive setup against synthetic HTTP; no personal profile/credential reads."""

import json
import stat
import sys
from unittest.mock import Mock

import pytest

from cmu_cli import cli, models, setup
from tests.test_network_security import BASE, payload, transport

PROFILE = {"browser": "edge", "profile": "Default", "cookie_file": "/synthetic/Cookies"}
ROW = {
    "id": 12,
    "course_code": "15-213",
    "name": "Systems",
    "term": {"name": "Fall 2030"},
}


def interactive(monkeypatch, answers, profiles=()):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(setup, "candidate_databases", lambda: list(profiles))
    responses = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt: next(responses))
    return responses


def manual_answers(base=BASE):
    return [
        base,
        "15-213",
        base + "/courses/12",
        "no",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "yes",
    ]


def test_manual_cli_dispatch_and_canonical_validation(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    interactive(monkeypatch, manual_answers())
    monkeypatch.setattr(sys, "argv", ["cmu-cli", "setup", "--output", str(path)])
    cli.main()
    result = models.load_config(path)
    assert result.courses[0].canvas_id == 12
    assert result.courses[0].directory == "15-213"
    assert result.browser_auth is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_default_path(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(models, "CONFIG_PATH", path)
    interactive(monkeypatch, manual_answers())
    setup.run_setup()
    assert path.is_file()


@pytest.mark.parametrize("at", range(12))
def test_cancel_at_every_manual_prompt(monkeypatch, tmp_path, at):
    answers = manual_answers()[:at] + ["cancel"]
    interactive(monkeypatch, answers)
    setup.run_setup(tmp_path / "config.json")
    assert not (tmp_path / "config.json").exists()


@pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
def test_eof_interrupt(monkeypatch, tmp_path, exc):
    interactive(monkeypatch, [])

    def stop(prompt):
        raise exc

    monkeypatch.setattr("builtins.input", stop)
    setup.run_setup(tmp_path / "config.json")
    assert not (tmp_path / "config.json").exists()


def test_nontty_never_discovers(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    forbidden = Mock(side_effect=AssertionError("must not list profiles"))
    monkeypatch.setattr(setup, "candidate_databases", forbidden)
    with pytest.raises(models.UsageError, match="config init"):
        setup.run_setup(tmp_path / "config.json")
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://canvas.cmu.edu",
        "https://u:p@canvas.cmu.edu",
        "https://canvas.cmu.edu.evil/",
        "https://canvas.cmu.edu:444",
        "https://canvas.cmu.edu?token=SECRET",
        "https://canvas.cmu.edu#",
        "https://canvas.cmu.edu\\evil",
        "https://canvas.cmu.edu\n",
        "https://canvas.cmu.edu/",
        "https://[::1]",
        "https://canvas.cmu.edu:",
    ],
)
def test_invalid_origins(url):
    with pytest.raises((ValueError, RuntimeError)):
        setup.origin(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.invalid/courses/12",
        BASE + "/courses/12?token=SECRET",
        BASE + "/courses/0",
        BASE + "/courses/12/assignments",
        BASE + "/courses/012",
        "https://u:p@canvas.example.invalid/courses/12",
        BASE + ":444/courses/12",
    ],
)
def test_invalid_course_urls(url):
    with pytest.raises((ValueError, RuntimeError)):
        setup.course_id(url, BASE)


@pytest.mark.parametrize(
    "provider,url",
    [
        ("piazza", "https://piazza.com.evil/class/x"),
        ("piazza", "https://piazza.com/class/x?cid=1"),
        ("piazza", "https://u:p@piazza.com/class/x"),
        ("gradescope", "https://www.gradescope.com:444/courses/1"),
        ("gradescope", "https://www.gradescope.com/courses/1/assignments"),
        ("gradescope", "https://canvas.cmu.edu/courses/1/external_tools/2"),
    ],
)
def test_invalid_provider_urls(provider, url):
    with pytest.raises((ValueError, RuntimeError)):
        setup.provider_url(url, provider)


def test_profile_selection_and_denial_do_not_authenticate(monkeypatch):
    interactive(monkeypatch, ["1", "no"], [PROFILE])
    factory = Mock(side_effect=AssertionError("credentials must not be accessed"))
    monkeypatch.setattr(setup, "CanvasClient", factory)
    profile = setup.choose_profile()
    assert setup.discover(BASE, profile) == (None, [])
    assert profile["hosts"] == []
    factory.assert_not_called()


def test_token_denial_never_falls_back_to_cookies(monkeypatch):
    interactive(monkeypatch, ["no"])
    monkeypatch.setenv("CMU_CLI_CANVAS_TOKEN", "SYNTHETIC_SECRET")
    factory = Mock(side_effect=AssertionError)
    monkeypatch.setattr(setup, "CanvasClient", factory)
    assert setup.discover(BASE, {**PROFILE, "hosts": []}) == (None, [])
    factory.assert_not_called()


def test_authenticated_paginated_setup_with_tabs_and_separate_hosts(
    monkeypatch, tmp_path, capsys
):
    session, adapter = transport(
        monkeypatch,
        [
            payload([ROW], "/api/v1/courses?page=2"),
            payload([{**ROW, "id": 13, "course_code": "15-410"}]),
            payload(
                [
                    {
                        "label": "Piazza",
                        "html_url": "https://evil.invalid/launch?SECRET",
                    },
                    {"label": "Gradescope"},
                ]
            ),
        ],
    )
    loader = Mock(return_value=session)
    monkeypatch.setattr("cmu_cli.web_session.browser_cookie_session", loader)
    interactive(
        monkeypatch,
        [
            BASE,
            "1",
            "yes",
            "15-213",
            "",
            "",
            "",
            "https://piazza.com/class/demo",
            "yes",
            "https://www.gradescope.com/courses/9",
            "no",
            "",
            "",
            "yes",
            "yes",
        ],
        [PROFILE],
    )
    path = tmp_path / "config.json"
    setup.run_setup(path)
    raw = json.loads(path.read_text())
    assert raw["term"] == "Fall 2030"
    assert raw["browser_auth"]["hosts"] == [
        "canvas.example.invalid",
        "piazza.com",
        setup.SIO_HOST,
    ]
    assert len(adapter.seen) == 3
    assert all(
        r.url.startswith(BASE + "/api/v1/") and r.method == "GET" for r in adapter.seen
    )
    assert "include%5B%5D=term" in adapter.seen[0].url
    loader.assert_called_once()
    assert loader.call_args.args[0] == "canvas.example.invalid"
    out = capsys.readouterr().out
    assert "Detected piazza" in out and "Detected gradescope" in out
    assert "evil.invalid" not in out and "SECRET" not in out
    assert "SECRET" not in path.read_text()


@pytest.mark.parametrize(
    "replies",
    [
        [(403, {}, b"SECRET")],
        [payload([ROW], "/page2"), (403, {}, b"SECRET")],
        [payload([ROW], "https://evil.invalid/page2")],
        [payload([ROW], "/api/v1/courses")],
    ],
)
def test_incomplete_discovery_discards_all_rows(monkeypatch, replies, capsys):
    session, adapter = transport(monkeypatch, replies)
    monkeypatch.setattr("cmu_cli.web_session.configured_session", lambda: session)
    monkeypatch.setenv("CMU_CLI_CANVAS_TOKEN", "SYNTHETIC_SECRET")
    interactive(monkeypatch, ["yes"])
    assert setup.discover(BASE, None) == (None, [])
    assert all(r.url.startswith(BASE + "/") for r in adapter.seen)
    assert all(
        r.headers["Authorization"] == "Bearer SYNTHETIC_SECRET" for r in adapter.seen
    )
    assert "SECRET" not in capsys.readouterr().out


def test_ambiguous_selection_and_terminal_injection(monkeypatch, capsys):
    rows = [{**ROW, "name": "\x1b[2JBad\n\u202e[red]"}, {**ROW, "id": 13}]
    interactive(monkeypatch, ["15-213", "#2"])
    assert setup.select_courses(rows) == [rows[1]]
    out = capsys.readouterr().out
    assert "ambiguous" in out
    assert "\x1b" not in out and "\u202e" not in out
    assert "[red]" in out  # literal built-in print, not Rich markup


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling", "parent"])
def test_no_clobber_links_or_existing_file(monkeypatch, tmp_path, kind):
    target = tmp_path / "config.json"
    original = tmp_path / "original"
    original.write_text("KEEP")
    if kind == "file":
        target.write_text("KEEP")
    elif kind == "parent":
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        target = link / "new.json"
    else:
        target.symlink_to(original if kind == "symlink" else tmp_path / "missing")
    interactive(monkeypatch, manual_answers())
    with pytest.raises((models.UsageError, OSError)):
        setup.run_setup(target)
    assert original.read_text() == "KEEP"
    if kind == "parent":
        assert not (tmp_path / "new.json").exists()
    elif kind == "file":
        assert target.read_text() == "KEEP"
    else:
        assert target.is_symlink()


def test_concurrent_publication_never_overwrites(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    interactive(monkeypatch, manual_answers())
    write = setup.atomic_write

    def race(target, content, *, exclusive):
        assert exclusive is True
        target.write_text("WINNER")
        write(target, content, exclusive=exclusive)

    monkeypatch.setattr(setup, "atomic_write", race)
    with pytest.raises(FileExistsError):
        setup.run_setup(path)
    assert path.read_text() == "WINNER"
    assert not list(tmp_path.glob(".cmu-cli-*.tmp"))


@pytest.mark.parametrize("term", [{"name": "Spring 2031"}, None])
def test_mixed_or_missing_terms_require_explicit_storage_term(
    monkeypatch, tmp_path, capsys, term
):
    rows = [ROW, {**ROW, "id": 13, "course_code": "15-410", "term": term}]
    session, _ = transport(monkeypatch, [payload(rows), payload([]), payload([])])
    monkeypatch.setattr("cmu_cli.web_session.configured_session", lambda: session)
    monkeypatch.setenv("CMU_CLI_CANVAS_TOKEN", "SYNTHETIC_SECRET")
    interactive(
        monkeypatch, [BASE, "yes", "1,2", *([""] * 10), "Chosen term", "", "yes"]
    )
    path = tmp_path / "config.json"
    setup.run_setup(path)
    assert models.load_config(path).term == "Chosen term"
    assert "missing or ambiguous" in capsys.readouterr().out
    assert "SYNTHETIC_SECRET" not in path.read_text()


def test_403_full_manual_fallback_and_declined_sio(monkeypatch, tmp_path, capsys):
    session, adapter = transport(monkeypatch, [(403, {}, b"SYNTHETIC_SECRET")])
    monkeypatch.setattr(
        "cmu_cli.web_session.browser_cookie_session", lambda host, options: session
    )
    interactive(
        monkeypatch, [BASE, "1", "yes", *manual_answers()[1:-1], "no", "yes"], [PROFILE]
    )
    path = tmp_path / "config.json"
    setup.run_setup(path)
    assert models.load_config(path).browser_auth["hosts"] == ["canvas.example.invalid"]
    assert len(adapter.seen) == 1
    assert "SYNTHETIC_SECRET" not in capsys.readouterr().out


def test_final_decline_leaves_no_file(monkeypatch, tmp_path):
    interactive(monkeypatch, [*manual_answers()[:-1], "no"])
    setup.run_setup(tmp_path / "config.json")
    assert not (tmp_path / "config.json").exists()


@pytest.mark.parametrize(
    "raw", [{}, [ROW, ROW], [{"id": True}], [{"id": -1}], [{"id": "1"}]]
)
def test_malformed_discovery_is_not_partial_success(monkeypatch, raw):
    session, _ = transport(monkeypatch, [payload(raw)])
    monkeypatch.setattr("cmu_cli.web_session.configured_session", lambda: session)
    monkeypatch.setenv("CMU_CLI_CANVAS_TOKEN", "SYNTHETIC_SECRET")
    interactive(monkeypatch, ["yes"])
    assert setup.discover(BASE, None) == (None, [])


def test_exact_consent_prompt_is_not_truncated(monkeypatch):
    prompts = []
    label = "Allow " + "a" * 230 + ".example.invalid"
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "no")
    assert setup.yes(label) is False
    assert label in prompts[0]


def test_interruption_after_publication_does_not_claim_no_write(
    monkeypatch, tmp_path, capsys
):
    path = tmp_path / "config.json"
    interactive(monkeypatch, manual_answers())
    write = setup.atomic_write

    def interrupt(target, content, *, exclusive):
        write(target, content, exclusive=exclusive)
        raise KeyboardInterrupt

    monkeypatch.setattr(setup, "atomic_write", interrupt)
    setup.run_setup(path)
    assert models.load_config(path).courses[0].canvas_id == 12
    assert "check the destination" in capsys.readouterr().out
