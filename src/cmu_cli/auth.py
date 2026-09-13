"""Offline registration preflight, not an OAuth client or credential store."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit

from .storage import directory_fd

OOB = "urn:ietf:wg:oauth:2.0:oob"
MAX_REGISTRATION_BYTES = 16384


class RegistrationError(ValueError):
    """Only fixed, non-sensitive diagnostic codes may be raised."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RegistrationError("OAUTH_REGISTRATION_DUPLICATE_FIELD")
        result[key] = value
    return result


def _https(value, *, origin=False):
    if not isinstance(value, str) or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
        return bool(
            value.startswith("https://")
            and parsed.hostname
            and re.fullmatch(r"[a-zA-Z0-9.-]+", parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and "?" not in value
            and "#" not in value
            and (port is None or 1 <= port <= 65535)
            and (not origin or not parsed.path)
        )
    except ValueError:
        return False


def validate_registration(raw):
    """Validate non-secret metadata only; never infer provider authorization."""
    fields = {
        "canvas_base_url",
        "client_id",
        "client_type",
        "deployment",
        "redirect_uri",
        "scopes",
        "pkce_method",
        "secret_custody",
    }
    if not isinstance(raw, dict) or set(raw) != fields:
        raise RegistrationError("OAUTH_REGISTRATION_FIELDS")
    if not _https(raw["canvas_base_url"], origin=True):
        raise RegistrationError("OAUTH_REGISTRATION_ORIGIN")
    if not isinstance(raw["client_id"], str) or not re.fullmatch(
        r"[0-9]{1,64}", raw["client_id"]
    ):
        raise RegistrationError("OAUTH_REGISTRATION_CLIENT_ID")
    if raw["client_type"] not in ("public", "confidential"):
        raise RegistrationError("OAUTH_REGISTRATION_CLIENT_TYPE")
    if raw["deployment"] not in ("native", "server"):
        raise RegistrationError("OAUTH_REGISTRATION_DEPLOYMENT")
    redirect = raw["redirect_uri"]
    if (raw["deployment"] == "native" and redirect != OOB) or (
        raw["deployment"] == "server" and not _https(redirect)
    ):
        raise RegistrationError("OAUTH_REGISTRATION_REDIRECT")
    if raw["pkce_method"] not in ("S256", "none") or (
        raw["client_type"] == "public" and raw["pkce_method"] != "S256"
    ):
        raise RegistrationError("OAUTH_REGISTRATION_PKCE")
    expected_custody = (
        "none"
        if raw["client_type"] == "public"
        else "institution_server"
        if raw["deployment"] == "server"
        else "institution_approved_per_installation"
    )
    if raw["secret_custody"] != expected_custody:
        raise RegistrationError("OAUTH_REGISTRATION_SECRET_CUSTODY")
    scopes = raw["scopes"]
    if (
        not isinstance(scopes, list)
        or not 1 <= len(scopes) <= 64
        or any(
            not isinstance(scope, str)
            or not re.fullmatch(r"url:GET\|/api/v1/[A-Za-z0-9_:/.-]+", scope)
            or any(part in (".", "..") for part in scope.split("/"))
            for scope in scopes
        )
        or len(set(scopes)) != len(scopes)
        or len(" ".join(scopes)) > 4000
    ):
        raise RegistrationError("OAUTH_REGISTRATION_SCOPES")
    return {
        "metadata_valid": True,
        "registration_verified": False,
        "oauth_implemented": False,
        "network_used": False,
        "credentials_read": False,
        "blockers": [
            "INSTITUTION_REGISTRATION_AND_DEPLOYMENT_CONFIRMATION_REQUIRED",
            "OAUTH_ONBOARDING_NOT_IMPLEMENTED",
            "AUTHORIZED_LIVE_LIFECYCLE_VALIDATION_REQUIRED",
        ],
    }


def check_registration(path: Path):
    """Read only an explicitly selected, bounded regular metadata file."""
    try:
        with directory_fd(path.parent) as parent:
            descriptor = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            with os.fdopen(descriptor, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise RegistrationError("OAUTH_REGISTRATION_NOT_REGULAR")
                data = handle.read(MAX_REGISTRATION_BYTES + 1)
        if len(data) > MAX_REGISTRATION_BYTES:
            raise RegistrationError("OAUTH_REGISTRATION_TOO_LARGE")
        return validate_registration(json.loads(data, object_pairs_hook=_pairs))
    except RegistrationError:
        raise
    except (OSError, ValueError, RuntimeError, RecursionError):
        raise RegistrationError(
            "OAUTH_REGISTRATION_UNREADABLE_OR_INVALID_JSON"
        ) from None
