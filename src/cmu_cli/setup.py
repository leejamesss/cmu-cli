"""Consent-first onboarding; existing transports, validators and storage only."""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from .browser_profiles import candidate_databases
from .canvas_client import CanvasClient
from .models import UsageError, default_config_path, validate_config
from .sio_client import SIO_ORIGIN
from .storage import atomic_write, safe_filename
from .web_session import (
    BROWSER_EXTRA_HINT,
    BrowserDependencyMissing,
    https_origin,
)

CANVAS_ORIGIN = "https://canvas.cmu.edu"
SIO_HOST = SIO_ORIGIN[1]


def display(value, limit: int | None = 240) -> str:
    """Plain terminal text: no ANSI, control/bidi characters or Rich markup."""
    text = value if isinstance(value, str) else ""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    cleaned = "".join(c for c in text if not unicodedata.category(c).startswith("C"))
    return cleaned if limit is None else cleaned[:limit]


def ask(label: str, default: str = "") -> str:
    answer = input(
        f"{display(label, limit=None)}"
        + (f" [{display(default)}]" if default else "")
        + ": "
    ).strip()
    if answer.casefold() in {"quit", "cancel"}:
        raise KeyboardInterrupt
    return answer or default


def yes(label: str) -> bool:
    return ask(label + " (yes/no)", "no").casefold() == "yes"


def validated(label, validator, default=""):
    while True:
        value = ask(label, default)
        try:
            return validator(value)
        except (ValueError, RuntimeError):
            print(
                "Invalid value; use the requested format, without credentials or query parameters."
            )


