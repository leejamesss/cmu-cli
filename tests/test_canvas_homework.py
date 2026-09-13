import hashlib

import pytest

from cmu_cli.storage import canvas_material_folder, sync_canvas_file


@pytest.mark.parametrize(
    "name,number",
    [
        ("hw2_2026.pdf", 2),
        ("DEMO_101_HW1_solutions.pdf", 1),
        ("homework-02", 2),
        ("Assignment 2", 2),
        ("problem set 3.pdf", 3),
        ("problem_set_04_solutions.pdf", 4),
        ("pset5.zip", 5),
        ("HW0.pdf", 0),
    ],
)
def test_numbered_homework_folder(tmp_path, name, number):
    assert (
        canvas_material_folder(tmp_path, name)
        == tmp_path / f"02_作业/Assignment_{number:02d}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "homework_2026.pdf",
        "Assignment.pdf",
        "hw.pdf",
        "pset.pdf",
        "HW1_HW2.pdf",
        "Assignment 1 and Assignment 2.pdf",
        "homework-1-2.pdf",
        "HW_1_2.pdf",
        "Assignment 2 and 3.pdf",
        "problem sets 1-3.pdf",
        "hw2a.pdf",
        "homework solutions 2026.pdf",
        "Assignment 123.pdf",
        "HW2_pset3.pdf",
    ],
)
def test_ambiguous_homework_stays_generic(tmp_path, name):
    assert canvas_material_folder(tmp_path, name) == tmp_path / "02_作业/Canvas资料"


def item(name="hw2_2026.pdf"):
    return {
        "id": 42,
        "display_name": name,
        "size": 8,
        "updated_at": "revision-1",
        "url": "https://example.invalid/file",
    }


def no_fetch(_) -> bytes:
    raise AssertionError("unchanged migration must not fetch")


def seed(root, relative="02_作业/Canvas资料/hw2_2026.pdf", name="hw2_2026.pdf"):
    old = root / relative
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"original")
    manifest = {
        "42": {
            "source_name": name,
            "relative_path": relative,
            "size": 8,
            "source_updated_at": "revision-1",
            "sha256": hashlib.sha256(b"original").hexdigest(),
        }
    }
    return old, manifest


def test_new_download_and_next_sync(tmp_path):
    manifest = {}
    result = sync_canvas_file(item(), tmp_path, manifest, lambda _: b"original")
    assert result["status"] == "downloaded"
    assert result["relative_path"] == "02_作业/Assignment_02/hw2_2026.pdf"
    assert (tmp_path / result["relative_path"]).read_bytes() == b"original"
    assert (
        sync_canvas_file(item(), tmp_path, manifest, no_fetch)["status"] == "unchanged"
    )


