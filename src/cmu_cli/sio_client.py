"""Read-only SIO portal and verified semester/history HTML views.

A plan's scheduled units are NOT evidence of registered/enrolled courses.
History records are not current waitlist positions. See docs/sio.md.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .web_session import (
    MAX_DOCUMENT_BYTES,
    SessionError,
    bounded_content,
    browser_cookie_session,
    close_response,
    configured_session,
    https_origin,
    safe_request,
)

SEMESTER_SCHEDULE_URL = "https://s3.andrew.cmu.edu/sio/mpa/semesterschedule"
WAITLIST_HISTORY_URL = "https://s3.andrew.cmu.edu/sio/mpa/schedule/waitlisthistory"
SIO_URL = "https://s3.andrew.cmu.edu/sio/"
SIO_ORIGIN = https_origin(SIO_URL)
PLAN_URL = SIO_URL + "#schedule-plan"
REGISTRATION_URL = SIO_URL + "#schedule-registration"
PAGE_TITLE = "CMU - Student Information Online"


class SIOError(RuntimeError):
    """Sanitized SIO input or transport failure."""


def _result(url: str, method: str, status: str, warnings: list[str]) -> dict:
    return {
        "source": "sio",
        "status": status,
        "complete": False,
        "provenance": {"url": url, "method": method},
        "warnings": warnings,
        "view": None,
        "term": None,
        "planned_units": None,
        # None means unknown, never a verified empty enrollment or waitlist.
        "planned_courses": None,
        "registered_courses": None,
        "schedule": None,
        "waitlist": None,
    }


def parse_snapshot(*, url: str, title: str, text: str) -> dict:
    """Parse caller-supplied visible document text, without browser execution.

    Input is a local snapshot contract, NOT an asserted SIO API schema. Caller
    must capture location.href, document.title and document.body.innerText from
    the same user-selected page. No raw page text is retained in the result.
    Only the verified plan heading and Units Scheduled label are interpreted.
    """
    if not all(isinstance(value, str) for value in (url, title, text)):
        raise SIOError("Snapshot url, title and text must be strings")
    try:
        valid_origin = https_origin(url) == SIO_ORIGIN
        parsed = urlsplit(url)
    except SessionError:
        raise SIOError("Snapshot must use the exact SIO HTTPS origin") from None
    if (
        not valid_origin
        or parsed.path != "/sio/"
        or parsed.query
        or parsed.fragment not in {"schedule-plan", "schedule-registration"}
    ):
        raise SIOError("Snapshot must identify a verified SIO schedule route")
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise SIOError("Snapshot exceeds size limit")
    result = _result(
        PLAN_URL if parsed.fragment == "schedule-plan" else REGISTRATION_URL,
        "user_supplied_visible_text",
        "discovery_required",
        ["COURSE_ROWS_UNVERIFIED", "TERM_UNVERIFIED"],
    )
    if title.strip() != PAGE_TITLE:
        result["warnings"].append("PAGE_IDENTITY_UNVERIFIED")
        return result
    if parsed.fragment == "schedule-registration":
        result["view"] = "registration"
        result["warnings"].append("REGISTRATION_DOM_UNVERIFIED")
        return result
    if "Plan Course Schedule" not in text:
        result["warnings"].append("PLAN_HEADING_MISSING")
        return result
    result["view"] = "planned"
    # Anchor to a complete line: reject malformed numbers and unrelated prose.
    units = re.findall(
        r"(?m)^\s*Units Scheduled\s*::\s*([0-9]+(?:\.[0-9]+)?)\s*$",
        text,
    )
    if len(units) == 1 and len(units[0]) <= 32:
        result["planned_units"] = units[0]  # preserve source decimal text
        result["status"] = "partial"
    else:
        result["warnings"].append("PLANNED_UNITS_MISSING_OR_AMBIGUOUS")
    return result


def _parse_html(html: str, *, history: bool) -> dict:
    if not isinstance(html, str):
        raise SIOError("HTML snapshot must be a string")
    if len(html.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise SIOError("Snapshot exceeds size limit")
    url = WAITLIST_HISTORY_URL if history else SEMESTER_SCHEDULE_URL
    result = _result(url, "user_supplied_html", "rendered_snapshot_required", [])
    result["view"] = "waitlist_history" if history else "semester_schedule"
    result["waitlist_history"] = None
    result["term_code"] = None
    result["rows_seen"] = 0
    result["rows_parsed"] = 0
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script, style, template"):
        node.decompose()
    heading = "Waitlist History" if history else "Semester Schedule"
    if (
        not soup.title
        or "Student Information Online" not in soup.title.get_text()
        or not any(
            h.get_text(" ", strip=True) == heading for h in soup.select("h1,h2,h3,h4")
        )
    ):
        result["warnings"].append("PAGE_IDENTITY_UNVERIFIED")
        return result
    select = soup.select_one(
        "#semesters-select" if history else "#semester-code-select"
    )
    if select:
        options = select.select("option[selected]")
        # A single available option is unambiguous even in unrendered HTML.
        if not options and len(select.select("option")) == 1:
            options = select.select("option")
        if len(options) == 1 and options[0].get("value"):
            result["term"] = options[0].get_text(" ", strip=True) or None
            result["term_code"] = options[0]["value"]
    if not result["term"]:
        result["warnings"].append("SELECTED_TERM_UNVERIFIED")
    tables = soup.select(
        "table.schedule-waitlist-tbl" if history else "table.course-list-tbl"
    )
    if len(tables) != 1:
        result["warnings"].append("TABLE_MISSING_OR_AMBIGUOUS")
        return result
    labels = (
        ["Course", "Data Entry By", "On Date", "Removed By", "Off Date", "Confirm Date"]
        if history
        else ["Course", "Instructor", "Dates", "Times", "Bldg/Room"]
    )
    rows = []
    for tr in tables[0].select("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells or all(c.select_one(".grid-hdr") for c in cells):
            continue
        result["rows_seen"] += 1
        mapped = {c.get("data-title"): c for c in cells if c.get("data-title")}
        if not all(label in mapped for label in labels):
            result["warnings"].append("ROW_SCHEMA_UNRECOGNIZED")
            continue
        course = mapped["Course"]
        parts = list(course.stripped_strings)
        match = re.fullmatch(
            r"([0-9]{5})\s+([A-Za-z0-9-]+)", parts[-1] if parts else ""
        )
        if not match:
            result["warnings"].append("COURSE_ID_UNRECOGNIZED")
            continue
        row = {"course_code": match[1], "section": match[2]}
        if history:
            for label, key in zip(
                labels[1:],
                ("data_entry_by", "on_date", "removed_by", "off_date", "confirm_date"),
                strict=True,
            ):
                row[key] = mapped[label].get_text(" ", strip=True) or None
        else:
            row["title"] = " ".join(parts[:-1]) or None
            instructor = mapped["Instructor"]
            row["instructors"] = [
                n.get_text(" ", strip=True)
                for n in instructor.select(".display-block")
                if n.get_text(strip=True)
            ]
            if not row["instructors"]:
                row["instructors"] = [
                    t for t in instructor.stripped_strings if "@" not in t
                ]
            for label, key in (
                ("Dates", "dates"),
                ("Times", "times"),
                ("Bldg/Room", "building_room"),
            ):
                row[key] = mapped[label].get_text(" ", strip=True) or None
            if (
                not row["title"]
                or not row["instructors"]
                or not _unscheduled_row(row)
                and not all(row[k] for k in MEETING_FIELDS)
            ):
                result["warnings"].append("SCHEDULE_FIELDS_MISSING")
        rows.append(row)
    result["rows_parsed"] = len(rows)
    # Empty templates and loading states are not evidence of an empty schedule.
    if not result["rows_seen"]:
        result["warnings"].append("EMPTY_TABLE_UNVERIFIED")
    key = "waitlist_history" if history else "schedule"
    result[key] = rows or None
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    result["complete"] = bool(rows) and not result["warnings"]
    result["status"] = (
        "ok"
        if result["complete"]
        else "partial"
        if rows
        else "rendered_snapshot_required"
    )
    return result


MEETING_FIELDS = ("dates", "times", "building_room")
# SIO writes these where a course has no meeting pattern; an empty cell means the
# same thing. Compared case-insensitively against the cell's collapsed text.
UNSCHEDULED_TEXT = {"", "tba", "tba tba", "tba tba tba", "n/a", "none", "-"}


def _unscheduled_row(row: dict) -> bool:
    """True when the row carries no meeting pattern at all.

    Directed research, independent study and thesis units are normally enrolled and
    normally have no time, room or day. Their cells are found -- a row whose columns
    were not recognised has already been rejected as ROW_SCHEMA_UNRECOGNIZED -- so an
    empty one is the schedule saying "not scheduled", not the parser failing. Treating
    it as a missing field marked the reader's whole semester incomplete, with exit 3,
    over a course that was read correctly.

    A row with *some* meeting fields filled and others empty is still reported: that
    is the shape a real extraction failure would take.
    """
    return all(
        str(row.get(key) or "").strip().lower() in UNSCHEDULED_TEXT
        for key in MEETING_FIELDS
    )


def parse_semester_schedule(html: str) -> dict:
    """Parse verified semester table only; never execute HTML or infer enrollment."""
    return _parse_html(html, history=False)


def parse_waitlist_history(html: str) -> dict:
    """Parse historical events, NOT a current queue or current waitlist status."""
    return _parse_html(html, history=True)


class SIOClient:
    """Read verified SIO views with explicit optional browser_auth config.

    Construction does not access a cookie store. probe() uses a new anonymous
    session by default. Online schedule/history reads require browser_auth,
    passed to the project's opt-in/host/file-validated cookie loader.
    No automatic credential discovery or JavaScript/browser execution occurs.
    """

    def __init__(self, *, browser_auth: dict | None = None, sso=None):
        self.browser_auth = browser_auth
        # Set only from an explicitly enabled configuration block; None keeps the
        # existing cookie-database transport and every guarantee that comes with it.
        self.sso = sso

    def semester_schedule(self) -> dict:
        """Read the default selected semester; no guessed term query parameters."""
        return self._read_view(SEMESTER_SCHEDULE_URL, parse_semester_schedule)

    def waitlist_history(self) -> dict:
        """Read historical waitlist events, not current queue positions."""
        return self._read_view(WAITLIST_HISTORY_URL, parse_waitlist_history)

    def _read_view(self, url, parser) -> dict:
        if self.sso is not None:
            return self._read_view_over_sso(url, parser)
        if self.browser_auth is None:
            result = parser("")
            result.update(
                status="login_required", warnings=["EXPLICIT_BROWSER_AUTH_REQUIRED"]
            )
            result["provenance"]["method"] = "not_requested"
            return result
        try:
            with browser_cookie_session(SIO_ORIGIN[1], self.browser_auth) as session:
                response = safe_request(session, url, origin=SIO_ORIGIN, method="GET")
                if response.status_code != 200:
                    status = response.status_code
                    close_response(response)
                    result = parser("")
                    result.update(
                        status="login_required"
                        if status in {401, 403}
                        else "unavailable",
                        warnings=[f"HTTP_{status}"],
                    )
                else:
                    content = bounded_content(response, MAX_DOCUMENT_BYTES)
                    result = parser(content.decode("utf-8", errors="replace"))
        except SessionError as exc:
            if (
                str(exc)
                == "Authenticated request must remain on its exact HTTPS origin"
            ):
                result = parser("")
                result.update(
                    status="auth_redirect_blocked",
                    warnings=["LOGIN_OR_ORIGIN_REDIRECT_BLOCKED"],
                )
            else:
                raise SIOError("SIO session or transport unavailable") from None
        result["provenance"]["method"] = "https_get"
        return result

    def _read_view_over_sso(self, url, parser) -> dict:
        """Read the page through an explicitly authorized Shibboleth session.

        The cookie-database transport cannot reach these views: the application
        session is established by a redirect through login.cmu.edu, which
        safe_request blocks before transmission. Only the fetch differs -- the same
        parser runs on the same markup, and the same completeness rules apply.
        """
        from .sso_session import NeedsLogin, SsoError

        try:
            html = self.sso.fetch(url)
        except NeedsLogin:
            result = parser("")
            result.update(status="login_required", warnings=["SSO_LOGIN_REQUIRED"])
            result["provenance"]["method"] = "not_requested"
            return result
        except SsoError:
            raise SIOError("SIO session or transport unavailable") from None
        result = parser(html[:MAX_DOCUMENT_BYTES])
        result["provenance"]["method"] = "sso_page_load"
        return result

    def probe(self) -> dict:
        """GET the portal only; refuse any cross-origin authentication redirect."""
        try:
            session = (
                configured_session()
                if self.browser_auth is None
                else browser_cookie_session(SIO_ORIGIN[1], self.browser_auth)
            )
            with session:
                response = safe_request(
                    session,
                    SIO_URL,
                    origin=SIO_ORIGIN,
                    anonymous=self.browser_auth is None,
                )
                if response.status_code != 200:
                    status = response.status_code
                    close_response(response)
                    return _result(
                        SIO_URL,
                        "https_get",
                        "login_required" if status in {401, 403} else "unavailable",
                        [f"HTTP_{status}"],
                    )
                # Bound/close the response, but don't infer SPA state from HTML.
                bounded_content(response, MAX_DOCUMENT_BYTES)
        except SessionError as exc:
            if str(exc) == (
                "Authenticated request must remain on its exact HTTPS origin"
            ):
                return _result(
                    SIO_URL,
                    "https_get",
                    "auth_redirect_blocked",
                    ["LOGIN_OR_ORIGIN_REDIRECT_BLOCKED"],
                )
            raise SIOError("SIO session or transport unavailable") from None
        return _result(
            SIO_URL,
            "https_get",
            "discovery_required",
            [
                "AUTHENTICATION_UNVERIFIED",
                "VISIBLE_SNAPSHOT_REQUIRED",
                "READ_API_UNVERIFIED",
            ],
        )
