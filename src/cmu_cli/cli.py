"""cmu-cli: read-only CMU coursework CLI for Canvas, Piazza, and Gradescope entry points."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse
from dateutil.parser import parse as parse_date

from . import style
from .canvas_client import CanvasClient, CanvasError
from .edge_browser import BrowserError, EdgeBrowser
from .gradescope_client import GradescopeClient
from .models import Config, Course, UsageError, load_config
from .official_materials import public_materials
from .official_quizzes import public_quizzes
from .piazza_client import PiazzaClient
from .storage import (
    clean_html,
    course_root,
    format_time,
    local_material_paths,
    sync_course,
)
from .submissions import submission_state
from .web_session import BROWSER_EXTRA_HINT, BrowserDependencyMissing

SCHEMA_VERSION = "1.0"
_CONTEXT: dict[str, Any] = {"command": None, "warnings": [], "sources": []}


class OperationError(RuntimeError):
    """Sanitized failure without provider exception details in its message."""


ERROR_HINTS = {
    "BROWSER_DEPENDENCY_MISSING": BROWSER_EXTRA_HINT,
    "CONFIG_NOT_FOUND": "Create one with: cmu-cli config init --output cmu-cli.json",
    "CONFIG_ALREADY_EXISTS": "Choose another path with --output, or edit the existing file.",
    "CONFIG_INVALID_JSON": "The configuration file is not valid JSON.",
    "CONFIG_OR_OPERATION_FAILED": (
        "Check configuration, authorization and service availability; "
        "no credentials are included in diagnostics."
    ),
}


@contextmanager
def sanitized_errors():
    try:
        yield
    except BrowserDependencyMissing:
        raise OperationError("BROWSER_DEPENDENCY_MISSING") from None
    except (UsageError, OperationError):
        # Authored here from the caller's own input and configuration, so there is
        # no provider detail to strip and nothing is gained by hiding the reason.
        raise
    except Exception as exc:  # noqa: BLE001 - sanitize provider failures
        code = (
            "CONFIG_NOT_FOUND"
            if isinstance(exc, FileNotFoundError)
            else "CONFIG_OR_OPERATION_FAILED"
        )
        if isinstance(exc, FileExistsError):
            code = "CONFIG_ALREADY_EXISTS"
        if isinstance(exc, json.JSONDecodeError):
            code = "CONFIG_INVALID_JSON"
        raise OperationError(code) from None


def timestamp(value):
    """Normalize aware ISO dates; never guess an offset for naive dates."""
    if not value:
        return None
    try:
        try:
            parsed = isoparse(str(value))
        except ValueError:
            import re

            if not re.fullmatch(
                r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}", str(value)
            ):
                return None
            parsed = parse_date(str(value))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError):
        return None


def fetch(source, course, function):
    record = {"source": source, "course": course, "status": "ok"}
    _CONTEXT["sources"].append(record)
    try:
        with sanitized_errors():
            return function()
    except OperationError as exc:
        record["status"] = "error"
        if str(exc) == "BROWSER_DEPENDENCY_MISSING":
            raise
        _CONTEXT["warnings"].append(
            {"code": "SOURCE_UNAVAILABLE", "source": source, "course": course}
        )
        return []


def limited(rows, limit):
    if limit is not None and len(rows) > limit:
        _CONTEXT["warnings"].append(
            {"code": "TRUNCATED", "total_before_limit": len(rows), "limit": limit}
        )
        return rows[:limit]
    return rows


def incomplete_report(warnings: list[dict[str, Any]]) -> list[str]:
    """Name what was missing, rather than only that something was.

    A per-course source failure is invisible in the plain output: the rows that did
    arrive print normally and a single trailing line says results are incomplete. On
    an eight-course account five courses can fail a quizzes fetch and the reader has
    no way to tell which, so an empty result reads as "no quizzes" instead of "not
    read". Group the failures by source so the line stays short when many courses
    fail the same way.
    """
    unavailable: dict[str, list[str]] = {}
    truncated = []
    for warning in warnings:
        if warning.get("code") == "SOURCE_UNAVAILABLE":
            courses = unavailable.setdefault(warning.get("source") or "unknown", [])
            course = warning.get("course")
            if course and course not in courses:
                courses.append(course)
        elif warning.get("code") == "TRUNCATED":
            truncated.append(warning)
    lines = ["cmu-cli: incomplete results"]
    for source, courses in unavailable.items():
        scope = f" for {', '.join(courses)}" if courses else ""
        lines.append(f"  {source} unavailable{scope}")
    for warning in truncated:
        lines.append(
            f"  showing {warning.get('limit')} of "
            f"{warning.get('total_before_limit')} rows (--limit)"
        )
    lines.append("  use --json for full source status")
    return lines


def print_json(data: Any, error=None) -> None:
    warnings = _CONTEXT["warnings"]
    status = "error" if error else ("partial" if warnings else "ok")
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "command": _CONTEXT["command"],
                "status": status,
                "complete": status == "ok",
                "retrieved_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "sources": _CONTEXT["sources"],
                "warnings": warnings,
                "error": error,
                "data": data,
            },
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
    )


def select_courses(config: Config, query: str | None) -> list[Course]:
    return [config.find_course(query)] if query else list(config.courses)


def course_files(client: CanvasClient, course: Course) -> list[dict[str, Any]]:
    canvas_rows = [
        {**item, "source": "canvas", "course": course.code}
        for item in fetch(
            "canvas.files", course.code, lambda: client.files(course.canvas_id)
        )
    ]
    return canvas_rows + (
        fetch(
            "public.materials",
            course.code,
            lambda: public_materials(course.code, course.public_site_url),
        )
        if course.public_site_url
        else []
    )


def platform_state(course: dict[str, Any], platform: str) -> str:
    """A Canvas launch tab is not a configured provider URL; say so rather than
    reporting a course as configured when the reader has nothing to read."""
    if course[f"{platform}_configured"]:
        return "configured"
    if course.get(f"{platform}_in_canvas"):
        return "in Canvas, URL not configured"
    return "not configured"


def command_status(
    args: argparse.Namespace, config: Config, client: CanvasClient
) -> int:
    result: dict[str, Any] = {
        "edge_running": None,
        "canvas_tab": None,
        "canvas_authenticated": False,
        "canvas_auth_method": client.auth_method,
        "courses": [],
    }
    try:
        live_courses = {course["id"]: course for course in client.list_courses()}
        result["canvas_authenticated"] = True
        for course in config.courses:
            live = live_courses.get(course.canvas_id)
            platforms = discovered_platform_urls(client, course)
            result["courses"].append(
                {
                    "code": course.code,
                    "canvas": "ok" if live else "missing",
                    "piazza_configured": bool(platforms["piazza"]),
                    "gradescope_configured": bool(platforms["gradescope"]),
                    "piazza_in_canvas": bool(platforms["piazza_canvas_tab"]),
                    "gradescope_in_canvas": bool(platforms["gradescope_canvas_tab"]),
                }
            )
    except (CanvasError, BrowserError):
        result["error"] = "SOURCE_UNAVAILABLE"
        _CONTEXT["warnings"].append(
            {"code": "SOURCE_UNAVAILABLE", "source": "canvas.status"}
        )
    if args.json:
        print_json(result)
    else:
        auth_label = result["canvas_auth_method"]
        print(f"Canvas authenticated: {result['canvas_authenticated']} ({auth_label})")
        if result.get("error"):
            print(f"Reason: {result['error']}")
        for course in result["courses"]:
            states = " ".join(
                f"{name.title()}={platform_state(course, name)}"
                for name in ("piazza", "gradescope")
            )
            print(f"◆ {course['code']} Canvas={course['canvas']} {states}")
    return 3 if result.get("error") else 0


def command_courses(
    args: argparse.Namespace, config: Config, client: CanvasClient
) -> int:
    live = {course["id"]: course for course in client.list_courses()}
    rows = []
    for course in config.courses:
        record = live.get(course.canvas_id, {})
        rows.append(
            {
                "code": course.code,
                "name": course.name,
                "canvas_id": course.canvas_id,
                "available": bool(record),
                "canvas_url": f"{config.canvas_base_url}/courses/{course.canvas_id}",
                "piazza_url": course.piazza_url,
                "gradescope_url": course.gradescope_url,
            }
        )
    if args.json:
        print_json(rows)
    else:

        def plain():
            for row in rows:
                print(f"◆ {row['code']} — {row['name']} [Canvas {row['canvas_id']}]")
                print(f"  {row['canvas_url']}")

        style.render(
            "Courses",
            [
                ("Code", "code"),
                ("Name", "name"),
                ("Canvas", "canvas_id"),
                ("URL", "canvas_url"),
            ],
            rows,
            plain,
            empty="No courses configured.",
        )
    return 0


def assignment_rows(
    client: CanvasClient, courses: list[Course]
) -> list[dict[str, Any]]:
    rows = []
    gradescope = GradescopeClient(browser_auth=getattr(client, "browser_auth", None))
    for course in courses:
        for assignment in fetch(
            "canvas.assignments",
            course.code,
            lambda course=course: client.assignments(course.canvas_id),
        ):
            submission = assignment.get("submission") or {}
            rows.append(
                {
                    "source": "canvas",
                    "id": str(assignment["id"])
                    if assignment.get("id") is not None
                    else None,
                    "course": course.code,
                    "name": assignment.get("name"),
                    "due_at": timestamp(assignment.get("due_at")),
                    "due_at_raw": assignment.get("due_at"),
                    "unlock_at": timestamp(assignment.get("unlock_at")),
                    "lock_at": timestamp(assignment.get("lock_at")),
                    "due_local": format_time(assignment.get("due_at")),
                    "submitted": submission_state(
                        submission.get("workflow_state"), submission.get("submitted_at")
                    ),
                    "submission": submission,
                    "status": submission.get("workflow_state") or "unknown",
                    "points_possible": assignment.get("points_possible"),
                    "url": assignment.get("html_url"),
                    "description": clean_html(assignment.get("description")),
                }
            )
        for assignment in (
            fetch(
                "gradescope.assignments",
                course.code,
                lambda course=course: gradescope.assignments(course),
            )
            if course.gradescope_url
            else []
        ):
            rows.append(
                {
                    "source": "gradescope",
                    "id": str(assignment.get("id") or assignment.get("url"))
                    if assignment.get("id") or assignment.get("url")
                    else None,
                    "course": course.code,
                    "name": assignment.get("name"),
                    "due_at": timestamp(assignment.get("due_at")),
                    "due_at_raw": assignment.get("due_at"),
                    "unlock_at": timestamp(assignment.get("unlock_at")),
                    "lock_at": timestamp(assignment.get("lock_at")),
                    "due_local": assignment.get("released_due")
                    or format_time(assignment.get("due_at")),
                    "submitted": submission_state(assignment.get("status")),
                    "status": assignment.get("status") or "unknown",
                    "points_possible": None,
                    "url": assignment.get("url"),
                    "description": "",
                }
            )
    rows.sort(
        key=lambda item: (
            item["due_at"] is None,
            item["due_at"] or "",
            item["course"],
            item["source"],
        )
    )
    return rows


def command_assignments(
    args: argparse.Namespace, config: Config, client: CanvasClient
) -> int:
    rows = assignment_rows(client, select_courses(config, args.course))
    if args.json:
        print_json(rows)
    elif not rows:
        print("No matching assignments returned.")
    else:

        def plain():
            for item in rows:
                print(
                    f"◆ {item['course']} | {item['name']} | {item['status']} | {item['source']}"
                )
                print(f"  Due: {item['due_local']}")
                print(f"  {item['url']}")

        style.render(
            "Assignments",
            [
                ("Course", "course"),
                ("Assignment", "name"),
                ("Due", "due_local"),
                ("Status", "status"),
                ("Source", "source"),
                ("URL", "url"),
            ],
            rows,
            plain,
            empty="No matching assignments returned.",
            styles={"status": style.status_markup},
        )
    return 0


def quiz_rows(client: CanvasClient, courses: list[Course]) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for course in courses:
        for quiz in fetch(
            "canvas.quizzes",
            course.code,
            lambda course=course: client.quizzes(course.canvas_id),
        ):
            row = {
                "source": "canvas",
                "course": course.code,
                "id": str(quiz["id"]) if quiz.get("id") is not None else None,
                "name": quiz.get("title"),
                "kind": "quiz",
                "due_at": timestamp(quiz.get("due_at")),
                "due_local": format_time(quiz.get("due_at")),
                "unlock_at": timestamp(quiz.get("unlock_at")),
                "lock_at": timestamp(quiz.get("lock_at")),
                "dates_raw": {
                    k: quiz.get(k) for k in ("due_at", "unlock_at", "lock_at")
                },
                "starts_at": timestamp(quiz.get("unlock_at")),
                "starts_local": format_time(quiz.get("unlock_at")),
                "url": quiz.get("html_url"),
                "published": bool(quiz.get("published")),
            }
            rows[(course.code, "canvas", row["id"] or str(row["url"]))] = row
        for row in (
            fetch(
                "public.quizzes",
                course.code,
                lambda course=course: public_quizzes(
                    course.code,
                    course.public_site_url,
                    course.assessment_year,
                    course.assessment_hour,
                ),
            )
            if course.public_site_url
            else []
        ):
            normalized_row = {
                **row,
                "id": row.get("id"),
                "starts_at_raw": row.get("starts_at"),
                "starts_at": timestamp(row.get("starts_at")),
                "time_inferred": True,
                "due_at": None,
                "due_local": format_time(None),
                "unlock_at": None,
                "lock_at": None,
            }
            rows.setdefault(
                (
                    course.code,
                    str(normalized_row["name"]),
                    str(normalized_row["starts_at"]),
                ),
                normalized_row,
            )
    return sorted(
        rows.values(),
        key=lambda item: (item.get("starts_at") or "", item.get("course") or ""),
    )


def quiz_time(item: dict[str, Any]) -> str:
    """Name the time being shown; a quiz's open time is not its deadline.

    The plain line used to print the unlock time in the position a reader scans for a
    deadline, unlabelled, so a quiz that opened in July read as though it were due
    then -- and a quiz with a real due date and no unlock time read as having none.
    """
    if item.get("due_at"):
        return f"due {item['due_local']}"
    if item.get("starts_at"):
        if item.get("source") == "course_site" and item.get("time_inferred"):
            return f"starts (inferred) {item['starts_local']}"
        return f"opens {item['starts_local']}"
    return f"due {item['due_local']}"


def command_quizzes(
    args: argparse.Namespace, config: Config, client: CanvasClient
) -> int:
    rows = quiz_rows(client, select_courses(config, args.course))
    if args.json:
        print_json(rows)
    elif not rows:
        print("No quizzes returned.")
    else:
        for item in rows:
            print(
                f"◆ {item['course']} | {item['name']} | {item.get('kind', 'quiz')} | {quiz_time(item)} | {item['source']}"
            )
            if item.get("url"):
                print(f"  {item['url']}")
    return 0


def command_materials(
    args: argparse.Namespace, config: Config, client: CanvasClient
) -> int:
    rows = []
    for course in select_courses(config, args.course):
        for item in course_files(client, course):
            rows.append(
                {
                    "source": item.get("source") or "canvas",
                    "course": course.code,
                    "id": str(item["id"]) if item.get("id") is not None else None,
                    "name": item.get("display_name") or item.get("filename"),
                    "size": item.get("size"),
                    "updated_at": timestamp(item.get("updated_at")),
                    "updated_at_raw": item.get("updated_at"),
                    "url": item.get("url"),
                    "path": None,
                }
            )
        for path in local_material_paths(course_root(config, course)):
            rows.append(
                {
                    "source": "local",
                    "course": course.code,
                    "name": path.name,
                    "size": path.stat().st_size,
                    "updated_at": None,
                    "url": None,
                    "path": str(path),
                }
            )
    rows.sort(key=lambda item: (item["course"], item["source"], item["name"] or ""))
    rows = limited(rows, args.limit)
    if args.json:
        print_json(rows)
    elif not rows:
        print("No materials returned.")
    else:
        for item in rows:
            print(
                f"◆ {item['course']} | {item['name']} | {item['size'] or 0} bytes | {item['source']}"
            )
            if item.get("path"):
                print(f"  {item['path']}")
            elif item.get("updated_at"):
                print(f"  Updated: {format_time(item['updated_at'])}")
    return 0


def command_announcements(
    args: argparse.Namespace, config: Config, client: CanvasClient
) -> int:
    rows = []
    for course in select_courses(config, args.course):
        for item in fetch(
            "canvas.announcements",
            course.code,
            lambda course=course: client.announcements(course.canvas_id),
        ):
            rows.append(
                {
                    "course": course.code,
                    "id": str(item["id"]) if item.get("id") is not None else None,
                    "source": "canvas",
                    "title": item.get("title"),
                    "posted_at": timestamp(item.get("posted_at")),
                    "posted_at_raw": item.get("posted_at"),
                    "posted_local": format_time(item.get("posted_at")),
                    "message": clean_html(item.get("message")),
                    "url": item.get("html_url"),
                }
            )
    rows.sort(key=lambda item: item.get("posted_at") or "", reverse=True)
    rows = limited(rows, args.limit)
    if args.json:
        print_json(rows)
    elif not rows:
        print("No announcements returned.")
    else:
        for item in rows:
            print(f"◆ {item['course']} | {item['title']} | {item['posted_local']}")
            print(f"  {item['message']}")
            print(f"  {item['url']}")
    return 0


def discovered_platform_urls(
    client: CanvasClient, course: Course
) -> dict[str, str | None]:
    """Configured provider URLs, plus any Canvas launch tab for the same provider.

    A Canvas tab labelled "Piazza" is an LTI launch point on the Canvas origin, not a
    Piazza URL: it carries no class network ID, and ``posts`` cannot read a feed from
    it. Reporting it as the provider URL made ``status`` claim Piazza was configured
    for a course where ``posts`` then returned nothing. Keep the launch tab as its own
    signal -- it is worth telling someone the course uses Piazza -- and leave the
    provider URLs to the configuration that the readers actually use.
    """
    result: dict[str, str | None] = {
        "canvas": f"{client.base_url}/courses/{course.canvas_id}",
        "piazza": course.piazza_url,
        "gradescope": course.gradescope_url,
        "piazza_canvas_tab": None,
        "gradescope_canvas_tab": None,
    }
    for tab in client.tabs(course.canvas_id):
        label = (tab.get("label") or "").lower()
        url = tab.get("html_url")
        if not url:
            continue
        for platform in ("piazza", "gradescope"):
            key = f"{platform}_canvas_tab"
            if platform in label and not result[key]:
                result[key] = urljoin(client.base_url, url)
    return result


def command_posts(
    args: argparse.Namespace, config: Config, client: CanvasClient
) -> int:
    courses = select_courses(config, args.course)
    rows = fetch(
        "piazza.posts",
        None,
        lambda: PiazzaClient(
            courses=courses, term=config.term, browser_auth=config.browser_auth
        ).open_class_posts(),
    )
    rows = limited(rows, args.limit)
    if args.json:
        print_json(rows)
    elif not rows:
        print("No posts returned; use --json to inspect source availability.")
    else:
        for row in rows:
            print(f"◆ {row['course']} | {row['title']}")
            print(f"  {row['snippet']}")
    return 0


def command_platforms(
    args: argparse.Namespace, config: Config, client: CanvasClient
) -> int:
    rows = []
    for course in select_courses(config, args.course):
        rows.append({"course": course.code, **discovered_platform_urls(client, course)})
    if args.json:
        print_json(rows)
    else:
        for row in rows:
            print(f"◆ {row['course']}")
            for platform in ["canvas", "piazza", "gradescope"]:
                print(f"  {platform}: {row.get(platform) or 'not configured'}")
                tab = row.get(f"{platform}_canvas_tab")
                if tab and not row.get(platform):
                    print(f"    (opens from Canvas: {tab})")
    return 0


def command_open(args: argparse.Namespace, config: Config, client: CanvasClient) -> int:
    course = config.find_course(args.course) if args.course else None
    if args.platform == "canvas" and not course:
        url = config.canvas_base_url
    elif not course:
        raise UsageError("Piazza and Gradescope require --course.")
    else:
        url = {
            "canvas": f"{config.canvas_base_url}/courses/{course.canvas_id}",
            "piazza": course.piazza_url,
            "gradescope": course.gradescope_url,
        }.get(args.platform)
        if not url:
            raise UsageError(
                f"{course.code} has no configured {args.platform} URL; "
                f"add one to the course entry in your configuration."
            )
    EdgeBrowser().open(url)
    print("Opened configured URL in your default browser.")
    return 0


def command_sync(args: argparse.Namespace, config: Config, client: CanvasClient) -> int:
    summaries = []
    for course in select_courses(config, args.course):
        previous_warnings = len(_CONTEXT["warnings"])
        assignments = client.assignments(course.canvas_id)
        files = course_files(client, course)
        if len(_CONTEXT["warnings"]) != previous_warnings:
            continue
        summaries.append(
            sync_course(
                config,
                course,
                assignments,
                files,
                client.announcements(course.canvas_id),
                client.modules(course.canvas_id),
                download=not args.metadata_only,
                fetch_bytes=client.download_bytes,
            )
        )
    if args.json:
        print_json(summaries)
    else:
        for summary in summaries:
            downloaded = sum(
                item["status"] in {"downloaded", "updated"}
                for item in summary["downloads"]
            )
            unchanged = sum(
                item["status"] in {"unchanged", "deduplicated"}
                for item in summary["downloads"]
            )
            print(
                f"◆ {summary['course']}: assignments {summary['assignments']}, files {summary['files']}, announcements {summary['announcements']}, downloaded {downloaded}, unchanged {unchanged}"
            )
            print(f"  {summary['root']}")
    return 0


def command_provider(args):
    from .ed_client import EdAuthError, EdClient, EdError
    from .models import load_provider_config
    from .sio_client import SIOClient, SIOError

    config = load_provider_config(args.config)
    source = args.command if args.command == "sio" else "ed." + args.action
    record = {
        "source": source,
        "course": getattr(args, "course_id", None),
        "status": "ok",
    }
    _CONTEXT["sources"].append(record)
    error = None
    try:
        if args.command == "sio":
            client = SIOClient(browser_auth=config.browser_auth)
            readers = {
                "schedule": "semester_schedule",
                "waitlist-history": "waitlist_history",
                "probe": "probe",
            }
            result = getattr(client, readers[args.action])()
            record.update(status=result["status"], provenance=result["provenance"])
        else:
            with EdClient(base_url=config.ed_api_base_url) as client:
                if args.action == "courses":
                    result = client.courses()
                elif args.action in {"thread", "replies"}:
                    result = getattr(client, args.action)(
                        args.id, course_id=args.course_id
                    )
                else:
                    kwargs = {"page_size": args.page_size, "max_pages": args.max_pages}
                    result = (
                        client.search(args.course_id, args.query, **kwargs)
                        if args.action == "search"
                        else client.threads(args.course_id, **kwargs)
                    )
        if isinstance(result, dict) and result.get("complete") is False:
            if args.command == "ed":
                record["status"] = "partial"
            for code in result.get("warnings") or ["SOURCE_INCOMPLETE"]:
                _CONTEXT["warnings"].append({"code": code, "source": source})
    except BrowserDependencyMissing:
        record["status"] = "error"
        raise
    except (EdError, SIOError) as exc:
        record["status"] = "error"
        code = (
            "ED_AUTH_REQUIRED" if isinstance(exc, EdAuthError) else "SOURCE_UNAVAILABLE"
        )
        partial = getattr(exc, "partial", None)
        if partial is not None:
            # A failed search carries unsearched listing records, not matches.
            result = {"partial_listing": partial}
            _CONTEXT["warnings"].append({"code": code, "source": source})
        else:
            result = None
            error = {
                "code": code,
                # An Ed authorization message is one of a few literals authored in
                # ed_client, so it carries no provider detail -- and it is the only
                # text that says what to do. Reporting it here keeps `ed courses`
                # consistent with `ed threads`, which already surfaced the same
                # sentence through its partial listing.
                "message": str(exc)
                if isinstance(exc, EdAuthError)
                else "Check provider authorization and availability.",
            }
    if args.json:
        print_json(result, error=error)
    elif error is not None:
        print(f"cmu-cli: {error['code']}\n  {error['message']}", file=sys.stderr)
    else:
        lines = (
            sio_lines(result)
            if args.command == "sio"
            else ed_lines(args.action, result)
        )
        print("\n".join(lines))
        if (
            args.command == "ed"
            and isinstance(result, dict)
            and result.get("complete") is False
        ):
            print(f"  incomplete: {_text(result.get('reason'), 'unknown')}")
        if _CONTEXT["warnings"]:
            print(
                "cmu-cli: incomplete results; use --json for source status",
                file=sys.stderr,
            )
    return 2 if error else (3 if _CONTEXT["warnings"] else 0)


def _text(value: Any, fallback: str = "?") -> str:
    text = str(value).strip() if value not in (None, "") else ""
    return text or fallback


def ed_lines(action: str, result: Any) -> list[str]:
    """Readable Ed output. --json still returns the provider shape untouched."""
    if action == "courses":
        rows = result if isinstance(result, list) else []
        if not rows:
            return ["No Ed courses returned."]
        lines = []
        for row in rows:
            course = row.get("course") or {}
            role = (row.get("role") or {}).get("role")
            suffix = f" ({role})" if role else ""
            lines.append(
                f"◆ {_text(course.get('code'))} — {_text(course.get('name'), '')}"
                f" [Ed {_text(course.get('id'))}]{suffix}".replace(" — ", " — ", 1)
            )
        return lines
    if action == "thread":
        row = result if isinstance(result, dict) else {}
        return [
            f"◆ #{_text(row.get('number'))} {_text(row.get('title'), 'untitled')}",
            f"  category: {_text(row.get('category'), 'none')}"
            f" | created: {format_time(row.get('created_at'))}"
            f" | replies: {_text(row.get('reply_count'), '0')}",
            f"  {clean_html(row.get('document') or row.get('content')) or 'No body.'}",
        ]
    result = result if isinstance(result, dict) else {}
    prefix = []
    if "partial_listing" in result:
        # A failed read carries the records it did collect, not matches.
        result = result.get("partial_listing") or {}
        prefix = [f"Partial read: {_text(result.get('reason'), 'unknown reason')}"]
    items = result.get("items")
    if items is None:
        return prefix + [json.dumps(result, ensure_ascii=False, indent=2)]
    if not items:
        return prefix + ["Nothing returned."]
    lines = list(prefix)
    for row in items:
        if action == "replies":
            user = (row.get("user") or {}).get("name")
            lines.append(
                f"◆ reply {_text(row.get('id'))} by {_text(user, 'unknown')}"
                f" | {format_time(row.get('created_at'))}"
            )
            body = clean_html(row.get("document") or row.get("content"))
            if body:
                lines.append(f"  {body}")
        else:
            lines.append(
                f"◆ #{_text(row.get('number'))} {_text(row.get('title'), 'untitled')}"
                f" | {_text(row.get('category'), 'no category')}"
                f" | {format_time(row.get('created_at'))}"
            )
    return lines


def sio_lines(result: dict[str, Any]) -> list[str]:
    """Readable SIO output for the two parsed views and the readiness probe."""
    view = result.get("view")
    rows = result.get("schedule") or result.get("waitlist_history") or []
    header = (
        f"◆ SIO {_text(view, 'probe')} | status: {_text(result.get('status'))}"
        f" | term: {_text(result.get('term'), 'unknown')}"
    )
    lines = [header]
    if view == "semester_schedule":
        for row in rows:
            lines.append(
                f"  {_text(row.get('course_code'))} {_text(row.get('section'), '')}"
                f" — {_text(row.get('title'), 'untitled')}"
            )
            lines.append(
                f"    {_text(row.get('dates'), 'dates unknown')}"
                f" | {_text(row.get('times'), 'times unknown')}"
                f" | {_text(row.get('building_room'), 'room unknown')}"
                f" | {', '.join(row.get('instructors') or ['instructor unknown'])}"
            )
    elif view == "waitlist_history":
        for row in rows:
            lines.append(
                f"  {_text(row.get('course_code'))} {_text(row.get('section'), '')}"
                f" | on: {_text(row.get('on_date'), 'none')}"
                f" | off: {_text(row.get('off_date'), 'none')}"
                f" | confirmed: {_text(row.get('confirm_date'), 'none')}"
            )
    if view and not rows:
        # An empty template is unknown, not a verified empty schedule.
        lines.append("  No rows parsed. This is not a verified empty result.")
    seen, parsed = result.get("rows_seen"), result.get("rows_parsed")
    if seen is not None:
        lines.append(f"  rows parsed: {parsed} of {seen} seen")
    for warning in result.get("warnings") or []:
        lines.append(f"  warning: {_text(warning)}")
    if (result.get("provenance") or {}).get("url"):
        lines.append(f"  source: {result['provenance']['url']}")
    return lines


def ed_identifier(value):
    from .models import validate_ed_id

    try:
        return validate_ed_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


# argparse lists a subcommand in the help body only when add_parser was given a help
# string. These were built in loops without one, so the commands this tool exists for
# -- assignments, courses, sync -- appeared in the usage line and nowhere else.
COMMAND_HELP = {
    "status": "Report Canvas authentication and per-course platform configuration",
    "courses": "List configured courses and their Canvas availability",
    "assignments": "Read Canvas and Gradescope assignments with submission state",
    "quizzes": "Read Canvas quizzes and their deadlines",
    "platforms": "Show the configured Canvas, Piazza and Gradescope links",
    "materials": "List Canvas files and already-synced local materials",
    "announcements": "Read Canvas course announcements",
    "posts": "Read configured Piazza class feeds",
    "sync": "Download Canvas materials and write local Markdown indexes",
    "open": "Open a configured course link in your default browser",
}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="cmu-cli",
        description="Unofficial read-only coursework queries and local sync",
    )
    root.add_argument("--config", type=Path, help="User JSON config path")
    root.add_argument("--version", action="version", version="cmu-cli 0.1.0")
    commands = root.add_subparsers(dest="command", required=True)
    ed = commands.add_parser(
        "ed", help="Read-only Ed API token queries (no Canvas required)"
    )
    actions = ed.add_subparsers(dest="action", required=True)
    for name in ("courses", "threads", "thread", "replies", "search"):
        action = actions.add_parser(
            name, help="Local listing search" if name == "search" else None
        )
        action.add_argument("--json", action="store_true")
        if name != "courses":
            action.add_argument(
                "--course-id",
                type=ed_identifier,
                required=name in {"threads", "search"},
            )
        if name in {"thread", "replies"}:
            action.add_argument(
                "--id",
                type=ed_identifier,
                required=True,
                help="Global thread ID, not course-local number",
            )
        if name in {"threads", "search"}:
            action.add_argument(
                "--page-size",
                type=positive_limit,
                choices=range(1, 101),
                default=100,
                metavar="N",
                help="Threads per page (1–100; default: 100)",
            )
            action.add_argument(
                "--max-pages",
                type=positive_limit,
                choices=range(1, 1001),
                default=100,
                metavar="N",
                help="Maximum pages (1–1000; default: 100)",
            )
        if name == "search":
            action.add_argument("--query", required=True)
    sio = commands.add_parser(
        "sio",
        help="SIO readiness and selected semester table; no enrollment/current waitlist query",
    )
    probes = sio.add_subparsers(dest="action", required=True)
    probe = probes.add_parser("probe", help="Read portal readiness; always incomplete")
    probe.add_argument("--json", action="store_true")
    schedule = probes.add_parser(
        "schedule", help="Read selected semester table; rendered HTML may be required"
    )
    schedule.add_argument("--json", action="store_true")
    history = probes.add_parser(
        "waitlist-history", help="Read historical waitlist entries, not current queue"
    )
    history.add_argument("--json", action="store_true")
    configuration = commands.add_parser("config", help="Offline configuration tools")
    configuration.add_argument("action", choices=["init", "validate", "browsers"])
    configuration.add_argument("--output", type=Path, default=Path("cmu-cli.json"))
    configuration.add_argument("--json", action="store_true")
    auth = commands.add_parser("auth", help="Offline OAuth registration preflight only")
    auth.add_argument("action", choices=["check-registration"])
    auth.add_argument("--registration", type=Path, required=True)
    auth.add_argument("--json", action="store_true")
    doctor = commands.add_parser(
        "doctor", help="Offline diagnostics; does not authenticate"
    )
    doctor.add_argument("--json", action="store_true")
    demo = commands.add_parser(
        "demo", help="Offline synthetic assignments, indexes and repeat-sync demo"
    )
    demo.add_argument("--json", action="store_true")
    for name in ["status", "courses"]:
        command = commands.add_parser(name, help=COMMAND_HELP[name])
        command.add_argument("--json", action="store_true")
    for name in ["assignments", "quizzes", "platforms"]:
        command = commands.add_parser(name, help=COMMAND_HELP[name])
        command.add_argument("--course")
        command.add_argument("--json", action="store_true")
    for name in ["materials", "announcements", "posts"]:
        command = commands.add_parser(name, help=COMMAND_HELP[name])
        command.add_argument("--course")
        command.add_argument("--limit", type=positive_limit, default=None)
        command.add_argument("--json", action="store_true")
    sync = commands.add_parser("sync", help=COMMAND_HELP["sync"])
    sync.add_argument("--course")
    sync.add_argument("--metadata-only", action="store_true")
    sync.add_argument("--json", action="store_true")
    open_command = commands.add_parser("open", help=COMMAND_HELP["open"])
    open_command.add_argument("platform", choices=["canvas", "piazza", "gradescope"])
    open_command.add_argument("--course")
    return root


def positive_limit(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("limit must be positive")
    return number


def main() -> None:
    args = parser().parse_args()
    _CONTEXT.update(command=args.command, warnings=[], sources=[])
    if args.command == "auth":
        from .auth import RegistrationError, check_registration

        try:
            result = check_registration(args.registration)
        except RegistrationError as exc:
            error = {
                "code": str(exc),
                "message": "See docs/auth.md; supply non-secret registration metadata only.",
            }
            if args.json:
                print_json(None, error=error)
            print("cmu-cli: " + str(exc), file=sys.stderr)
            raise SystemExit(2) from None
        _CONTEXT["warnings"].append({"code": "OAUTH_ONBOARDING_NOT_IMPLEMENTED"})
        if args.json:
            print_json(result)
        else:
            print("Registration metadata valid; provider registration is NOT verified.")
            print(
                "OAuth onboarding is not implemented. See docs/auth.md for prerequisites."
            )
        raise SystemExit(3)
    try:
        with sanitized_errors():
            if args.command in {"ed", "sio"}:
                _CONTEXT["command"] = args.command + " " + args.action
                raise SystemExit(command_provider(args))
            if args.command == "demo":
                from .demo import run_demo

                result = run_demo()
                if args.json:
                    print_json(result)
                else:
                    print("Offline synthetic demo: " + ", ".join(result["statuses"]))
                    print(result["assignment_index"])
                    print("Generated exports (temporary; removed after demo):")
                    print("\n".join(result["exports"]))
                return
            if args.command == "config" and args.action == "browsers":
                from .browser_profiles import candidate_databases

                rows = candidate_databases()
                if args.json:
                    print_json(rows)
                elif not rows:
                    print(
                        "No Edge or Chrome cookie database found in the standard "
                        "locations.\nSafari and Firefox are not supported; see "
                        "docs/browser-auth.md to name a database explicitly."
                    )
                else:
                    print(
                        "Cookie databases found. Nothing was opened or decrypted; "
                        "choose one yourself:\n"
                    )
                    for index, row in enumerate(rows, 1):
                        print(
                            f"  [{index}] {row['browser']} / {row['profile']}\n"
                            f"      {row['cookie_file']}"
                        )
                    chosen = rows[0]
                    print(
                        "\nAdd the one you are signed in with to your configuration, "
                        "listing only the hosts you want read:\n\n"
                        '  "browser_auth": {\n'
                        '    "enabled": true,\n'
                        f'    "browser": "{chosen["browser"]}",\n'
                        f'    "cookie_file": "{chosen["cookie_file"]}",\n'
                        '    "hosts": ["piazza.com", "www.gradescope.com"]\n'
                        "  }"
                    )
                return
            if args.command == "config" and args.action == "init":
                template = (
                    resources.files("cmu_cli")
                    .joinpath("config.example.json")
                    .read_text(encoding="utf-8")
                )
                with args.output.open("x", encoding="utf-8") as handle:
                    handle.write(template)
                result = {"created": str(args.output), "synthetic_template": True}
                print_json(result) if args.json else print(
                    "Created template; edit institution URL and course IDs before live use."
                )
                return
            config = load_config(args.config)
            if args.command in {"config", "doctor"}:
                result = {
                    "valid": True,
                    "version": "0.1.0",
                    "network_used": False,
                    "authentication_checked": False,
                    "courses": len(config.courses),
                    "capabilities": {
                        "offline_demo": True,
                        "canvas": "environment token or explicit browser_auth",
                        "piazza": "explicit browser_auth and class URL",
                        "gradescope": "explicit browser_auth and course URL",
                        "ed": "explicit CMU_CLI_ED_TOKEN; local listing search",
                        "sio": "probe and selected semester table; rendered HTML may be required",
                        "browser_open": True,
                    },
                }
                print_json(result) if args.json else print(
                    "Configuration valid. Offline checks only; authentication and service permission not verified."
                )
                return
            from . import storage

            storage.LOCAL_TZ = ZoneInfo(config.timezone)
            client = (
                CanvasClient(config.canvas_base_url, browser_auth=config.browser_auth)
                if args.command not in {"posts", "open"}
                else None
            )
            if getattr(args, "course", None):
                config.find_course(args.course)
            commands = {
                name: globals()["command_" + name]
                for name in (
                    "status",
                    "courses",
                    "assignments",
                    "quizzes",
                    "materials",
                    "announcements",
                    "posts",
                    "platforms",
                    "open",
                    "sync",
                )
            }
            code = commands[args.command](args, config, client)
            if _CONTEXT["warnings"]:
                if not getattr(args, "json", False):
                    print(
                        "\n".join(incomplete_report(_CONTEXT["warnings"])),
                        file=sys.stderr,
                    )
                code = 3
            raise SystemExit(code)
    except UsageError as exc:
        if getattr(args, "json", False):
            print_json(None, error={"code": "USAGE", "message": str(exc)})
        print(f"cmu-cli: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except OperationError as exc:
        code = str(exc)
        error = {"code": code, "message": ERROR_HINTS[code]}
        if getattr(args, "json", False):
            print_json(None, error=error)
        print(f"cmu-cli: {code}\n  {ERROR_HINTS[code]}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
