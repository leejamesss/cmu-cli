"""Canvas API client for explicitly supplied, legitimately obtained tokens."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from .web_session import (
    MAX_DOCUMENT_BYTES,
    BrowserDependencyMissing,
    CrossOriginRedirect,
    SessionError,
    bounded_content,
    canvas_api_session,
    close_response,
    configured_session,
    https_origin,
    safe_request,
)


class CanvasError(RuntimeError):
    pass


class CanvasEndpointUnavailable(CanvasError):
    """A denied/missing endpoint is not an empty collection."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Canvas endpoint unavailable (HTTP {status_code})")


class CanvasClient:
    def __init__(
        self, base_url: str, session: requests.Session | None = None, browser_auth=None
    ):
        self.browser_auth = browser_auth
        self.base_url = base_url.rstrip("/")
        try:
            self.origin = https_origin(self.base_url)
            parsed = urlsplit(self.base_url)
            if parsed.path or parsed.query or parsed.fragment:
                raise SessionError("Canvas base URL must be a plain HTTPS origin")
            if session is not None:
                self.session = session
                self.session.trust_env = False
                self.auth_method = "provided_session"
            else:
                self.session, self.auth_method = canvas_api_session(
                    self.base_url, browser_auth
                )
        except BrowserDependencyMissing:
            raise
        except SessionError as exc:
            raise CanvasError(str(exc)) from None

    def _page(self, url: str, params=None):
        try:
            response = safe_request(
                self.session, url, origin=self.origin, params=params
            )
            if response.status_code in (401, 403, 404):
                close_response(response)
                raise CanvasEndpointUnavailable(response.status_code)
            if not 200 <= response.status_code < 300:
                close_response(response)
                raise CanvasError(f"Canvas API returned HTTP {response.status_code}")
            if "json" not in response.headers.get("Content-Type", "").lower():
                close_response(response)
                raise CanvasError("Canvas API did not return JSON")
            # Requests parses quoted rel values and all links, not only the first.
            next_url = response.links.get("next", {}).get("url")
            next_url = urljoin(response.url or url, next_url) if next_url else None
            payload = bounded_content(response, MAX_DOCUMENT_BYTES)
            try:
                data = json.loads(payload)
            except (ValueError, UnicodeError):
                raise CanvasError("Canvas API returned invalid JSON") from None
            return data, next_url
        except SessionError as exc:
            raise CanvasError(str(exc)) from None

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Read JSON, completing list pagination or fail without partial success."""
        url = urljoin(self.base_url + "/", path)
        rows = []
        seen = set()
        for page_index in range(1000):
            if url in seen:
                raise CanvasError("Canvas pagination loop detected")
            seen.add(url)
            data, next_url = self._page(url, params)
            if not isinstance(data, list):
                if page_index or next_url:
                    raise CanvasError("Canvas pagination returned a non-list payload")
                return data
            rows.extend(data)
            if len(rows) > 100000:
                raise CanvasError("Canvas collection exceeds safety limit")
            if not next_url:
                return rows
            url, params = next_url, None
        raise CanvasError("Canvas pagination exceeds safety limit")

    def download_bytes(self, url: str) -> bytes:
        anonymous_session = None
        try:
            origin = https_origin(url)
            if origin == self.origin:
                try:
                    response = safe_request(self.session, url, origin=self.origin)
                except CrossOriginRedirect as exc:
                    # Canvas answers a file URL with a 302 to pre-signed storage on
                    # another host. That URL carries its own signature, so it must be
                    # fetched *without* our credentials -- which is also the only way
                    # to fetch it at all. Handing off here keeps the rule the origin
                    # pin exists to enforce: nothing authenticated leaves the origin.
                    anonymous_session = configured_session()
                    response = safe_request(
                        anonymous_session, exc.location, anonymous=True
                    )
            else:
                anonymous_session = configured_session()
                response = safe_request(anonymous_session, url, anonymous=True)
            if response.status_code != 200:
                close_response(response)
                raise CanvasError(
                    f"Canvas download returned HTTP {response.status_code}"
                )
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" in content_type or "application/xhtml+xml" in content_type:
                close_response(response)
                raise CanvasError("Refusing HTML download; it may be a login page")
            data = bounded_content(response)
            prefix = data[:1024].lstrip().lower()
            if prefix.startswith((b"<!doctype html", b"<html", b"<form")):
                raise CanvasError("Refusing an HTML/login response")
            if (
                urlsplit(url).path.lower().endswith(".pdf")
                or "application/pdf" in content_type
            ) and not data.startswith(b"%PDF-"):
                raise CanvasError("Download did not contain a PDF signature")
            return data
        except SessionError as exc:
            raise CanvasError(str(exc)) from None
        finally:
            if anonymous_session is not None:
                anonymous_session.close()

    def list_courses(self, *, include_term: bool = False) -> list[dict[str, Any]]:
        params = {"enrollment_state": "active", "per_page": 100}
        if include_term:
            params["include[]"] = ["term"]
        return self.get("/api/v1/courses", params)

    def assignments(self, course_id: int) -> list[dict[str, Any]]:
        return self.get(
            f"/api/v1/courses/{course_id}/assignments",
            {"per_page": 100, "include[]": ["submission"]},
        )

    def quizzes(self, course_id: int) -> list[dict[str, Any]]:
        return self.get(f"/api/v1/courses/{course_id}/quizzes", {"per_page": 100})

    def files(self, course_id: int) -> list[dict[str, Any]]:
        return self.get(
            f"/api/v1/courses/{course_id}/files",
            {"per_page": 100, "sort": "updated_at", "order": "desc"},
        )

    def announcements(self, course_id: int) -> list[dict[str, Any]]:
        return self.get(
            "/api/v1/announcements",
            {"context_codes[]": [f"course_{course_id}"], "per_page": 100},
        )

    def modules(self, course_id: int) -> list[dict[str, Any]]:
        from .canvas_grades import identifier, unique

        identifier(course_id)
        modules = unique(
            self.get(
                f"/api/v1/courses/{course_id}/modules",
                {"per_page": 100, "include[]": ["items", "content_details"]},
            )
        )
        for module in modules:
            items = module.get("items")
            count = module.get("items_count")
            if not isinstance(items, list) or count is None or len(items) != count:
                items = self.get(
                    f"/api/v1/courses/{course_id}/modules/{module['id']}/items",
                    {"per_page": 100, "include[]": ["content_details"]},
                )
            items = unique(items)
            if count is not None and (type(count) is not int or count != len(items)):
                raise CanvasError("Canvas module item count mismatch")
            module["items"] = items
        return modules

    def module_files(self, course_id: int) -> list[dict[str, Any]]:
        from .canvas_grades import identifier

        rows = {}
        for module in self.modules(course_id):
            for item in module["items"]:
                if item.get("type") != "File":
                    continue
                file_id = identifier(item.get("content_id"))
                if file_id not in rows:
                    row = self.get(f"/api/v1/courses/{course_id}/files/{file_id}")
                    if not isinstance(row, dict) or row.get("id") != file_id:
                        raise CanvasError("Canvas file identity mismatch")
                    rows[file_id] = {**row, "module_ids": []}
                if module["id"] not in rows[file_id]["module_ids"]:
                    rows[file_id]["module_ids"].append(module["id"])
        return list(rows.values())

    def tabs(self, course_id: int) -> list[dict[str, Any]]:
        return self.get(f"/api/v1/courses/{course_id}/tabs", {"per_page": 100})
