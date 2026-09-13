"""Name the browser profile databases that exist, without opening any of them.

Automatic profile discovery is deliberately not performed when authenticating: an
unattended scan for credential stores is the behaviour this tool exists to avoid,
and naming the exact database is how consent is expressed. That is a good property
and this module does not change it -- nothing here reads, decrypts, or opens a
database, and nothing here is reachable from an authenticated read path.

What it removes is the part that was never protective: making someone open
edge://version, read a profile path out of a browser page and retype it correctly.
A candidate list is still a choice the caller has to make.
"""

from __future__ import annotations

import os
from pathlib import Path

# Fixed, documented per-browser locations. Not a search: each candidate is a
# stat() on a known name, so nothing outside these directories is ever examined.
BROWSER_ROOTS = {
    "edge": [
        "~/Library/Application Support/Microsoft Edge",
        "~/.config/microsoft-edge",
    ],
    "chrome": [
        "~/Library/Application Support/Google/Chrome",
        "~/.config/google-chrome",
    ],
}
PROFILE_NAMES = ["Default", *[f"Profile {n}" for n in range(1, 10)]]
DATABASE_NAMES = ["Network/Cookies", "Cookies"]


def candidate_databases() -> list[dict[str, str]]:
    """Existing cookie databases at the standard locations, newest profile first.

    Returns paths and metadata only. The modification time is read from the
    directory entry to order the list; the file itself is never opened.
    """
    found: list[dict[str, str]] = []
    seen: set[Path] = set()
    for browser, roots in BROWSER_ROOTS.items():
        for root in roots:
            base = Path(os.path.expanduser(root))
            for profile in PROFILE_NAMES:
                for database in DATABASE_NAMES:
                    path = base / profile / database
                    try:
                        if not path.is_file() or path.is_symlink():
                            continue
                        modified = path.stat().st_mtime
                    except OSError:
                        continue
                    if path in seen:
                        continue
                    seen.add(path)
                    found.append(
                        {
                            "browser": browser,
                            "profile": profile,
                            "cookie_file": str(path),
                            "modified": modified,
                        }
                    )
    found.sort(key=lambda row: row["modified"], reverse=True)
    for row in found:
        del row["modified"]
    return found
