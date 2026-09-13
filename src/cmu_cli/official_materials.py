"""Public course-site materials that are not exposed through Canvas Files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .web_session import (
    SessionError,
    close_response,
    configured_session,
    public_document,
    safe_request,
)

SMALL_WORDS = {"and", "as", "of", "to", "for", "in", "on", "the"}


def readable_topic(value: str) -> str:
    value = re.sub(r"\s*/\s*", " and ", value.strip())
    tokens = re.findall(r"[A-Za-z0-9]+", value)
    rendered: list[str] = []
    for index, token in enumerate(tokens):
        lower = token.lower()
        if token.isupper() or any(char.isdigit() for char in token):
            rendered.append(token)
        elif index and lower in SMALL_WORDS:
            rendered.append(lower)
        else:
            rendered.append(lower.capitalize())
    return "_".join(rendered) or "Slides"


def public_materials(
    course_code: str, base_url: str | None = None
) -> list[dict[str, Any]]:
    if not base_url:
        return []
    soup = BeautifulSoup(public_document(base_url), "html.parser")
    rows: dict[str, dict[str, Any]] = {}
    for tr in soup.select("tr"):
        cells = tr.select("th,td")
        if len(cells) < 3:
            continue
        topic = cells[1].get_text(" ", strip=True)
        for anchor in tr.select("td a[href]"):
            url = urljoin(base_url, anchor["href"])
            path = urlparse(url).path
            if (
                urlparse(url).netloc != urlparse(base_url).netloc
                or Path(path).suffix.lower() != ".pdf"
            ):
                continue
            if "/recs/" in path:
                match = re.search(
                    r"/rec(?:itation)?[_-]?(\d+)[^/]*\.pdf$", path, re.IGNORECASE
                )
                if not match:
                    continue
                stem = Path(path).stem.lower()
                kind = (
                    "Solutions"
                    if "sol" in stem
                    else "Slides"
                    if "slide" in stem
                    else "Handout"
                )
                display_name = f"Recitation_{int(match.group(1)):02d}_{kind}.pdf"
            elif "/slides/" in path:
                match = re.search(r"/(\d+)[^/]*\.pdf$", path, re.IGNORECASE)
                if not match:
                    continue
                lecture = int(match.group(1))
                inked = "ink" in Path(path).stem.lower()
                suffix = "_inked" if inked else ""
                display_name = (
                    f"Lecture_{lecture:02d}_{readable_topic(topic)}{suffix}.pdf"
                )
            else:
                continue
            with configured_session() as session:
                probe = safe_request(session, url, method="HEAD", anonymous=True)
                close_response(probe)
            if probe.status_code != 200:
                raise SessionError(
                    f"Public material unavailable (HTTP {probe.status_code})"
                )
            if "application/pdf" not in probe.headers.get("Content-Type", "").lower():
                raise SessionError("Public material did not advertise PDF content")
            size_header = probe.headers.get("Content-Length")
            updated = probe.headers.get("ETag") or probe.headers.get("Last-Modified")
            rows[url] = {
                "id": f"course-site:{url}",
                "source": "course_site",
                "course": course_code,
                "display_name": display_name,
                "filename": Path(path).name,
                "content-type": probe.headers.get("Content-Type"),
                "url": url,
                "size": int(size_header)
                if size_header and size_header.isdigit()
                else None,
                "updated_at": updated,
                "modified_at": probe.headers.get("Last-Modified"),
            }
    return sorted(rows.values(), key=lambda item: (item["display_name"], item["url"]))
