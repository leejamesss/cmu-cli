"""Offline storage boundary, ownership and integrity regressions."""

import hashlib
import json
import os
import stat

import pytest

from cmu_cli import storage
from cmu_cli.models import Config, Course


def item(file_id=1, revision="v1", name="note.txt", content=b"ONE"):
    return {
        "id": file_id,
        "display_name": name,
        "updated_at": revision,
        "size": len(content),
        "url": "https://example.invalid/file",
    }


def sync(root, manifest, file_id=1, revision="v1", content=b"ONE", name="note.txt"):
    return storage.sync_canvas_file(
        item(file_id, revision, name, content), root, manifest, lambda _: content
    )


def test_predictable_part_symlink_is_not_used(tmp_path):
    victim = tmp_path / "victim"
    victim.write_bytes(b"USER")
    folder = tmp_path / "01_讲义"
    folder.mkdir()
    (folder / "note.txt.part").symlink_to(victim)
    result = sync(tmp_path, {})
    assert victim.read_bytes() == b"USER"
    assert not (tmp_path / result["relative_path"]).is_symlink()
    assert (folder / "note.txt.part").is_symlink()


@pytest.mark.parametrize(
    "relative",
    [
        ".cmu-cli/assignments.json",
        ".cmu-cli/files.json",
        ".cmu-cli/announcements.json",
        ".cmu-cli/modules.json",
        ".cmu-cli/download_manifest.json",
        ".cmu-cli/last_sync.json",
        "02_作业/Canvas作业索引.md",
        "00_课程信息/Canvas通知索引.md",
    ],
)
@pytest.mark.parametrize("dangling", [False, True])
def test_all_export_leaf_symlinks_fail_closed(tmp_path, relative, dangling):
    course = Course("TEST", 1, "Test", "Test")
    config = Config("https://example.invalid", tmp_path / "workspace", (course,))
    root = storage.ensure_layout(config, course)
    victim = tmp_path / "victim"
    if not dangling:
        victim.write_bytes(b"USER")
    target = root / relative
    target.symlink_to(victim)
    with pytest.raises((ValueError, OSError)):
        storage.sync_course(config, course, [], [], [], [], True, lambda _: b"")
    assert target.is_symlink()
    assert not victim.exists() if dangling else victim.read_bytes() == b"USER"


@pytest.mark.parametrize(
    "relative", ["01_讲义", ".cmu-cli", ".cmu-cli/versions", ".cmu-cli/versions/1"]
)
def test_directory_symlinks_cannot_redirect_writes(tmp_path, relative):
    root = tmp_path / "root"
    root.mkdir()
    manifest = {}
    if relative.startswith(".cmu-cli"):
        sync(root, manifest)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        sync(root, manifest, revision="v2", content=b"TWO")
    assert list(outside.iterdir()) == []


def test_distinct_ids_same_name_and_size_never_overwrite(tmp_path):
    manifest = {}
    first = sync(tmp_path, manifest)
    second = sync(tmp_path, manifest, 2, content=b"TWO")
    assert first["relative_path"] != second["relative_path"]
    again = sync(tmp_path, manifest)
    actual = (tmp_path / again["relative_path"]).read_bytes()
    assert actual == b"ONE"
    assert again["sha256"] == hashlib.sha256(actual).hexdigest()


def test_deduplicated_ids_split_on_divergence(tmp_path):
    manifest = {}
    first = sync(tmp_path, manifest)
    same = sync(tmp_path, manifest, 2)
    assert first["relative_path"] == same["relative_path"]
    changed = sync(tmp_path, manifest, 2, "v2", b"TWO")
    assert changed["relative_path"] != first["relative_path"]
    assert (tmp_path / first["relative_path"]).read_bytes() == b"ONE"
    assert sync(tmp_path, manifest)["sha256"] == hashlib.sha256(b"ONE").hexdigest()


@pytest.mark.parametrize("revision", ["v1", "v2"])
def test_same_size_local_edits_preserved_and_not_reported_as_remote(tmp_path, revision):
    manifest = {}
    original = sync(tmp_path, manifest)
    old = tmp_path / original["relative_path"]
    old.write_bytes(b"EDIT")
    result = sync(tmp_path, manifest, revision=revision, content=b"FOUR")
    assert old.read_bytes() == b"EDIT"
    assert result["relative_path"] != original["relative_path"]
    assert (
        result["sha256"]
        == hashlib.sha256((tmp_path / result["relative_path"]).read_bytes()).hexdigest()
    )


def test_equal_size_edit_invalidates_fast_path(tmp_path):
    manifest = {}
    original = sync(tmp_path, manifest)
    old = tmp_path / original["relative_path"]
    old.write_bytes(b"BAD")
    result = sync(tmp_path, manifest)
    assert old.read_bytes() == b"BAD"
    assert (tmp_path / result["relative_path"]).read_bytes() == b"ONE"


def test_unmanaged_file_and_occupied_conflict_preserved(tmp_path):
    folder = tmp_path / "01_讲义"
    folder.mkdir()
    for name in ["note.txt", "note__canvas_1.txt"]:
        (folder / name).write_bytes(b"USER")
    result = sync(tmp_path, {})
    assert result["relative_path"].endswith("note__canvas_1_2.txt")
    assert (folder / "note.txt").read_bytes() == b"USER"
    assert (folder / "note__canvas_1.txt").read_bytes() == b"USER"