@pytest.mark.parametrize("target_bytes", [None, b"original", b"user-data"])
def test_existing_manifest_migrates_without_fetch_and_is_idempotent(
    tmp_path, target_bytes
):
    old, manifest = seed(tmp_path)
    target = tmp_path / "02_作业/Assignment_02/hw2_2026.pdf"
    if target_bytes is not None:
        target.parent.mkdir(parents=True)
        target.write_bytes(target_bytes)
    result = sync_canvas_file(item(), tmp_path, manifest, no_fetch)
    canonical = tmp_path / result["relative_path"]
    assert canonical.parent == target.parent
    assert canonical.read_bytes() == b"original"
    assert not old.exists()
    assert manifest["42"]["relative_path"] == result["relative_path"]
    assert manifest["42"]["sha256"] == hashlib.sha256(b"original").hexdigest()
    if target_bytes is not None:
        assert target.read_bytes() == target_bytes
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    again = sync_canvas_file(item(), tmp_path, manifest, no_fetch)
    assert again["status"] == "unchanged"
    assert again["relative_path"] == result["relative_path"]
    assert before == {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize(
    "relative",
    [
        "02_作业/Assignment_02/My custom name.pdf",
        "02_作业/Canvas资料/custom/hw2_2026.pdf",
        "02_作业/Canvas资料_backup/hw2_2026.pdf",
        "my-notes/hw2_2026.pdf",
    ],
)
def test_custom_manifest_path_is_preserved(tmp_path, relative):
    old, manifest = seed(tmp_path, relative)
    result = sync_canvas_file(item(), tmp_path, manifest, no_fetch)
    assert result["relative_path"] == relative
    assert old.read_bytes() == b"original"


def test_ambiguous_manifest_not_migrated(tmp_path):
    name = "HW1_HW2.pdf"
    relative = "02_作业/Canvas资料/" + name
    old, manifest = seed(tmp_path, relative, name)
    assert (
        sync_canvas_file(item(name), tmp_path, manifest, no_fetch)["relative_path"]
        == relative
    )
    assert old.read_bytes() == b"original"


def test_migration_preserves_renamed_canonical_basename(tmp_path):
    old, manifest = seed(tmp_path, "02_作业/Canvas资料/My problems.pdf")
    result = sync_canvas_file(item(), tmp_path, manifest, no_fetch)
    assert result["relative_path"] == "02_作业/Assignment_02/My problems.pdf"
    assert not old.exists()


def test_occupied_conflict_name_is_never_overwritten(tmp_path):
    old, manifest = seed(tmp_path)
    folder = tmp_path / "02_作业/Assignment_02"
    folder.mkdir(parents=True)
    (folder / old.name).write_bytes(b"user-1")
    (folder / "hw2_2026__canvas_42.pdf").write_bytes(b"user-2")
    result = sync_canvas_file(item(), tmp_path, manifest, no_fetch)
    assert (tmp_path / result["relative_path"]).read_bytes() == b"original"
    assert (folder / old.name).read_bytes() == b"user-1"
    assert (folder / "hw2_2026__canvas_42.pdf").read_bytes() == b"user-2"


@pytest.mark.parametrize("existing", ["HW1", "hw1", "Assignment_01", "HW01"])
def test_reuses_unique_numbered_folder(tmp_path, existing):
    folder = tmp_path / "02_作业" / existing
    folder.mkdir(parents=True)
    assert canvas_material_folder(tmp_path, "DEMO_101_HW1_solutions.pdf") == folder


@pytest.mark.parametrize(
    "existing,expected",
    [
        (["HW1"], "HW2"),
        (["hw1"], "HW2"),
        (["Assignment_01"], "Assignment_02"),
        (["HW1", "Assignment_03"], "Canvas资料"),
        (["HW2", "Assignment_02"], "Canvas资料"),
        (["HW1", "Assignment_02"], "Assignment_02"),
    ],
)
def test_existing_course_family_and_ambiguity(tmp_path, existing, expected):
    for name in existing:
        (tmp_path / "02_作业" / name).mkdir(parents=True)
    assert (
        canvas_material_folder(tmp_path, "hw2_2026.pdf")
        == tmp_path / "02_作业" / expected
    )


@pytest.mark.parametrize("migrate", [True, False])
def test_sync_uses_hw_course_convention(tmp_path, migrate):
    (tmp_path / "02_作业/HW1").mkdir(parents=True)
    if migrate:
        _old, manifest = seed(tmp_path)
        fetch = no_fetch
    else:
        manifest = {}

        def fetch(_):
            return b"original"

    result = sync_canvas_file(item(), tmp_path, manifest, fetch)
    assert result["relative_path"] == "02_作业/HW2/hw2_2026.pdf"
    assert (
        sync_canvas_file(item(), tmp_path, manifest, no_fetch)["relative_path"]
        == result["relative_path"]
    )
    assert not (tmp_path / "02_作业/Assignment_02").exists()


def test_ambiguous_existing_folders_prevent_migration(tmp_path):
    for name in ["HW2", "Assignment_02"]:
        (tmp_path / "02_作业" / name).mkdir(parents=True)
    old, manifest = seed(tmp_path)
    result = sync_canvas_file(item(), tmp_path, manifest, no_fetch)
    assert result["relative_path"] == str(old.relative_to(tmp_path))
    assert old.read_bytes() == b"original"


def test_missing_old_file_downloads_into_numbered_folder_without_clobber(tmp_path):
    old, manifest = seed(tmp_path)
    old.unlink()
    target = tmp_path / "02_作业/Assignment_02" / old.name
    target.parent.mkdir(parents=True)
    target.write_bytes(b"user-file")
    result = sync_canvas_file(item(), tmp_path, manifest, lambda _: b"original")
    assert (tmp_path / result["relative_path"]).parent == target.parent
    assert (tmp_path / result["relative_path"]).read_bytes() == b"original"
    assert target.read_bytes() == b"user-file"


@pytest.mark.parametrize("dangling", [False, True])
def test_migration_does_not_overwrite_target_symlink(tmp_path, dangling):
    old, manifest = seed(tmp_path)
    target = tmp_path / "02_作业/Assignment_02" / old.name
    target.parent.mkdir(parents=True)
    user_file = tmp_path / "user-file.pdf"
    if not dangling:
        user_file.write_bytes(b"original")
    target.symlink_to(user_file)
    result = sync_canvas_file(item(), tmp_path, manifest, no_fetch)
    assert target.is_symlink()
    assert (tmp_path / result["relative_path"]).read_bytes() == b"original"
    assert tmp_path / result["relative_path"] != target
    assert not old.exists()


def test_course_sync_persists_migrated_manifest(tmp_path):
    import json

    from cmu_cli.models import Config, Course
    from cmu_cli.storage import ensure_layout, sync_course, write_json

    course = Course(code="TEST", canvas_id=1, name="Test", directory="Test")
    config = Config(
        canvas_base_url="https://example.invalid",
        storage_root=tmp_path,
        courses=(course,),
    )
    root = ensure_layout(config, course)
    old, manifest = seed(root)
    path = root / ".cmu-cli/download_manifest.json"
    write_json(path, manifest)
    for _ in range(2):
        sync_course(config, course, [], [item()], [], [], True, no_fetch)
        saved = json.loads(path.read_text())
        assert saved["42"]["relative_path"] == "02_作业/Assignment_02/hw2_2026.pdf"
        assert (root / saved["42"]["relative_path"]).read_bytes() == b"original"
    assert not old.exists()


def test_migrated_changed_revision_archives_old_bytes_and_keeps_conflict(tmp_path):
    old, manifest = seed(tmp_path)
    target = tmp_path / "02_作业/Assignment_02" / old.name
    target.parent.mkdir(parents=True)
    target.write_bytes(b"user-file")
    revised = {**item(), "updated_at": "revision-2"}
    result = sync_canvas_file(revised, tmp_path, manifest, lambda _: b"revision")
    assert result["status"] == "updated"
    assert (tmp_path / result["relative_path"]).read_bytes() == b"revision"
    assert target.read_bytes() == b"user-file"
    versions = list((tmp_path / ".cmu-cli/versions/42").iterdir())
    assert len(versions) == 1 and versions[0].read_bytes() == b"original"
    assert (
        sync_canvas_file(revised, tmp_path, manifest, no_fetch)["status"] == "unchanged"
    )


def test_migration_copy_failure_keeps_original_and_manifest(tmp_path, monkeypatch):
    import copy

    from cmu_cli import storage

    old, manifest = seed(tmp_path)
    before = copy.deepcopy(manifest)

    def fail_copy(source, target):
        target.write(b"partial")
        raise OSError("simulated disk failure")

    monkeypatch.setattr(storage.shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError, match="simulated disk failure"):
        sync_canvas_file(item(), tmp_path, manifest, no_fetch)
    assert old.read_bytes() == b"original"
    assert manifest == before
    assert list((tmp_path / "02_作业/Assignment_02").iterdir()) == []


def test_unmanaged_legacy_file_is_not_moved(tmp_path):
    old, _ = seed(tmp_path)
    manifest = {}
    result = sync_canvas_file(item(), tmp_path, manifest, lambda _: b"original")
    assert old.read_bytes() == b"original"
    assert result["relative_path"] == "02_作业/Assignment_02/hw2_2026.pdf"
