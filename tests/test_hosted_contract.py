"""Offline regressions for hosted-release machine contracts."""

import copy
import json
import sys

import pytest
from jsonschema import ValidationError

from cmu_cli import cli
from tests.test_auth_registration import registration
from tests.test_final_integration import VALIDATOR


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_provider_data_is_one_safe_error(monkeypatch, capsys, value):
    from cmu_cli import demo

    monkeypatch.setattr(demo, "run_demo", lambda: {"points": value})
    monkeypatch.setattr(sys, "argv", ["cmu-cli", "demo", "--json"])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    VALIDATOR.validate(payload)
    assert payload["status"] == "error"
    assert payload["data"] is None
    assert "NaN" not in captured.out and "Infinity" not in captured.out


@pytest.mark.parametrize(
    "value", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"]
)
def test_timestamp_utc_overflow_is_unknown(value):
    assert cli.timestamp(value) is None


def test_auth_schema_rejects_false_readiness(tmp_path, monkeypatch, capsys):
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
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 3
    payload = json.loads(capsys.readouterr().out)
    VALIDATOR.validate(payload)
    assert payload["command"] == "auth"
    assert payload["data"]["blockers"]
    for field in [
        "registration_verified",
        "oauth_implemented",
        "network_used",
        "credentials_read",
    ]:
        invalid = copy.deepcopy(payload)
        invalid["data"][field] = True
        with pytest.raises(ValidationError):
            VALIDATOR.validate(invalid)
    for field, value in [("complete", True), ("error", {}), ("data", None)]:
        invalid = copy.deepcopy(payload)
        invalid[field] = value
        with pytest.raises(ValidationError):
            VALIDATOR.validate(invalid)
    path.write_text("not json")
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2
    VALIDATOR.validate(json.loads(capsys.readouterr().out))
