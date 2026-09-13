"""Minimal SIO cookies in a private, no-follow, atomically replaced file.

No existing browser profile is read. Only filtered SIO cookies are persisted;
the isolated context's transient storage snapshot is filtered before writing.
The caller must select a private directory without symlink components.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import uuid
from pathlib import Path


class StateError(RuntimeError):
    """Fixed-message local credential storage error."""


def minimal_state(state: dict) -> dict:
    """Retain only secure host-scoped SIO application/Shibboleth cookies.

    IdP/MFA state is deliberately not replayed. A stale application session needs
    another explicit login. Cookie values never enter diagnostics.
    """
    cookies = []
    for cookie in state.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name", "")
        path = cookie.get("path")
        if (
            cookie.get("domain") != "s3.andrew.cmu.edu"
            or cookie.get("secure") is not True
            or not isinstance(name, str)
            or not isinstance(cookie.get("value"), str)
            or not (
                (name.startswith("_shibsession_") and path == "/")
                or (name == "JSESSIONID" and path in {"/sio", "/sio/"})
            )
        ):
            continue
        cookies.append(
            {
                key: cookie[key]
                for key in (
                    "name",
                    "value",
                    "domain",
                    "path",
                    "expires",
                    "httpOnly",
                    "secure",
                    "sameSite",
                )
                if key in cookie
            }
        )
    return {"cookies": cookies, "origins": []}


@contextlib.contextmanager
def private_directory(path: Path, *, create: bool = False):
    """Walk by directory descriptors; refuse links and unsafe parent ownership.

    A root-owned sticky temporary ancestor is acceptable; the immediate parent
    must be owned by this user and have no group/other permissions. Never chmod
    existing directories or follow a substituted path after validation.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise StateError(
            "SSO state requires an absolute, non-symlink private directory"
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o700, dir_fd=fd)
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid not in {0, os.getuid()} or (
                info.st_mode & 0o022
                and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
            ):
                raise StateError("SSO state directory is not trusted")
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise StateError("SSO state parent must be owner-only (0700)")
        yield fd
    finally:
        os.close(fd)


def _check_file(info):
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        raise StateError("SSO state must be a private regular file")


def write_state(state: dict, path: Path) -> int:
    """Create as 0600 before any write, then atomically replace in a pinned dir."""
    state = minimal_state(state)
    if not state["cookies"]:
        raise StateError("No scoped SIO cookies were captured; nothing saved")
    try:
        with private_directory(path.parent, create=True) as directory:
            temporary = ".sio-state-" + uuid.uuid4().hex
            created = False
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                fd = os.open(temporary, flags, 0o600, dir_fd=directory)
                created = True
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(state, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                with contextlib.suppress(FileNotFoundError):
                    _check_file(
                        os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                    )
                os.replace(
                    temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory
                )
            finally:
                if created:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(temporary, dir_fd=directory)
    except StateError:
        raise
    except Exception:  # noqa: BLE001 -- credential persistence boundary
        raise StateError("SSO state could not be saved securely") from None
    return len(state["cookies"])


def read_state(path: Path) -> dict:
    """Validate permissions on the opened descriptor and re-filter legacy state."""
    try:
        with private_directory(path.parent) as directory:
            fd = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                _check_file(os.fstat(handle.fileno()))
                raw = handle.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise StateError("SSO state is too large")
                return minimal_state(json.loads(raw))
    except FileNotFoundError:
        raise
    except StateError:
        raise
    except Exception:  # noqa: BLE001 -- untrusted stored credentials
        raise StateError("SSO state could not be read securely") from None
