import json
import os
import socket
import sys
import webbrowser

import pytest

from cmu_cli import cli
from cmu_cli.auth import (
    OOB,
    RegistrationError,
    check_registration,
    validate_registration,
)


def registration():
    return {
        "canvas_base_url": "https://canvas.example.edu",
        "client_id": "12345",
        "client_type": "public",
        "deployment": "native",
        "redirect_uri": OOB,
        "scopes": ["url:GET|/api/v1/courses"],
        "pkce_method": "S256",
        "secret_custody": "none",
    }


def test_valid_metadata_is_not_authorization():
    result = validate_registration(registration())
    assert result["metadata_valid"]
    assert not result["registration_verified"]
    assert not result["oauth_implemented"]
    assert not result["credentials_read"]


@pytest.mark.parametrize(
    "changes",
    [
        {"client_secret": "SYNTHETIC_SECRET"},
        {"access_token": "SYNTHETIC_TOKEN"},
        {"client_type": "automatic"},
        {"client_id": 12345},
        {"client_id": " 12345"},
        {"pkce_method": "none"},
        {"pkce_method": "plain"},
        {"secret_custody": "shared_embedded"},
        {"redirect_uri": "http://127.0.0.1:8000/callback"},
        {"redirect_uri": "cmu-cli://callback"},
        {"canvas_base_url": "https://user:secret@canvas.example.edu"},
        {"canvas_base_url": "https://canvas.example.edu?token=secret"},
        {"canvas_base_url": "https://canvas.example.edu#"},
        {"canvas_base_url": "https://canvas.example.edu:bad"},
        {"canvas_base_url": "https://canvas.example.edu/"},
        {"canvas_base_url": "https://canvas.example.edu\n"},
        {"scopes": []},
        {"scopes": ["url:POST|/api/v1/courses"]},
        {"scopes": ["url:GET|/api/v1/../accounts"]},
        {"scopes": ["url:GET|/api/v1/courses"] * 2},
        {"scopes": [{}]},
        {"deployment": "guess"},
    ],
)
def test_invalid(changes):
    raw = registration()
    raw.update(changes)
    with pytest.raises(RegistrationError):
        validate_registration(raw)


def test_confidential_custody():
    raw = registration()
    raw.update(
        client_type="confidential",
        deployment="server",
        redirect_uri="https://broker.example.edu/oauth/callback",
        secret_custody="institution_server",
        pkce_method="none",
    )
    assert validate_registration(raw)["metadata_valid"]
    raw["secret_custody"] = "none"
    with pytest.raises(RegistrationError, match="SECRET_CUSTODY"):
        validate_registration(raw)


def test_cli_offline_and_never_claims_ready(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected network/browser/client access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(webbrowser, "open", forbidden)
    monkeypatch.setattr(cli, "CanvasClient", forbidden)
    monkeypatch.setattr(cli, "load_config", forbidden)
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(registration()))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cmu-cli",
            "auth",
            "check-registration",
            "--registration",
            str(path),
            "--json",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "partial"
    assert payload["complete"] is False
    assert payload["data"]["registration_verified"] is False
    path.write_text('{"client_secret": "SYNTHETIC_PRIVATE_VALUE"}')
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE_VALUE" not in output.out + output.err
    assert json.loads(output.out)["error"]["code"] == "OAUTH_REGISTRATION_FIELDS"


@pytest.mark.parametrize(
    "kind", ["duplicate", "large", "invalid", "symlink", "fifo", "directory", "missing"]
)
def test_safe_bounded_read(tmp_path, kind):
    path = tmp_path / "input.json"
    if kind == "duplicate":
        path.write_text('{"client_id":"1","client_id":"2"}')
    elif kind == "large":
        path.write_bytes(b" " * 16385)
    elif kind == "invalid":
        path.write_bytes(b"\xff")
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text(json.dumps(registration()))
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    with pytest.raises(RegistrationError):
        check_registration(path)
