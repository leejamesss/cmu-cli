"""Read-only Gradescope course assignments and reusable HTML parser."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .models import Course
from .web_session import (
    MAX_DOCUMENT_BYTES,
    SessionError,
    bounded_content,
    browser_cookie_session,
    close_response,
    https_origin,
    safe_request,
)


class GradescopeError(RuntimeError):
    pass


class GradescopeClient:
    def __init__(self, session: requests.Session | None = None, browser_auth=None):
        self.session = session
        self.browser_auth = browser_auth

    @staticmethod
    def course_id(course: Course) -> str | None:
        if not course.gradescope_url:
            return None
        try:
            if https_origin(course.gradescope_url)[2] != 443:
                raise SessionError("Invalid port")
        except SessionError:
            raise GradescopeError("Gradescope URL must use HTTPS") from None
        if urlparse(course.gradescope_url).hostname not in {
            "gradescope.com",
            "www.gradescope.com",
        }:
            raise GradescopeError("Gradescope URL must use the official host")
        match = re.fullmatch(r"/courses/(\d+)/?", urlparse(course.gradescope_url).path)
        return match.group(1) if match else None

    def assignments(self, course: Course) -> list[dict[str, Any]]:
        if not self.course_id(course):
            raise GradescopeError("An explicit Gradescope course URL is required")
        try:
            origin = https_origin(course.gradescope_url)
            session = self.session or browser_cookie_session(
                origin[1], self.browser_auth
            )
            response = safe_request(session, course.gradescope_url, origin=origin)
            if response.status_code != 200:
                close_response(response)
                raise GradescopeError(
                    "Gradescope course unavailable; check browser login and enrollment"
                )
            document = bounded_content(response, MAX_DOCUMENT_BYTES).decode("utf-8")
            return self.parse_assignments(course, document)
        except (SessionError, UnicodeError):
            raise GradescopeError(
                "Gradescope read failed; check selected browser login and configured course"
            ) from None

    @staticmethod
    def parse_assignments(course: Course, html: str) -> list[dict[str, Any]]:
        course_id = GradescopeClient.course_id(course)
        if not course_id or not course.gradescope_url:
            raise GradescopeError("An explicit Gradescope course URL is required")
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("#assignments-student-table")
        if not table:
            raise GradescopeError(f"Gradescope 页面结构异常或无权访问：{course.code}")
        rows: list[dict[str, Any]] = []
        for row in table.select("tbody tr"):
            title_element = row.select_one("[data-assignment-title]") or row.select_one(
                "th[scope='row']"
            )
            name = ""
            if title_element:
                name = str(
                    title_element.get("data-assignment-title")
                    or title_element.get_text(" ", strip=True)
                ).strip()
            if not name:
                continue
            status_element = row.select_one(".submissionStatus--text")
            due_element = row.select_one("time.submissionTimeChart--dueDate[datetime]")
            if due_element is None:
                times = row.select("time[datetime]")
                due_element = times[-1] if times else None
            link = row.select_one("a[href]")
            assignment_id = (
                title_element.get("data-assignment-id") if title_element else None
            )
            if link:
                assignment_url = urljoin(course.gradescope_url, str(link.get("href")))
            elif assignment_id:
                assignment_url = urljoin(
                    course.gradescope_url,
                    f"/courses/{course_id}/assignments/{assignment_id}",
                )
            else:
                assignment_url = course.gradescope_url
            rows.append(
                {
                    "name": name,
                    "status": status_element.get_text(" ", strip=True)
                    if status_element
                    else "",
                    "released_due": due_element.get_text(" ", strip=True)
                    if due_element
                    else "",
                    "due_at": due_element.get("datetime") if due_element else None,
                    "url": assignment_url,
                }
            )
        return rows
