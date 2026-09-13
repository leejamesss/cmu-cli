"""Behavior preserved by the lint cleanup, using synthetic failures only."""

import json
import sys

import pytest

from cmu_cli import cli
from cmu_cli.models import InvalidTypeError, load_config
from cmu_cli.storage import load_json_object


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, AssertionError])
def test_unexpected_cli_failure_is_sanitized(monkeypatch, capsys, error_type):
    def fail(_):
        raise error_type("SYNTHETIC_SECRET_DO_NOT_PRINT")

    monkeypatch.setattr(cli, "load_config", fail)
    monkeypatch.setattr(sys, "argv", ["cmu-cli", "doctor", "--json"])
    with pytest.raises(SystemExit) as result:
        cli.main()
    captured = capsys.readouterr()
    assert result.value.code == 2
    assert "SYNTHETIC_SECRET" not in captured.out + captured.err
    assert json.loads(captured.out)["error"]["code"] == "CONFIG_OR_OPERATION_FAILED"


@pytest.mark.parametrize("field,value", [("courses", {}), ("timezone", 42)])
def test_invalid_config_types_keep_value_error_compatibility(tmp_path, field, value):
    raw = {
        "canvas_base_url": "https://canvas.example.invalid",
        "courses": [],
        "storage_root": "files",
    }
    raw[field] = value
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(InvalidTypeError) as result:
        load_config(path)
    assert isinstance(result.value, (TypeError, ValueError))
    assert isinstance(result.value, ValueError)
    assert isinstance(result.value, TypeError)


def test_invalid_manifest_type_preserves_compatibility(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("[]")
    with pytest.raises(ValueError) as result:
        load_json_object(path)
    assert isinstance(result.value, TypeError)
