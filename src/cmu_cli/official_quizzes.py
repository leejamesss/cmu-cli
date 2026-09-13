"""Official public/local course assessment schedules."""

from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from .web_session import public_document

ET = ZoneInfo("America/New_York")


def public_quizzes(
    course_code: str, url: str | None = None, year: int | None = None, hour: int = 0
) -> list[dict[str, Any]]:
    if not url or year is None:
        return []
    soup = BeautifulSoup(public_document(url), "html.parser")
    rows: list[dict[str, Any]] = []
    for tr in soup.select("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.select("th,td")]
        if len(cells) < 2:
            continue
        label = cells[1]
        is_quiz = bool(re.fullmatch(r"Quiz\s+\d+", label, re.IGNORECASE))
        is_exam = bool(
            re.match(
                r"^(?:Midterm(?:\s+Exam)?|Final(?:\s+Exam)?|Exam\s+\d+)\b",
                label,
                re.IGNORECASE,
            )
        )
        if not is_quiz and not is_exam:
            continue
        if cells[0].strip().upper() == "TBD":
            continue
        date = date_parser.parse(f"{cells[0]}, {year}", fuzzy=True).replace(
            hour=hour, minute=0, second=0, microsecond=0, tzinfo=ET
        )
        rows.append(
            {
                "source": "course_site",
                "course": course_code,
                "name": label,
                "kind": "exam" if is_exam else "quiz",
                "starts_at": date.isoformat(),
                "starts_local": date.strftime("%Y-%m-%d %H:%M %Z"),
                "url": url,
                "published": True,
            }
        )
    return rows
