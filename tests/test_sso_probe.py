"""Offline runner regressions: no browsers, credentials, or institutional calls."""

import json
import stat
from types import SimpleNamespace

import pytest

from cmu_cli import sso_probe as probe


def snapshot():
    return {
        "cookies": [
            {
                "name": "_shibsession_synthetic",
                "value": "SYNTHETIC",
                "domain": "s3.andrew.cmu.edu",
                "path": "/",
                "secure": True,
            }
        ],
        "origins": [],
    }


def fake_page():
    return SimpleNamespace(
        url=probe.LANDING,
        is_closed=lambda: False,
        evaluate=lambda _: True,
        close=lambda: pytest.fail("poll must not close browser"),
    )


def test_navigation_race_recovers_and_saves_without_closure(tmp_path):
    page = fake_page()
    page.url = "https://login.cmu.edu/idp/profile/SAML2/Redirect/SSO"

    def race(_):
        raise RuntimeError(
            "Execution context destroyed https://secret/?token=SYNTHETIC"
        )

    page.locator = race
    context = SimpleNamespace(
        storage_state=snapshot, close=lambda: pytest.fail("must not close context")
    )
    state = {}
    assert probe.poll(page, context, [], tmp_path, state)
    assert state["phase"] == "retrying"
    probe.publish(tmp_path, state)
    first = json.loads((tmp_path / "status.json").read_text())
    assert "SYNTHETIC" not in json.dumps(first)
    assert first["error"] == "browser_poll_retry"
    page.url = probe.LANDING
    assert probe.poll(page, context, [], tmp_path, state)
    assert state["phase"] == "scoped_session_saved"
    saved = (tmp_path / "session.json").read_bytes()
    page.evaluate = race
    assert probe.poll(page, context, [], tmp_path, state)
    probe.publish(tmp_path, state)
    assert state["session_saved"]
    assert (tmp_path / "session.json").read_bytes() == saved
    assert state["updated_at"] >= first["updated_at"]
    assert stat.S_IMODE((tmp_path / "session.json").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "case", ["wrong_url", "provisional", "blocked", "snapshot_race", "no_cookie"]
)
def test_unverified_landing_never_saves(tmp_path, case):
    page = fake_page()
    blocked = [True] if case == "blocked" else []
    if case == "wrong_url":
        page.url += "?secret=SYNTHETIC"
    if case == "provisional":
        page.evaluate = lambda _: False

    def storage():
        if case == "snapshot_race":
            page.url = "about:blank"
        return {"cookies": []} if case == "no_cookie" else snapshot()

    assert probe.poll(
        page, SimpleNamespace(storage_state=storage), blocked, tmp_path, {}
    )
    assert not (tmp_path / "session.json").exists()


def test_wait_race_does_not_drop_session_or_close_context(tmp_path, monkeypatch):
    page = fake_page()
    ticks = iter([0, 0, 1, 3])
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)

    def wait(_):
        raise RuntimeError("SYNTHETIC context replaced")

    page.wait_for_timeout = wait
    state = {}
    probe.monitor(page, SimpleNamespace(storage_state=snapshot), [], tmp_path, state, 2)
    assert state["phase"] == "expired"
    assert state["session_saved"]
    assert (tmp_path / "session.json").exists()
    assert "SYNTHETIC" not in (tmp_path / "status.json").read_text()


def test_status_write_failure_does_not_interrupt_monitor(tmp_path, monkeypatch):
    page = fake_page()
    page.wait_for_timeout = lambda _: None
    ticks = iter([0, 0, 2])
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(ticks))

    def fail(*args):
        raise OSError("SYNTHETIC status reader failure")

    monkeypatch.setattr(probe, "publish", fail)
    state = {}
    probe.monitor(page, SimpleNamespace(storage_state=snapshot), [], tmp_path, state, 1)
    assert state["session_saved"]
    assert (tmp_path / "session.json").exists()


def test_prepare_only_private_profile_without_browser(tmp_path, monkeypatch):
    root = tmp_path / "probe"
    monkeypatch.setattr("sys.argv", ["probe", "--root", str(root), "--prepare-only"])
    assert probe.main() == 0
    for path in [root, root / "profile"]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "status.json").stat().st_mode) == 0o600
    assert not (root / "session.json").exists()
