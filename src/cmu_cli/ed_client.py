"""Read-only Ed protocol client; endpoint evidence and limits: docs/ed.md."""

from __future__ import annotations

import html
import json
import os
import re
from typing import Any

from .web_session import (
    MAX_DOCUMENT_BYTES,
    SessionError,
    bounded_content,
    close_response,
    configured_session,
    https_origin,
    safe_request,
)

# Only independently evidenced API origins, never arbitrary token destinations.
ED_API_BASES = frozenset({"https://us.edstem.org/api", "https://edstem.org/api"})


class EdError(RuntimeError):
    """Sanitized provider failure; partial contains only successfully read records."""

    def __init__(self, message: str, *, partial=None):
        super().__init__(message)
        self.partial = partial


class EdAuthError(EdError):
    """Authorization failure whose message is authored here, not quoted from Ed.

    Every EdAuthError is raised with one of the literals below, so the text carries
    no URL, response body or token and is safe to show. It is also the only place
    that says what to do about it.
    """

    NO_TOKEN = "Set CMU_CLI_ED_TOKEN to an Ed API token"
    DENIED = "Ed authentication or course access denied"
    REJECTED = "Ed API token rejected"


def _identifier(value: Any) -> str:
    if type(value) not in (int, str) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise EdError(
            "Ed identifiers must be positive decimal integers without leading zeros"
        )
    return str(value)


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise EdError("Invalid Ed record list")
    for row in value:
        if not isinstance(row, dict):
            raise EdError("Invalid Ed record")
        _identifier(row.get("id"))
    return value


def _result(items, *, complete, reason, pages=0, scope="course thread listings"):
    return {
        "items": items,
        "complete": complete,
        "reason": reason,
        "pages_fetched": pages,
        "fetched_count": len(items),
        "scope": scope,
        "snapshot_consistent": False,
    }


