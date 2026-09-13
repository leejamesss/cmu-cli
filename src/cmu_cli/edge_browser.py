"""Open configured HTTPS URLs; no browser read, cookie or JavaScript APIs."""

import webbrowser
from urllib.parse import urlsplit


class BrowserError(RuntimeError):
    pass


class EdgeBrowser:
    """Compatibility name; opens the operating system's default browser."""

    def open(self, url: str, activate: bool = True) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or any(ord(c) < 32 for c in url)
        ):
            raise BrowserError("Only credential-free HTTPS URLs can be opened")
        if not webbrowser.open(url, new=2, autoraise=activate):
            raise BrowserError(
                "No usable default browser; open the configured URL manually"
            )
