"""Conservative Gradescope grades and observed-link discovery.

No detail endpoint enumeration, JavaScript execution, downloads, or inferred totals.
Detail parsing accepts caller-supplied authorized rendered HTML; see provider docs.
"""

from __future__ import annotations

import math
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .gradescope_client import GradescopeClient, GradescopeError
from .models import Course
from .web_session import (
    MAX_DOCUMENT_BYTES,
    SessionError,
    bounded_content,
    close_response,
    https_origin,
    safe_request,
)

_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
_SCORE = re.compile(rf"({_NUMBER})\s*/\s*({_NUMBER})")


def parse_score(status: str) -> dict:
    """Accept only a complete, unambiguous English decimal score pair.

    Locale commas, percentages, letter grades and arbitrary embedded numbers stay
    unknown. Extra credit and negative earned scores are not clamped.
    """
    raw = " ".join(status.split())
    result = {"score": None, "points_possible": None, "release_state": "unknown"}
    match = _SCORE.fullmatch(raw)
    if match:
        score, maximum = map(float, match.groups())
        if math.isfinite(score) and math.isfinite(maximum) and maximum >= 0:
            result.update(
                score=score, points_possible=maximum, release_state="released"
            )
    elif raw.casefold() == "submitted":
        result["release_state"] = "not_released"
    return result


def _course_url(course: Course) -> str:
    if not GradescopeClient.course_id(course) or not course.gradescope_url:
        raise GradescopeError("An explicit Gradescope course URL is required")
    parsed = urlsplit(course.gradescope_url)
    if parsed.query or parsed.fragment:
        raise GradescopeError(
            "Gradescope course URL must not contain query or fragment"
        )
    return course.gradescope_url


def _visible(element) -> bool:
    for node in (element, *element.parents):
        if getattr(node, "attrs", None) is None:
            continue
        style = re.sub(r"\s+", "", str(node.get("style", "")).lower())
        if (
            node.has_attr("hidden")
            or str(node.get("aria-hidden", "")).lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        ):
            return False
    return True


def _observed_url(base: str, href: str) -> str | None:
    if not href or href.startswith("#"):
        return None
    try:
        # Validate raw href too: urljoin otherwise strips leading controls.
        if "\\" in href or any(ord(c) <= 32 or ord(c) == 127 for c in href):
            return None
        url = urljoin(base, href)
        https_origin(url)
        return url
    except (SessionError, ValueError):
        return None


def _detail_url(course: Course, url: str) -> bool:
    base = _course_url(course)
    parsed = urlsplit(url)
    return (
        https_origin(url) == https_origin(base)
        and not parsed.query
        and not parsed.fragment
        and re.fullmatch(
            rf"/courses/{GradescopeClient.course_id(course)}/assignments/[0-9]+"
            r"(?:/submissions/[0-9]+)?/?",
            parsed.path,
        )
        is not None
    )


def parse_course_grades(course: Course, html: str) -> list[dict]:
    """Parse existing student-table selectors; never fabricate a missing URL."""
    base = _course_url(course)
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#assignments-student-table")
    if table is None or soup.select_one('input[type="password"]'):
        raise GradescopeError("Gradescope student assignment table unavailable")
    results = []
    for row in table.select("tbody tr"):
        if not _visible(row):
            continue
        title = row.select_one("[data-assignment-title]") or row.select_one(
            "th[scope='row']"
        )
        if title is None or not _visible(title):
            continue
        name = str(
            title.get("data-assignment-title") or title.get_text(" ", strip=True)
        ).strip()
        if not name:
            continue
        status_node = row.select_one(".submissionStatus--text")
        status = (
            status_node.get_text(" ", strip=True)
            if status_node and _visible(status_node)
            else ""
        )
        score_node = row.select_one(".submissionStatus--score")
        grade = parse_score(
            score_node.get_text(" ", strip=True)
            if score_node and _visible(score_node)
            else status
        )
        normalized = " ".join(status.split()).casefold()
        submission = (
            "submitted"
            if grade["release_state"] == "released" or normalized == "submitted"
            else "not_submitted"
            if normalized in {"no submission", "not submitted"}
            else "unknown"
        )
        links = []
        for anchor in row.select("a[href]"):
            if str(anchor.get("data-method", "get")).lower() != "get":
                continue
            url = _observed_url(base, str(anchor["href"]))
            if (
                url
                and _visible(anchor)
                and _detail_url(course, url)
                and url not in links
            ):
                links.append(url)
        identifier = title.get("data-assignment-id")
        identifier = str(identifier) if identifier is not None else None
        results.append(
            {
                "source": "gradescope",
                "course": course.code,
                "course_id": GradescopeClient.course_id(course),
                "assignment_id": identifier,
                "name": name,
                "status": status,
                **grade,
                "submission_state": submission,
                "url": links[0] if links else None,
                "detail_links": links,
                "provenance": {"url": base, "selector": "#assignments-student-table"},
                "unavailable_reason": None
                if grade["score"] is not None
                else (
                    "not_released"
                    if grade["release_state"] == "not_released"
                    else "no_unambiguous_score_in_status"
                ),
            }
        )
    return results


