"""Manual, isolated persistent SSO runner. Diagnostics never contain browser data.

The parent owns this foreground process. Poll/reader errors never close its context;
only explicit interruption, user closure, or the advertised deadline ends it.
The dedicated profile retains browser state; only scoped SIO state is exported.
"""

import argparse
import contextlib
import json
import os
import time
import uuid
from pathlib import Path

from .sso_session import SsoSession, at_login_wall
from .sso_state import private_directory, write_state

LANDING = "https://s3.andrew.cmu.edu/sio/"
ROOT = Path("/private/tmp/cmu-sio-fresh-probe")
LABEL = "CMU SIO Fresh Login Verification"


def prepare(root):
    with private_directory(root, create=True):
        pass
    with private_directory(root / "profile", create=True):
        pass


def publish(root, state):
    """Atomic owner-only heartbeat, even on navigation retries; no exception text."""
    state["updated_at"] = time.time()
    with private_directory(root) as directory:
        name = ".probe-status-" + uuid.uuid4().hex
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(state, handle, indent=2)
            os.replace(name, "status.json", src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=directory)


def poll(page, context, blocked, root, state):
    """One recoverable tick. Never tears down a context or removes saved state."""
    try:
        if page.is_closed():
            state["phase"] = "browser_closed"
            return False
        if at_login_wall(page.url):
            visible = page.locator('input[type="password"]:visible').count()
            state["phase"] = "manual_login_ready" if visible else "idp_waiting"
        elif page.url == LANDING:
            state["phase"] = "sio_returned_not_yet_verified"
            # A URL alone can be a provisional redirect/error page. Require a
            # completed, non-redirecting document and a nonempty scoped snapshot.
            ready = page.evaluate("""() => document.readyState === 'complete' &&
                !document.querySelector('meta[http-equiv="refresh" i]') &&
                !document.querySelector('input[type="password"]')""")
            if ready and not blocked:
                snapshot = context.storage_state()
                if page.url == LANDING and not blocked:
                    write_state(snapshot, root / "session.json")
                    state["session_saved"] = True
                    state["phase"] = "scoped_session_saved"
        else:
            state["phase"] = "policy_blocked" if blocked else "navigating"
        state["error"] = None
    except Exception:  # noqa: BLE001 -- navigation destroys locators/contexts
        state["phase"] = "retrying"
        state["error"] = "browser_poll_retry"
    state["policy_violation"] = bool(blocked)
    return True


def heartbeat(root, state):
    # Status readers and filesystem errors must not tear down a manual login.
    with contextlib.suppress(Exception):
        publish(root, state)


def monitor(page, context, blocked, root, state, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        running = poll(page, context, blocked, root, state)
        heartbeat(root, state)
        if not running:
            return
        try:
            # Pump Playwright events without letting a racing navigation or
            # reader shutdown unwind the browser-owning scope.
            page.wait_for_timeout(1000)
        except Exception:  # noqa: BLE001 -- fixed diagnostics only
            state["phase"] = "retrying"
            state["error"] = "browser_wait_retry"
            heartbeat(root, state)
            time.sleep(1)
    state["phase"] = "expired"
    heartbeat(root, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--timeout-s", type=int, default=2700)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.timeout_s <= 86400:
        parser.error("timeout must be between 1 and 86400 seconds")
    root = args.root
    state = {
        "phase": "starting",
        "pid": os.getpid(),
        "window_label": LABEL,
        "session_saved": False,
        "error": None,
    }
    try:
        prepare(root)
        publish(root, state)
        if args.prepare_only:
            state["phase"] = "prepared"
            publish(root, state)
            return 0
        from playwright.sync_api import sync_playwright

        # No CDP listener, no attachment to any existing browser. A private
        # persistent profile survives runner restarts; no ephemeral context.
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(root / "profile"),
                channel="chrome",
                headless=False,
                service_workers="block",
                accept_downloads=False,
                args=["--window-size=1000,850"],
            )
            page, blocked = SsoSession(root / "session.json")._guard(
                context, login=True
            )
            try:
                page.goto(LANDING, wait_until="domcontentloaded", timeout=60000)
            except Exception:  # noqa: BLE001 -- continue manual flow after navigation race
                state["error"] = "initial_navigation_retry"
            monitor(page, context, blocked, root, state, args.timeout_s)
            context.close()  # only advertised deadline or explicit user closure
    except KeyboardInterrupt:
        state["phase"] = "interrupted"
    except Exception:  # noqa: BLE001 -- never print exception URLs or browser payloads
        state["phase"] = "runner_unavailable"
        state["error"] = "runner_unavailable"
    with contextlib.suppress(Exception):
        publish(root, state)
    return 0 if state["phase"] in {"expired", "browser_closed", "interrupted"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
