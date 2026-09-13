"""Explicit authentication and bounded, origin-bound HTTPS transport."""

from __future__ import annotations

import os
import re
from copy import copy
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 5


class SessionError(RuntimeError):
    pass


BROWSER_EXTRA_HINT = (
    "Install browser support: python -m pip install "
    '"cmu-cli[browser] @ https://github.com/leejamesss/cmu-cli/archive/refs/heads/main.zip"'
)


class BrowserDependencyMissing(SessionError):
    """Trusted optional-dependency failure; never contains loader diagnostics."""


class CrossOriginRedirect(SessionError):
    """An origin-pinned request was redirected off its origin.

    A SessionError like before -- existing callers and tests that catch SessionError
    are unaffected -- but it carries the destination, so a caller that knows the
    target is self-authenticating (a pre-signed download URL) can fetch it without
    credentials instead of giving up. The credential boundary is unchanged: nothing
    authenticated ever leaves the pinned origin.
    """

    def __init__(self, location: str):
        super().__init__("Authenticated request must remain on its exact HTTPS origin")
        self.location = location


def https_origin(url: str) -> tuple[str, str, int]:
    """Validate before transmission, including userinfo and ambiguous URL syntax."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "\\" in url
            or any(ord(c) <= 32 or ord(c) == 127 for c in url)
        ):
            raise ValueError
        return ("https", parsed.hostname.lower(), parsed.port or 443)
    except (ValueError, TypeError):
        raise SessionError(
            "URL must use HTTPS with a valid host and no embedded credentials"
        ) from None


def configured_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": "cmu_cli/0.1"})
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def canvas_api_session(
    base_url: str, browser_auth=None
) -> tuple[requests.Session, str]:
    https_origin(base_url)
    token = os.environ.get("CMU_CLI_CANVAS_TOKEN")
    if not token:
        return browser_cookie_session(
            https_origin(base_url)[1], browser_auth
        ), "browser_cookie"
    session = configured_session()
    session.headers["Authorization"] = f"Bearer {token}"
    return session, "environment_token"


def browser_cookie_session(host: str, options=None) -> requests.Session:
    """Read one explicitly selected database, retaining only host-applicable cookies."""
    if not isinstance(options, dict) or options.get("enabled") is not True:
        raise SessionError(
            "Browser authentication requires explicit opt-in in browser_auth"
        )
    hosts = options.get("hosts")
    if (
        not isinstance(hosts, list)
        or not re.fullmatch(r"[A-Za-z0-9.-]+", host)
        or host not in hosts
    ):
        raise SessionError("Cookie host is not explicitly allowed")
    browser = options.get("browser", "edge")
    filename = options.get("cookie_file")
    if (
        browser not in {"edge", "chrome"}
        or not isinstance(filename, str)
        or not filename
    ):
        raise SessionError("Select edge or chrome and an explicit profile cookie_file")
    if not Path(filename).expanduser().is_absolute():
        raise SessionError("cookie_file must be an absolute local path")
    try:
        import browser_cookie3
    except ImportError:
        raise BrowserDependencyMissing(BROWSER_EXTRA_HINT) from None
    try:
        loader = getattr(browser_cookie3, browser)
        cookies = loader(cookie_file=str(Path(filename).expanduser()), domain_name=host)
        session = configured_session()
        for cookie in cookies:
            domain = cookie.domain.lstrip(".").lower()
            if domain and (host == domain or host.endswith("." + domain)):
                scoped = copy(cookie)
                scoped.domain = host
                scoped.domain_initial_dot = False
                scoped.domain_specified = False
                scoped.secure = True
                session.cookies.set_cookie(scoped)
        if not session.cookies:
            raise SessionError("No applicable browser session cookies found")
        return session
    except Exception:  # noqa: BLE001 - never expose loader/keychain diagnostics
        raise SessionError(
            "Browser authentication unavailable; check optional dependency, selected file and local browser login"
        ) from None


def edge_cookie_session(host: str, options=None) -> requests.Session:
    return browser_cookie_session(host, options)


def close_response(response) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()


def safe_request(
    session: requests.Session,
    url: str,
    *,
    origin=None,
    method: str = "GET",
    params=None,
    anonymous: bool = False,
    allowed_urls=None,
    json=None,
):
    """Follow only validated redirects; authenticated traffic never changes origin.

    allowed_urls optionally pins every hop to an observed URL set (not just origin).
    Anonymous callers must supply a fresh configured_session. Clear any cookies
    set by earlier responses so redirects cannot turn public downloads into auth.
    """
    if anonymous:
        from .public_transport import PublicHTTPSAdapter

        if not isinstance(session.get_adapter("https://"), PublicHTTPSAdapter):
            session.mount("https://", PublicHTTPSAdapter(max_retries=0))
    seen = set()
    session.trust_env = False
    # Requests normally consumes redirect bodies while constructing Response.next,
    # even with allow_redirects=False. Disable that implicit redirect machinery;
    # this function alone validates and follows each hop without reading its body.
    session.resolve_redirects = lambda *args, **kwargs: iter(())
    for hop in range(MAX_REDIRECTS + 1):
        target_origin = https_origin(url)
        if allowed_urls is not None and url not in allowed_urls:
            raise SessionError("Request left its authorized observed URLs")
        if origin is not None and target_origin != origin:
            raise CrossOriginRedirect(url)
        if url in seen:
            raise SessionError("HTTP redirect loop detected")
        seen.add(url)
        if anonymous:
            session.cookies.clear()
            session.auth = None
            session.headers.clear()
            session.headers["User-Agent"] = "cmu_cli/0.1"
            session.params = {}
            session.hooks = {"response": []}
            session.proxies.clear()
            session.cert = None
            session.verify = True
        try:
            kwargs = {
                "params": params,
                "timeout": (15, 90),
                "allow_redirects": False,
                "stream": True,
            }
            if json is not None:
                kwargs["json"] = json
            response = getattr(session, method.lower())(url, **kwargs)
        except requests.RequestException:
            raise SessionError("HTTP transport failed") from None
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get("Location")
        close_response(response)
        if not location:
            raise SessionError("HTTP redirect omitted its destination")
        if hop == MAX_REDIRECTS:
            raise SessionError("HTTP redirect limit exceeded")
        if method != "GET" and response.status_code not in (307, 308):
            raise SessionError("Refusing ambiguous authenticated method redirect")
        url = urljoin(url, location)
        params = None
    raise SessionError("HTTP redirect limit exceeded")


def bounded_content(response, limit: int = MAX_DOWNLOAD_BYTES) -> bytes:
    """Bound decompressed bytes as well as advertised length; always close body."""
    try:
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                size = int(length)
            except (ValueError, TypeError):
                raise SessionError("Invalid HTTP content length") from None
            if size < 0 or size > limit:
                raise SessionError("HTTP response exceeds size limit")
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > limit:
                raise SessionError("HTTP response exceeds size limit")
            chunks.append(chunk)
        return b"".join(chunks)
    except requests.RequestException:
        raise SessionError("HTTP response read failed") from None
    finally:
        close_response(response)


def public_document(url: str) -> bytes:
    with configured_session() as session:
        response = safe_request(session, url, anonymous=True)
        if response.status_code != 200:
            close_response(response)
            raise SessionError(
                f"Public document unavailable (HTTP {response.status_code})"
            )
        content_type = response.headers.get("Content-Type", "").lower()
        if content_type and not any(
            t in content_type
            for t in ("text/html", "application/xhtml+xml", "text/plain")
        ):
            close_response(response)
            raise SessionError("Public schedule did not return a text document")
        return bounded_content(response, MAX_DOCUMENT_BYTES)
