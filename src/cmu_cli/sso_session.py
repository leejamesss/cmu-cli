"""Shibboleth-backed reads for pages a cookie database cannot reach.

The default transport reads an explicitly selected browser cookie database and
refuses cross-origin redirects. That is the right default and nothing here changes
it. It cannot authenticate SIO, though, and the reason is structural rather than a
missing feature:

  * SIO is fronted by Shibboleth, so the application session on s3.andrew.cmu.edu is
    established by a redirect chain through login.cmu.edu and back. `safe_request`
    blocks that chain before transmission -- correctly, since following it would send
    cookies to another origin -- so the read returns LOGIN_OR_ORIGIN_REDIRECT_BLOCKED;
  * the cookie Shibboleth sets has no expiry. Chromium keeps such cookies in memory,
    so they are frequently absent from the on-disk database the default path reads,
    and logging in again does not put them there.

This module takes the other route: a real browser engine completes the redirect
chain, and the resulting storage state -- which does include session cookies -- is
saved and replayed for later reads. That means executing a browser, which the default
path deliberately does not do, so it is opt-in twice over: the `sso` extra must be
installed, and `sso.enabled` must be set in configuration. Nothing imports Playwright
until a caller asks for a session.

What it does not do: no password, no MFA code and no credential of any kind is read,
typed, stored or logged. You authenticate in a window you can see, exactly as you
would normally, and only the resulting cookies are kept -- owner-readable, in a file
you can delete. Reads remain GET page loads of explicitly listed URLs.

Adapted from a working implementation by @HorizonWind2004.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext

# Hosts the sign-in chain is allowed to pass through. Landing on one of these is not
# a successful read; it is the wall.
LOGIN_HOSTS = ("login.cmu.edu", "duo.com", "duosecurity.com")


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


def at_login_wall(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in LOGIN_HOSTS)


def landed(url: str, want_host: str) -> bool:
    """Are we actually on the host we asked for?

    Stated positively on purpose. "Not a login host" is satisfied by about:blank and
    by any error page the browser substitutes, so a closed tab would have counted as
    a completed sign-in.
    """
    host = urlsplit(url).hostname
    return bool(want_host) and host == want_host and not at_login_wall(url)


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlaywrightMissing() from exc
    return sync_playwright


def _launch(pw, **kwargs):
    from playwright.sync_api import Error as PlaywrightError

    try:
        return pw.chromium.launch(**kwargs)
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc):
            raise PlaywrightMissing() from exc
        raise SsoError("Browser engine unavailable") from None


def save_state(context: BrowserContext, path: Path) -> int:
    """Persist cookies, session ones included, readable only by their owner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    state = context.storage_state()
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return len(state.get("cookies", []))


class SsoSession:
    """An explicitly authorized Shibboleth session, replayed for GET page reads."""

    def __init__(
        self, state_file: str | os.PathLike[str], hosts: list[str] | None = None
    ):
        self.state_file = Path(state_file).expanduser()
        # The same discipline the default transport applies: a read may only touch a
        # host the configuration named.
        self.hosts = tuple(hosts or ())

    def _check_host(self, url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise SsoError("Only HTTPS URLs can be read")
        if self.hosts and parsed.hostname not in self.hosts:
            raise SsoError("URL host is not explicitly allowed")
        return parsed.hostname

    @contextlib.contextmanager
    def _replayed(self) -> Iterator[BrowserContext]:
        if not self.state_file.exists():
            raise NeedsLogin()
        sync_playwright = _playwright()
        with sync_playwright() as pw:
            browser = _launch(pw, headless=True)
            context = browser.new_context(storage_state=str(self.state_file))
            try:
                yield context
            finally:
                with contextlib.suppress(Exception):
                    context.close()
                    browser.close()

    def login(self, landing_url: str, timeout_s: int = 300) -> int:
        """Open a visible window; you authenticate; only cookies are kept."""
        want = self._check_host(landing_url)
        sync_playwright = _playwright()
        with sync_playwright() as pw:
            browser = _launch(pw, headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(landing_url, wait_until="domcontentloaded")
            from playwright.sync_api import Error as PlaywrightError

            try:
                page.wait_for_url(
                    lambda url: landed(url, want), timeout=timeout_s * 1000
                )
            except PlaywrightError:
                # Covers both the timeout and the window being closed early; neither
                # is evidence of a completed sign-in.
                raise NeedsLogin(
                    "Sign-in did not complete before the window closed or timed out; "
                    "nothing was saved"
                ) from None
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=15_000)
            if not landed(page.url, want):
                raise NeedsLogin(
                    "Sign-in did not reach the requested host; nothing saved"
                )
            count = save_state(context, self.state_file)
            with contextlib.suppress(Exception):
                context.close()
                browser.close()
        if not count:
            raise NeedsLogin("No cookies were captured; the session would not work")
        return count

    def fetch(self, url: str, timeout_s: int = 60) -> str:
        """GET one page and return its rendered HTML."""
        self._check_host(url)
        with self._replayed() as context:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            if at_login_wall(page.url):
                # The IdP session may carry us through without interaction.
                with contextlib.suppress(Exception):
                    page.wait_for_url(
                        lambda current: not at_login_wall(current), timeout=12_000
                    )
            else:
                with contextlib.suppress(Exception):
                    page.wait_for_load_state("networkidle", timeout=10_000)
            if at_login_wall(page.url):
                raise NeedsLogin()
            html = page.content()
            # Shibboleth rotates its cookie as you browse; keep the freshest copy.
            with contextlib.suppress(Exception):
                save_state(context, self.state_file)
            return html


def session_from_config(
    sso: dict | None, hosts: list[str] | None = None
) -> SsoSession | None:
    """Build a session only from an explicit, enabled configuration block."""
    if not isinstance(sso, dict) or sso.get("enabled") is not True:
        return None
    state_file = sso.get("state_file")
    if not isinstance(state_file, str) or not state_file:
        raise SsoError("sso.state_file must be an explicit path")
    if not Path(state_file).expanduser().is_absolute():
        raise SsoError("sso.state_file must be an absolute local path")
    return SsoSession(state_file, hosts=hosts)