def read_course_grades(session, course: Course) -> list[dict]:
    """One bounded course GET with caller-authorized session, no cookie discovery."""
    base = _course_url(course)
    try:
        response = safe_request(
            session, base, origin=https_origin(base), allowed_urls=(base,)
        )
        if response.status_code != 200:
            close_response(response)
            raise GradescopeError("Gradescope course unavailable")
        html = bounded_content(response, MAX_DOCUMENT_BYTES).decode("utf-8")
        return parse_course_grades(course, html)
    except (SessionError, UnicodeError):
        raise GradescopeError("Gradescope course read failed") from None


def parse_submission_details(course: Course, submission_url: str, html: str) -> dict:
    """Extract rendered, observed rubric text and links, not a complete gradebook.

    No question/rubric association is guessed: public source establishes selectors
    but loads question rubrics interactively. No hidden React props are decoded.
    """
    try:
        valid = _detail_url(course, submission_url)
    except (SessionError, ValueError):
        valid = False
    if not valid:
        raise GradescopeError("Detail URL must be an observed same-course student URL")
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('input[type="password"]'):
        raise GradescopeError("Gradescope detail is a login document")
    questions = [
        node.get_text(" ", strip=True)
        for node in soup.select(".submissionOutlineQuestion--title")
        if _visible(node)
    ]
    rubric = []
    for node in soup.select(".submissionOutlineRubricItem"):
        if not _visible(node):
            continue
        label_node = node.select_one("span.sr-only")
        label = label_node.get_text(" ", strip=True).casefold() if label_node else ""
        applied = (
            False
            if "unapplied rubric item" in label
            else (True if "applied rubric item" in label else None)
        )
        description = node.select_one(".submissionOutlineRubricItem--description")
        points = node.select_one("[aria-label]")
        rubric.append(
            {
                "description": description.get_text(" ", strip=True)
                if description and _visible(description)
                else None,
                "points_label": str(points["aria-label"])
                if points and _visible(points)
                else None,
                "applied": applied,
                "question": None,
            }
        )
    materials = []
    seen = set()
    for anchor in soup.select("a[href]"):
        if not _visible(anchor):
            continue
        # Rails data-method links and generation buttons are not read-only assets.
        if str(anchor.get("data-method", "get")).lower() != "get":
            continue
        url = _observed_url(submission_url, str(anchor["href"]))
        if not url or url in seen:
            continue
        label = anchor.get_text(" ", strip=True)
        if label.casefold() != "download graded copy":
            continue
        seen.add(url)
        materials.append(
            {
                "source": "gradescope",
                "kind": "graded_submission",
                "url": url,
                "label": label,
                "source_url": submission_url,
                "availability": "observed_link_not_verified",
                "credential_policy": "same_origin_only"
                if https_origin(url) == https_origin(submission_url)
                else "anonymous_only",
                "download_verified": False,
            }
        )
    return {
        "source": "gradescope",
        "course": course.code,
        "url": submission_url,
        "question_titles": questions,
        "rubric_items": rubric,
        "materials": materials,
        "status": "partial",
        "score": None,
        "points_possible": None,
        "feedback_availability": "observed" if rubric else "unknown",
        "unavailable_reason": "rendered_html_only_no_question_association_or_completeness",
        "provenance": {"url": submission_url, "mode": "caller_supplied_html"},
    }
