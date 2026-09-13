"""Configured student grade reads with observed-only, explicitly partial feedback."""

import json

from .gradescope_client import GradescopeError
from .gradescope_details import (
    _course_url,
    _detail_url,
    parse_submission_details,
    read_course_grades,
)
from .web_session import (
    MAX_DOCUMENT_BYTES,
    bounded_content,
    browser_cookie_session,
    close_response,
    https_origin,
    safe_request,
)


def read_detail(session, course, url):
    # The command calls this only for links parsed from this course's table.
    if not _detail_url(course, url):
        raise GradescopeError("Detail must be a same-course student URL")
    response = safe_request(
        session, url, origin=https_origin(_course_url(course)), allowed_urls=(url,)
    )
    if response.status_code != 200:
        close_response(response)
        raise GradescopeError("Gradescope detail unavailable")
    result = parse_submission_details(
        course, url, bounded_content(response, MAX_DOCUMENT_BYTES).decode("utf-8")
    )
    result["provenance"]["mode"] = "observed_course_link_get"
    return result


def course_grades(course, browser_auth, details):
    from . import cli

    base = _course_url(course)
    with browser_cookie_session(https_origin(base)[1], browser_auth) as session:
        rows = read_course_grades(session, course)
        if details:
            cache = {}
            for row in rows:
                row["details"] = []
                row["details_status"] = "partial"
                for url in row["detail_links"]:
                    if url not in cache:
                        cache[url] = cli.fetch(
                            "gradescope.details",
                            course.code,
                            lambda url=url: read_detail(session, course, url),
                        )
                    if cache[url]:
                        row["details"].append(cache[url])
            cli._CONTEXT["warnings"].append(
                {
                    "code": "FEEDBACK_PARTIAL",
                    "source": "gradescope.details",
                    "course": course.code,
                    "reason": "Observed rendered HTML only; no complete rubric or attempt coverage",
                }
            )
        return rows


def command_gradescope(args, config):
    from . import cli

    result = {
        "assignments": [],
        "enrollments": [],
        "scope": "Configured Gradescope student assignment tables; not SIO or course totals",
    }
    for course in cli.select_courses(config, args.course):
        rows = cli.fetch(
            "gradescope.grades",
            course.code,
            lambda course=course: course_grades(
                course, config.browser_auth, args.details
            ),
        )
        result["assignments"].extend(rows)
    if args.json:
        cli.print_json(result)
    else:
        for row in result["assignments"]:
            print(
                f"◆ {row['course']} | {row['name']} | "
                f"score={cli._text(row['score'])}/{cli._text(row['points_possible'])} | "
                f"{row['submission_state']} | release={row['release_state']}"
            )
            if args.details:
                print(
                    "  Feedback (partial): "
                    + json.dumps(row["details"], ensure_ascii=True)
                )
        if not result["assignments"]:
            print("No Gradescope grades returned; inspect source status.")
    return 3 if cli._CONTEXT["warnings"] else 0