def origin(value: str) -> str:
    parsed = urlsplit(value)
    _, host, port = https_origin(value)
    if (
        parsed.path
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or port != 443
        or not re.fullmatch(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*", host)
        or parsed.netloc != host
    ):
        raise ValueError
    return value


def course_id(value: str, base: str) -> int:
    parsed = urlsplit(value)
    if (
        https_origin(value) != https_origin(base)
        or parsed.netloc != urlsplit(base).netloc
    ):
        raise ValueError
    match = re.fullmatch(r"/courses/([1-9][0-9]*)/?", parsed.path)
    if not match or parsed.query or parsed.fragment or "?" in value or "#" in value:
        raise ValueError
    return int(match[1])


def provider_url(value: str, provider: str) -> str:
    if not value:
        return ""
    _, host, port = https_origin(value)
    parsed = urlsplit(value)
    hosts = (
        {"piazza.com"}
        if provider == "piazza"
        else {"gradescope.com", "www.gradescope.com"}
    )
    pattern = (
        r"/class/[A-Za-z0-9_-]+/?"
        if provider == "piazza"
        else r"/courses/[1-9][0-9]*/?"
    )
    if (
        host not in hosts
        or port != 443
        or parsed.netloc != host
        or not re.fullmatch(pattern, parsed.path)
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
    ):
        raise ValueError
    return value


def text(value: str) -> str:
    if not value or display(value) != value:
        raise ValueError
    return value


def component(value: str) -> str:
    # Delegate path component rules to the canonical validator.
    validate_config(
        {
            "canvas_base_url": CANVAS_ORIGIN,
            "storage_root": "/tmp",
            "courses": [],
            "term": value,
        },
        Path("/tmp/config.json"),
    )
    return text(value)


def folder(value: str) -> str:
    value = safe_filename(display(value))
    try:
        return component(value)
    except ValueError:
        return "course-" + value


def choose_profile():
    rows = candidate_databases()
    print("Browser profiles (metadata only; no cookie database opened):")
    for index, row in enumerate(rows, 1):
        print(
            f"  {index}. {display(row['browser'])} / {display(row['profile'])} — {display(row['cookie_file'], limit=None)}"
        )
    if not rows:
        print(
            "No standard Edge/Chrome profiles found. Token or manual setup remains available."
        )
        return None
    while True:
        choice = ask("Profile number, or Enter to skip")
        if not choice:
            return None
        if choice.isdecimal() and 1 <= int(choice) <= len(rows):
            row = rows[int(choice) - 1]
            return {
                "enabled": True,
                "browser": row["browser"],
                "cookie_file": row["cookie_file"],
                "hosts": [],
            }
        print(
            "Choose one listed profile number; selection alone does not authorize cookie access."
        )


def discover(base, profile):
    """No client construction (and thus no credentials) before exact consent."""
    token_available = bool(os.environ.get("CMU_CLI_CANVAS_TOKEN"))
    if token_available:
        consent = yes(
            f"Use externally supplied CMU_CLI_CANVAS_TOKEN for GET course/tab discovery only at {base}"
        )
    elif profile:
        consent = yes(
            f"Allow selected profile cookies for Canvas GET discovery now and future reads at {base} (reads/decrypts cookies)"
        )
        if consent:
            profile["hosts"].append(urlsplit(base).hostname)
    else:
        return None, []
    if not consent:
        return None, []
    client = None
    try:
        client = CanvasClient(base, browser_auth=profile)
        rows = client.list_courses(include_term=True)
        if (
            not isinstance(rows, list)
            or any(
                not isinstance(r, dict) or type(r.get("id")) is not int or r["id"] < 1
                for r in rows
            )
            or len({r["id"] for r in rows}) != len(rows)
        ):
            raise ValueError
        return client, rows
    except BrowserDependencyMissing:
        print(BROWSER_EXTRA_HINT)
    except Exception:  # noqa: BLE001 - provider diagnostics may contain secrets
        print(
            "Canvas discovery unavailable or incomplete. Continue with manual course URLs."
        )
    if client:
        client.session.close()
    return None, []


def select_courses(rows):
    for index, row in enumerate(rows, 1):
        term = row.get("term")
        term_name = term.get("name", "") if isinstance(term, dict) else ""
        print(
            f"  {index}. {display(row.get('course_code'))} — {display(row.get('name'))} — {display(term_name)} (Canvas {row['id']})"
        )
    while True:
        query = ask(
            "Courses: comma-separated menu numbers or exact course codes; Enter for manual"
        )
        if not query:
            return []
        selected = []
        for selection in query.split(","):
            token = selection.strip()
            # Exact codes win; ambiguous codes require a menu number prefixed '#'.
            matches = [
                r
                for r in rows
                if display(r.get("course_code")).casefold() == token.casefold()
            ]
            if token.startswith("#") or not matches:
                number = token.removeprefix("#")
                matches = (
                    [rows[int(number) - 1]]
                    if number.isdecimal() and 1 <= int(number) <= len(rows)
                    else []
                )
            if len(matches) != 1:
                print(
                    "Unknown or ambiguous selection. Use #menu-number to disambiguate."
                )
                break
            if matches[0] not in selected:
                selected.append(matches[0])
        else:
            return selected


def courses_manual(base):
    rows = []
    while True:
        code = validated("Course code (e.g. 15-213)", text)
        ident = validated(
            "Canvas course URL (https://host/courses/ID)", lambda v: course_id(v, base)
        )
        rows.append({"id": ident, "course_code": code, "name": code})
        if not yes("Add another course"):
            return rows


def configure_courses(rows, client, profile):
    courses = []
    for row in rows:
        print(f"Configure Canvas course {row['id']}")
        code = validated("Course code", text, display(row.get("course_code")))
        name = validated("Course name", text, display(row.get("name")) or code)
        directory = validated("Course folder", component, folder(code))
        course = {
            "code": code,
            "name": name,
            "canvas_id": row["id"],
            "directory": directory,
        }
        if any(c["canvas_id"] == row["id"] for c in courses):
            raise UsageError(
                "Duplicate Canvas ID; restart setup with distinct courses."
            )
        while any(
            c["code"].casefold() == code.casefold()
            or c["directory"].casefold() == directory.casefold()
            for c in courses
        ):
            print("Codes and folders must be unique; distinguish sections explicitly.")
            code = validated("Distinct course code", text)
            directory = validated("Distinct course folder", component, folder(code))
        course.update(code=code, directory=directory)
        if client:
            try:
                tabs = client.tabs(row["id"])
                if not isinstance(tabs, list):
                    raise TypeError
                for provider in ("piazza", "gradescope"):
                    if any(
                        isinstance(t, dict)
                        and provider in display(t.get("label")).casefold()
                        for t in tabs
                    ):
                        print(
                            f"Detected {provider} Canvas tab. Its LTI URL is not a direct course URL and will not be opened."
                        )
            except Exception:  # noqa: BLE001 - never echo provider failures
                print(
                    "Canvas tabs unavailable; direct provider URLs can still be entered."
                )
        for provider in ("piazza", "gradescope"):
            url = validated(
                f"Direct {provider} course URL (optional; Enter to skip)",
                lambda v, provider=provider: provider_url(v, provider),
            )
            if url:
                course[provider + "_url"] = url
                host = urlsplit(url).hostname
                if (
                    profile
                    and host not in profile["hosts"]
                    and yes(
                        f"Allow selected browser profile for future {host} reads (no request now)"
                    )
                ):
                    profile["hosts"].append(host)
        courses.append(course)
    return courses


def run_setup(output: Path | None = None) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise UsageError(
            "setup requires an interactive terminal. Use cmu-cli config init --output PATH for automation."
        )
    destination = (output or default_config_path()).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise UsageError(
            "Setup will not overwrite an existing file or symlink. Choose another --output path."
        )
    client = None
    publishing = False
    try:
        print(
            "Guided setup. Type cancel or press Ctrl-C to stop without publishing a configuration."
        )
        base = validated(
            "Canvas HTTPS origin (no trailing slash)", origin, CANVAS_ORIGIN
        )
        profile = choose_profile()
        client, rows = discover(base, profile)
        selected = select_courses(rows) if rows else []
        if not selected:
            selected = courses_manual(base)
        courses = configure_courses(selected, client, profile)
        terms = {
            display(r["term"].get("name")) if isinstance(r.get("term"), dict) else ""
            for r in selected
        }
        unambiguous_term = len(terms) == 1 and "" not in terms
        if not unambiguous_term:
            print(
                "Term is missing or ambiguous; choose one storage term for these courses."
            )
        term = validated(
            "Storage term folder",
            component,
            folder(next(iter(terms))) if unambiguous_term else "current",
        )
        storage = validated(
            "Materials storage root", text, str(Path.home() / "cmu-coursework")
        )
        raw = {
            "canvas_base_url": base,
            "storage_root": str(Path(storage).expanduser().absolute()),
            "term": term,
            "courses": courses,
        }
        if profile and yes(
            f"Separately allow selected profile for SIO at https://{SIO_HOST} (future reads only; no SIO request now)"
        ):
            profile["hosts"].append(SIO_HOST)
        if profile and profile["hosts"]:
            raw["browser_auth"] = profile
        validate_config(raw, destination)
        print(
            f"Ready: {len(courses)} courses; destination {display(str(destination))}. No tokens, passwords or cookie values are stored."
        )
        if not yes("Create this configuration"):
            print("Cancelled; no configuration written.")
            return
        publishing = True
        atomic_write(
            destination,
            (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode(),
            exclusive=True,
        )
        print(
            "Configuration created. Run cmu-cli --config PATH config validate, then status. Provider access is not guaranteed by setup."
        )
    except (EOFError, KeyboardInterrupt):
        print(
            "\nInterrupted during publication; check the destination before retrying."
            if publishing
            else "\nCancelled; no configuration published."
        )
    finally:
        if client:
            client.session.close()
