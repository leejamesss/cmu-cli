"""Bounded discussion attachment discovery; no Resources/Lessons endpoint guesses.

Only already-authorized bodies are parsed. Downloads are anonymous and pinned to
an evidenced exact asset origin; off-origin redirects fail closed. See docs.
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from .ed_client import EdAuthError, EdError, _identifier
from .storage import checked_destination, download_file, safe_filename
from .web_session import (
    MAX_DOWNLOAD_BYTES,
    SessionError,
    bounded_content,
    close_response,
    configured_session,
    https_origin,
    safe_request,
)

ORIGINS = {
    "ed": frozenset(
        {
            "https://us.edstem.org",
            "https://edstem.org",
            "https://static.us.edusercontent.com",
        }
    ),
    "piazza": frozenset({"https://piazza.com"}),
}
FILE_SUFFIXES = frozenset(
    {
        ".pdf",
        ".ppt",
        ".pptx",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".zip",
        ".ipynb",
        ".py",
        ".r",
        ".txt",
        ".csv",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".mp4",
        ".mp3",
    }
)


def _id(value):
    if type(value) not in (int, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", str(value)):
        raise ValueError("Invalid discussion identity")
    return str(value)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"a", "link", "file", "img", "image"}:
            url = attrs.get("href") or attrs.get("src") or attrs.get("url")
            if url:
                self.links.append(
                    (
                        url,
                        attrs.get("filename")
                        or attrs.get("name")
                        or attrs.get("download"),
                        tag in {"file", "img", "image"} or "download" in attrs,
                    )
                )


def extract_attachments(
    platform, course_id, post_id, body, *, source_url, parent_id=None
):
    """Parse one HTML/XML string; return file candidates AND non-downloadable links.

    Unrecognized JSON node shapes are not silently interpreted as attachments.
    Candidate does not mean verified bytes. IDs include full URL query (signed URL
    rotation therefore yields a new identity, rather than unsafe query stripping).
    """
    if platform not in ORIGINS:
        raise ValueError("Unsupported discussion platform")
    course_id, post_id = _id(course_id), _id(post_id)
    parent_id = _id(parent_id) if parent_id is not None else post_id
    base_origin = https_origin(source_url)
    if base_origin not in {https_origin(u) for u in ORIGINS[platform]}:
        raise ValueError("Untrusted discussion source origin")
    if not isinstance(body, str):
        raise TypeError("Discussion body must be HTML/XML text")
    if len(body.encode("utf-8")) > 8 * 1024 * 1024:
        raise ValueError("Discussion body exceeds size limit")
    parser = _Links()
    parser.feed(body)
    rows = {}
    for reference, name, explicit in parser.links:
        try:
            target = urljoin(source_url, reference)
            origin = https_origin(target)
        except (SessionError, ValueError):
            continue
        parts = urlsplit(target)
        target = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        trusted = origin in {https_origin(u) for u in ORIGINS[platform]}
        filename = safe_filename(
            name or unquote(parts.path.rsplit("/", 1)[-1]) or "attachment"
        )
        candidate = explicit or (
            trusted and Path(unquote(parts.path)).suffix.lower() in FILE_SUFFIXES
        )
        # Ed's publicly evidenced file storage uses opaque IDs, not extensions.
        candidate |= (
            platform == "ed"
            and origin == https_origin("https://static.us.edusercontent.com")
            and parts.path.startswith("/files/")
            and len(parts.path) > len("/files/")
        )
        identity = hashlib.sha256(
            f"{platform}\0{course_id}\0{post_id}\0{target}".encode()
        ).hexdigest()
        key = f"{platform}:{course_id}:{post_id}:{identity}"
        row = rows.setdefault(
            key,
            {
                "id": key,
                "platform": platform,
                "course_id": course_id,
                "post_id": post_id,
                "filename": filename,
                "kind": "attachment" if candidate else "link",
                "url": target,
                "download_url": target if candidate and trusted else None,
                "availability": "unverified"
                if candidate and trusted
                else "not_downloadable",
                "provenance": [],
            },
        )
        provenance = {"source_url": source_url, "parent_id": parent_id}
        if provenance not in row["provenance"]:
            row["provenance"].append(provenance)
    return list(rows.values())


def _combine(target, rows):
    for row in rows:
        if row["id"] not in target:
            target[row["id"]] = row
        else:
            for source in row["provenance"]:
                if source not in target[row["id"]]["provenance"]:
                    target[row["id"]]["provenance"].append(source)


def ed_materials(client, course_id, *, page_size=100, max_pages=100):
    """Read existing Ed listing/detail APIs, retaining successful partial results."""
    cid = _identifier(course_id)
    issues, rows = [], {}
    try:
        listing = client.threads(cid, page_size=page_size, max_pages=max_pages)
    except EdError as exc:
        listing = exc.partial or {"items": [], "complete": False}
        if isinstance(exc, EdAuthError) and not listing["items"]:
            exc.partial = None
            raise
        issues.append(
            {
                "reason": "authentication_required"
                if isinstance(exc, EdAuthError)
                else "thread_listing_failed"
            }
        )
    if not listing["complete"]:
        issues.append({"reason": "thread_listing_incomplete"})
    seen = set()
    for summary in listing["items"]:
        tid = _identifier(summary.get("id"))
        if tid in seen:
            issues.append({"post_id": tid, "reason": "duplicate_thread"})
            continue
        seen.add(tid)
        try:
            thread = client.thread(tid, course_id=cid)
            pending, count = [thread], 0
            visited = set()
            while pending:
                node = pending.pop()
                marker = (
                    "thread" if node is thread else "reply",
                    _identifier(node.get("id")),
                )
                if marker in visited or len(visited) >= 100000:
                    raise ValueError("Duplicate or oversized reply tree")
                visited.add(marker)
                if node is not thread:
                    count += 1
                if not any(
                    isinstance(node.get(field), str)
                    for field in ("content", "document")
                ):
                    issues.append({"post_id": tid, "reason": "body_not_returned"})
                for field in ("content", "document"):
                    if node.get(field) is not None:
                        _combine(
                            rows,
                            extract_attachments(
                                "ed",
                                cid,
                                tid,
                                node[field],
                                source_url=f"{client.base_url}/threads/{tid}",
                                parent_id=node["id"],
                            ),
                        )
                for field in ("answers", "comments"):
                    children = node.get(field, [])
                    if not isinstance(children, list):
                        raise TypeError("Malformed replies")
                    pending.extend(children)
            if (
                type(thread.get("reply_count")) is not int
                or thread["reply_count"] != count
            ):
                issues.append(
                    {"post_id": tid, "reason": "reply_count_absent_or_mismatched"}
                )
        except (EdError, ValueError, TypeError, AttributeError):
            issues.append({"post_id": tid, "reason": "detail_unavailable_or_malformed"})
    return {
        "items": list(rows.values()),
        "complete": not issues,
        "issues": issues,
        "scope": "returned Ed thread bodies and embedded replies; excludes Resources/Lessons",
        "fetched_count": len(rows),
        "snapshot_consistent": False,
    }


def piazza_post_materials(post, *, network_id, post_id):
    """Parse caller-supplied authorized full content.get export (not feed snippets).

    Does not call content.get: read-state side effects are not established by the
    public unofficial protocol source. Full network coverage is never claimed.
    """
    nid, pid = _id(network_id), _id(post_id)
    if not isinstance(post, dict) or str(post.get("id")) != pid:
        raise ValueError("Piazza post identity mismatch")
    if post.get("nid") is not None and post["nid"] != nid:
        raise ValueError("Piazza network identity mismatch")
    rows, issues, pending, seen = {}, [], [post], set()
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            issues.append({"reason": "malformed_post_node"})
            continue
        marker = id(node)
        if marker in seen or len(seen) >= 100000:
            issues.append({"reason": "duplicate_or_oversized_post_tree"})
            continue
        seen.add(marker)
        history = node.get("history", [])
        # Parse all supplied revisions with provenance; not an inferred latest version.
        bodies = [node] + (history if isinstance(history, list) else [])
        if not isinstance(history, list):
            issues.append({"reason": "malformed_history"})
        for version in bodies:
            if not isinstance(version, dict):
                issues.append({"reason": "malformed_history"})
                continue
            for field in ("content", "subject"):
                if version.get(field) is not None:
                    try:
                        _combine(
                            rows,
                            extract_attachments(
                                "piazza",
                                nid,
                                pid,
                                version[field],
                                source_url=f"https://piazza.com/class/{nid}?cid={pid}",
                                parent_id=node.get("id", pid),
                            ),
                        )
                    except (ValueError, TypeError):
                        issues.append({"reason": "malformed_body"})
        children = node.get("children", [])
        if isinstance(children, list):
            pending.extend(children)
        else:
            issues.append({"reason": "malformed_children"})
    return {
        "items": list(rows.values()),
        "complete": False,
        "issues": issues,
        "fetched_count": len(rows),
        "parsed_supplied_bodies": not issues,
        "scope": "supplied Piazza post/revision/child bodies only; network and Resources unverified",
    }


def download_attachment(item, root, *, max_bytes=MAX_DOWNLOAD_BYTES):
    """Anonymous, exact-origin, bounded download into non-overwriting shared storage.

    Deliberately no credential/session parameter. Unsupported CDN redirects and
    auth-required files fail rather than copying a provider token to an asset.
    """
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_DOWNLOAD_BYTES:
        raise ValueError("Invalid download byte limit")
    platform = item.get("platform")
    url = item.get("download_url")
    if item.get("kind") != "attachment" or not url or platform not in ORIGINS:
        raise ValueError("Not a downloadable attachment")
    origin = https_origin(url)
    if origin not in {https_origin(u) for u in ORIGINS[platform]}:
        raise ValueError("Untrusted attachment origin")
    # Never use provider data as a directory component or overwrite a local edit.
    identity = hashlib.sha256(item["id"].encode()).hexdigest()
    name = safe_filename(item["filename"])
    destination = Path(root) / platform / identity / name
    checked_destination(Path(root), destination)

    def fetch(target):
        with configured_session() as session:
            response = safe_request(session, target, origin=origin, anonymous=True)
            if response.status_code != 200:
                close_response(response)
                raise SessionError("Discussion attachment unavailable or unauthorized")
            media = response.headers.get("Content-Type", "").lower()
            if "html" in media:
                close_response(response)
                raise SessionError("Refusing HTML attachment response")
            data = bounded_content(response, max_bytes)
            if (
                data[:1024]
                .lstrip()
                .lower()
                .startswith((b"<!doctype html", b"<html", b"<form"))
            ):
                raise SessionError("Refusing login response")
            if (
                name.lower().endswith(".pdf") or "application/pdf" in media
            ) and not data.startswith(b"%PDF-"):
                raise SessionError("Invalid PDF attachment")
            return data

    result = download_file({"url": url, "size": item.get("size")}, destination, fetch)
    return {
        **result,
        "id": item["id"],
        "relative_path": str(destination.relative_to(root)),
        "provenance": item.get("provenance", []),
    }