class EdClient:
    """GET-only client using an explicit CMU_CLI_ED_TOKEN environment credential.

    A supplied Session is an injection boundary for tests/explicit caller-managed
    authentication. No browser databases, .env files or keychains are inspected.
    """

    def __init__(self, session=None, *, base_url="https://us.edstem.org/api"):
        if base_url not in ED_API_BASES:
            raise EdError(
                "Unsupported Ed API base; use an evidenced exact HTTPS API base"
            )
        self.base_url = base_url
        self.origin = https_origin(base_url)
        self.session = session
        self._owns_session = session is None

    def close(self):
        if self._owns_session and self.session is not None:
            self.session.close()
            self.session = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _get(self, path, params=None):
        if not re.fullmatch(
            r"(?:user|courses/[1-9][0-9]*/threads|threads/[1-9][0-9]*)", path
        ):
            raise EdError("Only supported Ed read endpoints are allowed")
        try:
            if self.session is None:
                token = os.environ.get("CMU_CLI_ED_TOKEN")
                if not token or any(ord(c) <= 32 or ord(c) >= 127 for c in token):
                    raise EdAuthError(EdAuthError.NO_TOKEN)
                self.session = configured_session()
                self.session.headers["Authorization"] = f"Bearer {token}"
            response = safe_request(
                self.session,
                f"{self.base_url}/{path}",
                origin=self.origin,
                params=params,
            )
            if response.status_code in (401, 403):
                close_response(response)
                raise EdAuthError(EdAuthError.DENIED)
            if response.status_code != 200:
                status = response.status_code
                close_response(response)
                raise EdError(f"Ed read unavailable (HTTP {status})")
            if "json" not in response.headers.get("Content-Type", "").lower():
                close_response(response)
                raise EdError("Ed returned a non-JSON response; check authentication")
            data = json.loads(bounded_content(response, MAX_DOCUMENT_BYTES))
            if not isinstance(data, dict):
                raise EdError("Invalid Ed response object")
            if data.get("code") == "bad_token":
                raise EdAuthError(EdAuthError.REJECTED)
            if data.get("error") or data.get("code"):
                raise EdError("Ed API rejected the read request")
            return data
        except (SessionError, ValueError, UnicodeError, RecursionError):
            raise EdError(
                "Ed read failed: transport, redirect or invalid response"
            ) from None

    def courses(self) -> list[dict[str, Any]]:
        """Return course-membership wrappers, preserving role and course metadata."""
        rows = self._get("user").get("courses")
        if not isinstance(rows, list):
            raise EdError("Invalid Ed courses response")
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("course"), dict):
                raise EdError("Invalid Ed course membership")
            key = _identifier(row["course"].get("id"))
            if key in seen:
                raise EdError("Duplicate Ed course membership")
            seen.add(key)
        return rows

    def threads(self, course_id, *, page_size=100, max_pages=100):
        """Fetch until an empty page; caps/errors never masquerade as completeness.

        On failure EdError.partial carries the successful prefix. Authentication
        failures retain their EdAuthError type. Offset feeds are not snapshots.
        """
        cid = _identifier(course_id)
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise EdError("page_size must be an integer from 1 to 100")
        if type(max_pages) is not int or not 1 <= max_pages <= 1000:
            raise EdError("max_pages must be an integer from 1 to 1000")
        items, seen, offset, pages = [], set(), 0, 0
        try:
            for _ in range(max_pages):
                page = _records(
                    self._get(
                        f"courses/{cid}/threads",
                        {"limit": page_size, "offset": offset, "sort": "new"},
                    ).get("threads")
                )
                pages += 1
                if not page:
                    return _result(
                        items, complete=True, reason="empty terminal page", pages=pages
                    )
                if len(page) > page_size:
                    raise EdError("Ed returned an oversized thread page")
                keys = [_identifier(row["id"]) for row in page]
                if len(set(keys)) != len(keys) or seen.intersection(keys):
                    raise EdError("Ed pagination repeated or changed; retry")
                if any(_identifier(row.get("course_id")) != cid for row in page):
                    raise EdError("Ed thread course identity mismatch")
                seen.update(keys)
                items.extend(page)
                offset += len(page)
        except EdError as exc:
            exc.partial = _result(items, complete=False, reason=str(exc), pages=pages)
            raise
        return _result(items, complete=False, reason="page limit reached", pages=pages)

    def thread(self, thread_id, *, course_id=None) -> dict[str, Any]:
        """Return raw detail with nested answers/comments and original timestamps."""
        tid = _identifier(thread_id)
        cid = _identifier(course_id) if course_id is not None else None
        row = self._get(f"threads/{tid}").get("thread")
        if not isinstance(row, dict) or _identifier(row.get("id")) != tid:
            raise EdError("Ed thread identity mismatch")
        if cid is not None and _identifier(row.get("course_id")) != cid:
            raise EdError("Ed thread course identity mismatch")
        # Validate the tree without recursive Python calls; retain its raw shape.
        pending = _records(row.get("answers")) + _records(row.get("comments"))
        seen = set()
        while pending:
            reply = pending.pop()
            rid = _identifier(reply["id"])
            if rid in seen:
                raise EdError("Duplicate Ed reply identity")
            seen.add(rid)
            if _identifier(reply.get("thread_id")) != tid:
                raise EdError("Ed reply thread identity mismatch")
            pending.extend(_records(reply.get("comments")))
        return row

    def replies(self, thread_id, *, course_id=None):
        """Flatten returned reply tree; compare observed count to reply_count.

        Ed's visibility/count semantics may differ: mismatches are incomplete,
        not silently treated as a full reply export. Parent IDs remain intact.
        """
        row = self.thread(thread_id, course_id=course_id)
        items = []
        pending = list(reversed(row["answers"] + row["comments"]))
        while pending:
            reply = pending.pop()
            items.append(reply)
            pending.extend(reversed(reply["comments"]))
        count = row.get("reply_count")
        complete = type(count) is int and count == len(items)
        result = _result(
            items,
            complete=complete,
            reason="reply count matched"
            if complete
            else "reply count absent or mismatched",
            pages=1,
            scope="replies embedded in one thread detail response",
        )
        result["reported_reply_count"] = count
        return result

    def search(self, course_id, query: str, *, page_size=100, max_pages=100):
        """Local substring search of fetched listing title/content/document only.

        Does NOT search replies or fetch hidden detail text. No server-search
        endpoint has been verified. Completeness refers only to this scope.
        """
        if not isinstance(query, str) or not query.strip():
            raise EdError("Search query must be nonempty text")
        result = self.threads(course_id, page_size=page_size, max_pages=max_pages)
        needle = query.casefold()
        result["items"] = [
            row
            for row in result["items"]
            if needle
            in html.unescape(
                re.sub(
                    r"<[^>]*>",
                    " ",
                    " ".join(
                        row.get(key, "")
                        for key in ("title", "content", "document")
                        if isinstance(row.get(key, ""), str)
                    ),
                )
            ).casefold()
        ]
        result["matched_count"] = len(result["items"])
        result["scope"] = (
            "local listing title/content/document; excludes replies and unfetched detail"
        )
        result["search_mode"] = "local"
        return result
