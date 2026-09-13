"""Listing candidate databases must stay a listing.

Automatic profile discovery is deliberately not performed on an authenticated read
path: an unattended scan for credential stores is the behaviour this tool exists to
avoid, and naming the exact database is how consent is expressed. This helper keeps
that -- it reports paths so the caller can choose one, and must never open, decrypt
or otherwise touch a database's contents.
"""

from pathlib import Path

import pytest

from cmu_cli import browser_profiles


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    root = tmp_path / "Chrome"
    monkeypatch.setattr(
        browser_profiles, "BROWSER_ROOTS", {"chrome": [str(root)], "edge": []}
    )
    return root


def _database(root: Path, profile: str, name: str = "Cookies") -> Path:
    path = root / profile / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"SQLite format 3\x00synthetic")
    return path


def test_an_existing_profile_is_listed(fake_home):
    _database(fake_home, "Default")
    rows = browser_profiles.candidate_databases()
    assert [row["profile"] for row in rows] == ["Default"]
    assert rows[0]["browser"] == "chrome"


def test_several_profiles_are_all_offered(fake_home):
    _database(fake_home, "Default")
    _database(fake_home, "Profile 1")
    rows = browser_profiles.candidate_databases()
    assert {row["profile"] for row in rows} == {"Default", "Profile 1"}


def test_the_most_recently_used_profile_comes_first(fake_home):
    old = _database(fake_home, "Default")
    recent = _database(fake_home, "Profile 1")
    import os

    os.utime(old, (1_000_000, 1_000_000))
    rows = browser_profiles.candidate_databases()
    assert rows[0]["cookie_file"] == str(recent)


def test_a_profile_with_no_database_is_not_listed(fake_home):
    (fake_home / "Profile 2").mkdir(parents=True)
    assert browser_profiles.candidate_databases() == []


def test_nothing_installed_is_an_empty_list_not_an_error(fake_home):
    assert browser_profiles.candidate_databases() == []


def test_a_symlinked_database_is_refused(fake_home):
    real = _database(fake_home, "Profile 1")
    link = fake_home / "Default" / "Cookies"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)
    listed = {row["cookie_file"] for row in browser_profiles.candidate_databases()}
    assert str(link) not in listed


def test_the_database_is_never_opened(fake_home, monkeypatch):
    """The property that makes this safe to run unattended."""
    _database(fake_home, "Default")

    def refuse(*args, **kwargs):
        raise AssertionError("candidate_databases must not open a cookie database")

    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    assert browser_profiles.candidate_databases()


def test_no_row_carries_a_modification_time(fake_home):
    """Ordering is internal; a listing should not leak browsing recency."""
    _database(fake_home, "Default")
    assert set(browser_profiles.candidate_databases()[0]) == {
        "browser",
        "profile",
        "cookie_file",
    }
