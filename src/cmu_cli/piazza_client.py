"""Read-only Piazza feeds selected by exact configured network identity."""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlsplit

from .web_session import (
    MAX_DOCUMENT_BYTES,
    BrowserDependencyMissing,
    SessionError,
    bounded_content,
    browser_cookie_session,
    close_response,
    safe_request,
)


class PiazzaError(RuntimeError):
    pass


class PiazzaClient:
    def __init__(self, session=None, courses=(), term="current", browser_auth=None):
        self.courses = tuple(courses)
        self.term = term
        self.session = session
        self.browser_auth = browser_auth

    def call(self, method: str, params: dict[str, Any]) -> Any:
        if method != "network.get_my_feed":
            raise PiazzaError("Only read-only class feed requests are supported")
        if params.get("nid") not in {
            self.network_id(c) for c in self.courses if c.piazza_url
        }:
            raise PiazzaError("Piazza request requires an exact configured network")
        try:
            if self.session is None:
                self.session = browser_cookie_session("piazza.com", self.browser_auth)
            # Piazza web requests use session_id as CSRF-Token, never cross-origin.
            tokens = {
                c.value
                for c in self.session.cookies
                if c.name == "session_id" and c.domain.lstrip(".") == "piazza.com"
            }
            if len(tokens) != 1:
                raise PiazzaError("Piazza browser session is missing or ambiguous")
            self.session.headers["CSRF-Token"] = tokens.pop()
            response = safe_request(
                self.session,
                "https://piazza.com/logic/api",
                origin=("https", "piazza.com", 443),
                method="POST",
                params={"method": method},
                json={"method": method, "params": params},
            )
            if response.status_code != 200:
                close_response(response)
                raise PiazzaError(
                    "Piazza feed unavailable; check browser login and enrollment"
                )
            if "json" not in response.headers.get("Content-Type", "").lower():
                close_response(response)
                raise PiazzaError("Piazza did not return JSON")
            data = json.loads(bounded_content(response, MAX_DOCUMENT_BYTES))
            if not isinstance(data, dict) or data.get("error") or "result" not in data:
                raise PiazzaError("Piazza API rejected the read request")
            return data["result"]
        except BrowserDependencyMissing:
            raise
        except (SessionError, ValueError, UnicodeError):
            raise PiazzaError(
                "Piazza read failed; check browser authentication and service availability"
            ) from None

    @staticmethod
    def network_id(course) -> str | None:
        value = course.piazza_url
        if not value:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "piazza.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise PiazzaError("Piazza course URL must use its exact HTTPS origin")
        match = re.fullmatch(r"/class/([A-Za-z0-9_-]+)/?", parsed.path)
        if not match:
            raise PiazzaError(
                "Piazza course URL must identify an explicit class network"
            )
        return match.group(1)

    def normalized_course_code(self, network: dict[str, Any]) -> str | None:
        # Explicit provider identity only; CS-101 and MATH-101 are not aliases.
        matches = [
            c.code
            for c in self.courses
            if self.network_id(c) is not None
            and self.network_id(c) == str(network.get("id", ""))
        ]
        if len(matches) > 1:
            raise PiazzaError("Piazza network is configured for multiple courses")
        return matches[0] if matches else None

    def networks(self, course: str | None = None) -> list[dict[str, Any]]:
        if course is not None and course not in {c.code for c in self.courses}:
            raise PiazzaError("Unknown course filter")
        selected = [c for c in self.courses if course is None or c.code == course]
        networks = [
            {"id": self.network_id(c), "term": self.term}
            for c in selected
            if c.piazza_url
        ]
        if not networks:
            raise PiazzaError("Configure an explicit Piazza class URL")
        return networks

    def open_class_posts(self, course: str | None = None) -> list[dict[str, Any]]:
        networks = self.networks(course)
        feeds = {}
        for network in networks:
            nid = network["id"]
            posts = []
            seen = set()
            for offset in range(0, 10000, 100):
                result = self.call(
                    "network.get_my_feed",
                    {"nid": nid, "limit": 100, "offset": offset, "sort": "updated"},
                )
                if not isinstance(result, dict) or not isinstance(
                    result.get("feed"), list
                ):
                    raise PiazzaError("Invalid Piazza feed payload")
                page = result["feed"]
                for post in page:
                    if not isinstance(post, dict) or not post.get("id"):
                        raise PiazzaError("Invalid Piazza post")
                    pid = str(post["id"])
                    if pid in seen:
                        raise PiazzaError(
                            "Piazza feed changed or pagination repeated; retry"
                        )
                    seen.add(pid)
                    posts.append(post)
                more = result.get("more")
                if more is False or (more is None and len(page) < 100):
                    break
                if not page:
                    raise PiazzaError("Piazza pagination did not advance")
            else:
                raise PiazzaError(
                    "Piazza pagination exceeds safety limit; incomplete feed"
                )
            feeds[nid] = posts
        return self.parse_posts(networks, feeds, course)

    def parse_posts(
        self,
        networks: list[dict[str, Any]],
        feeds: dict[str, list[dict[str, Any]]],
        course: str | None = None,
    ) -> list[dict[str, Any]]:
        """Normalize supplied data, selecting the explicit course before feed access.

        Completeness is that of the supplied export; no server completeness claim.
        The course filter is an exact configured course code, not a fuzzy match.
        """
        if course is not None and course not in {c.code for c in self.courses}:
            raise PiazzaError("Unknown course filter")
        rows = []
        for network in networks:
            code = self.normalized_course_code(network)
            if code is None or (course is not None and code != course):
                continue
            if self.term != "current" and network.get("term") != self.term:
                continue
            nid = str(network["id"])
            for post in feeds.get(nid, []):
                snippet = html.unescape(str(post.get("content_snipet") or ""))
                snippet = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", snippet)).strip()
                pid = str(post.get("id") or "")
                if not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
                    raise PiazzaError("Invalid offline post identifier")
                rows.append(
                    {
                        "course": code,
                        "title": html.unescape(str(post.get("subject") or "")).strip(),
                        "snippet": snippet,
                        "id": post.get("id"),
                        "modified": post.get("modified") or post.get("updated"),
                        "url": f"https://piazza.com/class/{nid}?cid={pid}",
                    }
                )
        return rows
