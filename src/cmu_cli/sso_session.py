"""Explicit SIO browser transport with a deliberately closed protocol policy.

The default transport is unchanged. This draft policy covers only anonymously
observed CMU IdP navigation and published SIO SAML2 POST metadata; it does NOT
claim a complete Duo/MFA flow. Unknown destinations fail closed. See
``docs/sso-policy-review.md`` before considering this implementation mergeable.

Adapted from the opt-in implementation by @HorizonWind2004.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .sso_state import read_state, write_state

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext

SIO_HOST = "s3.andrew.cmu.edu"
IDP_HOST = "login.cmu.edu"
SIO_PATHS = {"/sio/", "/sio/mpa/semesterschedule", "/sio/mpa/schedule/waitlisthistory"}
IDP_SSO = "/idp/profile/SAML2/Redirect/SSO"
IDP_STYLES = {
    "/css/cmu-general-purpose.css",
    "/css/login.css",
    "/css/ie.css",
    "/css/iphone.css",
}
SAML_POST = "/Shibboleth.sso/SAML2/POST"


class SsoError(RuntimeError):
    """Sanitized SSO failure; no provider exception detail in its message."""


class NeedsLogin(SsoError):
    def __init__(
        self, message: str = "No usable SSO session; run: cmu-cli auth sso-login"
    ):
        super().__init__(message)


class PlaywrightMissing(SsoError):
    def __init__(self) -> None:
        super().__init__(
            "The sso extra is not installed. Run: "
            "python -m pip install 'cmu-cli[sso]' && python -m playwright install chromium"
        )


def _url(url):
    try:
        parts = urlsplit(url)
        if (
            not isinstance(url, str)
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            or "\\" in url
            or parts.scheme != "https"
            or parts.username is not None
            or parts.password is not None
            or parts.netloc not in {SIO_HOST, IDP_HOST}
            or parts.fragment
            or "%" in parts.path
            or ".." in parts.path.split("/")
        ):
            raise ValueError
        return parts
    except (ValueError, TypeError, AttributeError):
        raise SsoError("URL is outside the approved SSO boundary") from None


def at_login_wall(url: str) -> bool:
    try:
        return _url(url).hostname == IDP_HOST
    except SsoError:
        return False


def landed(url: str, want_host: str) -> bool:
    try:
        parts = _url(url)
        return (
            parts.hostname == want_host == SIO_HOST
            and parts.path in SIO_PATHS
            and not parts.query
        )
    except SsoError:
        return False


def allowed_request(url: str, method: str, resource_type: str, *, login: bool) -> bool:
    """Exact observed protocol endpoints, never application writes or host wildcards.

    This is not a complete institutional MFA policy. Adding endpoints requires
    protocol evidence, not a wildcard or a user-supplied arbitrary host list.
    """
    try:
        parts = _url(url)
    except SsoError:
        return False
    if parts.hostname == SIO_HOST:
        if method == "GET" and resource_type == "document":
            return parts.path in SIO_PATHS | {"/sio/s3Login"} and not parts.query
        return (
            login
            and method == "POST"
            and resource_type == "document"
            and parts.path == SAML_POST
            and not parts.query
        )
    if login and parts.hostname == IDP_HOST:
        return (
            parts.path == IDP_SSO
            and method in {"GET", "POST"}
            and resource_type == "document"
        ) or (
            parts.path in IDP_STYLES
            and method == "GET"
            and resource_type == "stylesheet"
            and not parts.query
        )
    return False


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise PlaywrightMissing() from None
    return sync_playwright


def _launch(pw, **kwargs):
    try:
        return pw.chromium.launch(**kwargs)
    except Exception:  # noqa: BLE001 -- redact the external browser boundary
        raise SsoError(
            "Browser engine unavailable; check the optional Chromium installation"
        ) from None


def save_state(context: BrowserContext, path: Path) -> int:
    try:
        return write_state(context.storage_state(), path)
    except Exception:  # noqa: BLE001 -- never expose browser/state diagnostics
        raise SsoError("Scoped SIO cookies could not be saved securely") from None


class SsoSession:
    """Only exact SIO pages; no arbitrary caller/configuration host extension."""

    def __init__(
        self, state_file: str | os.PathLike[str], hosts: list[str] | None = None
    ):
        self.state_file = Path(state_file).expanduser()
        if not self.state_file.is_absolute():
            raise SsoError("sso.state_file must be an absolute local path")
        if hosts is not None and hosts != [SIO_HOST]:
            raise SsoError("Only the verified SIO host is supported")

    def _check_host(self, url: str) -> str:
        if not landed(url, SIO_HOST):
            raise SsoError("Only exact verified SIO page URLs can be read")
        return SIO_HOST

    @contextlib.contextmanager
    def _context(self, *, login):
        browser = context = None
        try:
            state = None if login else read_state(self.state_file)
            if not login and (state is None or not state["cookies"]):
                raise NeedsLogin()
            with _playwright()() as pw:
                try:
                    browser = _launch(pw, headless=not login)
                    context = browser.new_context(
                        storage_state=state,
                        service_workers="block",
                        accept_downloads=False,
                        java_script_enabled=login,
                    )
                    yield context
                finally:
                    # Separate cleanup: one failing close must not skip the other.
                    for resource in (context, browser):
                        if resource is not None:
                            with contextlib.suppress(Exception):
                                resource.close()
        except FileNotFoundError:
            raise NeedsLogin() from None
        except SsoError:
            raise
        except Exception:  # noqa: BLE001 -- public transport sanitization boundary
            raise SsoError("SSO session or browser transport unavailable") from None

    def _guard(self, context, *, login):
        blocked = []
        primary = []

        def route_request(route):
            request = route.request
            response = None
            try:
                # Read parsing needs markup only. Suppress passive presentation
                # requests without sending them or treating their absence as an
                # authentication failure. Never relax navigation/script/write rules.
                if (
                    not login
                    and request.method == "GET"
                    and request.resource_type in {"stylesheet", "image", "font"}
                ):
                    route.abort()
                    return
                if (
                    not primary
                    or request.frame != primary[0].main_frame
                    or not allowed_request(
                        request.url, request.method, request.resource_type, login=login
                    )
                ):
                    blocked.append(True)
                    route.abort()
                    return
                # Playwright route.continue_ can follow a redirect without another
                # route callback. Fetch exactly ONE response and fulfill it instead.
                # A browser redirect then creates a new independently checked request.
                response = route.fetch(max_redirects=0, timeout=30_000)
                route.fulfill(response=response)
            except Exception:  # noqa: BLE001 -- fail closed in external callbacks
                blocked.append(True)
                with contextlib.suppress(Exception):
                    route.abort()
            finally:
                if response is not None:
                    with contextlib.suppress(Exception):
                        response.dispose()

        def close_socket(socket):
            blocked.append(True)
            socket.close()

        context.route("**/*", route_request)
        context.route_web_socket("**/*", close_socket)
        page = context.new_page()
        primary.append(page)
        context.on("page", lambda popup: popup.close())
        return page, blocked

    def login(self, landing_url: str, timeout_s: int = 300) -> int:
        """Visible manual sign-in; incomplete MFA policy currently fails closed."""
        self._check_host(landing_url)
        with self._context(login=True) as context:
            page, blocked = self._guard(context, login=True)
            page.goto(
                landing_url, wait_until="domcontentloaded", timeout=timeout_s * 1000
            )
            page.wait_for_url(
                lambda current: str(current) == landing_url, timeout=timeout_s * 1000
            )
            if blocked or page.url != landing_url:
                raise SsoError("SSO request policy blocked sign-in; nothing saved")
            return save_state(context, self.state_file)

    def fetch(self, url: str, timeout_s: int = 60) -> str:
        """Read an exact page without JavaScript; no silent IdP/MFA credential replay."""
        self._check_host(url)
        with self._context(login=False) as context:
            page, blocked = self._guard(context, login=False)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            if blocked:
                raise SsoError("SSO request policy blocked the page load")
            if at_login_wall(page.url):
                raise NeedsLogin()
            if page.url != url:
                raise SsoError("SSO read did not reach the exact requested page")
            html = page.content()
            if blocked or page.url != url:
                raise SsoError("SSO page changed outside the requested boundary")
            save_state(context, self.state_file)
            return html


def session_from_config(
    sso: dict | None, hosts: list[str] | None = None
) -> SsoSession | None:
    if not isinstance(sso, dict) or sso.get("enabled") is not True:
        return None
    state_file = sso.get("state_file")
    if not isinstance(state_file, str) or not state_file:
        raise SsoError("sso.state_file must be an explicit path")
    return SsoSession(state_file, hosts=hosts)