def test_archives_unique_even_with_repeated_revision_and_legacy_link(tmp_path):
    manifest = {}
    sync(tmp_path, manifest)
    archive_dir = tmp_path / ".cmu-cli/versions/1"
    archive_dir.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_bytes(b"USER")
    (archive_dir / "v2_note.txt").symlink_to(victim)
    sync(tmp_path, manifest, revision="v2", content=b"TWO")
    sync(tmp_path, manifest, revision="v3", content=b"TRE")
    sync(tmp_path, manifest, revision="v2", content=b"FOU")
    archives = [p for p in archive_dir.iterdir() if not p.is_symlink()]
    assert len(archives) == 3
    assert {p.read_bytes() for p in archives} == {b"ONE", b"TWO", b"TRE"}
    assert victim.read_bytes() == b"USER"


def test_atomic_failure_keeps_original_and_cleans_staging(tmp_path, monkeypatch):
    target = tmp_path / "data.json"
    target.write_bytes(b"USER")

    def fail(source, destination):
        destination.write(b"partial")
        raise OSError("disk failure")

    monkeypatch.setattr(storage.shutil, "copyfileobj", fail)
    with pytest.raises(OSError):
        storage.write_json(target, [])
    assert target.read_bytes() == b"USER"
    assert list(tmp_path.iterdir()) == [target]


def test_private_modes_and_hardlink_isolation(tmp_path):
    victim = tmp_path / "victim"
    victim.write_bytes(b"USER")
    target = tmp_path / "private/data.json"
    target.parent.mkdir()
    os.link(victim, target)
    storage.write_json(target, [])
    assert victim.read_bytes() == b"USER"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    created = tmp_path / "new/sub/data.json"
    storage.write_json(created, {})
    assert stat.S_IMODE(created.parent.stat().st_mode) == 0o700


def test_standalone_download_checks_bytes_and_preserves_user_file(tmp_path):
    target = tmp_path / "note.txt"
    target.write_bytes(b"BAD")
    with pytest.raises(FileExistsError):
        storage.download_file(item(), target, lambda _: b"ONE")
    assert target.read_bytes() == b"BAD"


def test_corrupt_manifest_not_silently_discarded(tmp_path):
    target = tmp_path / "manifest.json"
    target.write_text("{broken")
    with pytest.raises(json.JSONDecodeError):
        storage.load_json_object(target)


def test_missing_file_path_remains_owned(tmp_path):
    manifest = {}
    first = sync(tmp_path, manifest)
    (tmp_path / first["relative_path"]).unlink()
    second = sync(tmp_path, manifest, file_id=2, content=b"TWO")
    assert second["relative_path"] != first["relative_path"]


@pytest.mark.parametrize(
    "path", [".cmu-cli/assignments.json", "00_课程信息/Canvas通知索引.md"]
)
def test_manifest_cannot_claim_internal_exports(tmp_path, path):
    with pytest.raises(ValueError):
        storage.sync_canvas_file(
            item(), tmp_path, {"1": {"relative_path": path}}, lambda _: b"ONE"
        )


def test_atomic_publish_does_not_follow_last_instant_leaf_link(tmp_path, monkeypatch):
    target = tmp_path / "target"
    victim = tmp_path / "victim"
    victim.write_bytes(b"USER")
    replace = storage.os.replace

    def swap_then_replace(source, destination, **kwargs):
        target.symlink_to(victim)
        return replace(source, destination, **kwargs)

    monkeypatch.setattr(storage.os, "replace", swap_then_replace)
    storage.atomic_write(target, b"NEW")
    assert target.read_bytes() == b"NEW"
    assert not target.is_symlink()
    assert victim.read_bytes() == b"USER"


def test_exclusive_publish_never_replaces_existing_archive(tmp_path):
    target = tmp_path / "archive"
    target.write_bytes(b"OLD")
    with pytest.raises(FileExistsError):
        storage.atomic_write(target, b"NEW", exclusive=True)
    assert target.read_bytes() == b"OLD"
    assert list(tmp_path.iterdir()) == [target]


def test_unknown_source_revision_is_not_cached_forever(tmp_path):
    manifest = {}
    sync(tmp_path, manifest, revision=None)
    result = sync(tmp_path, manifest, revision=None, content=b"TWO")
    assert (tmp_path / result["relative_path"]).read_bytes() == b"TWO"


def test_long_unicode_names_support_updates_and_archives(tmp_path):
    manifest = {}
    name = "讲义" * 150 + ".txt"
    first = sync(tmp_path, manifest, name=name)
    updated = sync(tmp_path, manifest, revision="v2", content=b"TWO", name=name)
    assert first["relative_path"] == updated["relative_path"]
    assert updated["status"] == "updated"


def test_standalone_parent_symlink_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "linked"
    parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        storage.download_file(item(), parent / "note.txt", lambda _: b"ONE")
    assert list(outside.iterdir()) == []


def test_no_stable_id_fails_before_fetch(tmp_path):
    with pytest.raises(ValueError, match="stable file ID"):
        storage.sync_canvas_file(
            {"display_name": "note.txt"}, tmp_path, {}, lambda _: pytest.fail("fetch")
        )
