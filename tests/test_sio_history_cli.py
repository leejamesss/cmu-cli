"""Ensure the history CLI selects history, never the current queue or probe."""

import json
import sys
from unittest.mock import Mock

import pytest

from cmu_cli import cli, models, sio_client


def test_history_dispatch(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(models, "CONFIG_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(sys, "argv", ["cmu-cli", "sio", "waitlist-history", "--json"])
    client = Mock()
    client.waitlist_history.return_value = {
        "status": "ok",
        "complete": True,
        "provenance": "synthetic",
        "view": "waitlist_history",
        "waitlist_history": [],
        "waitlist": None,
    }
    monkeypatch.setattr(sio_client, "SIOClient", Mock(return_value=client))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["data"]["view"] == "waitlist_history"
    assert data["data"]["waitlist"] is None
    client.waitlist_history.assert_called_once_with()
    client.probe.assert_not_called()
    client.semester_schedule.assert_not_called()
