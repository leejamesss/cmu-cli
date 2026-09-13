"""A downloaded material must be listable wherever ``sync`` filed it.

``canvas_material_folder`` routes a Canvas download into one of six categories.
``materials`` used to scan two of them, so a synced syllabus, course calendar or
homework attachment was written to disk and then never listed again. These tests
derive the destinations from the routing function itself, so a seventh category
cannot be added without the listing following it.
"""

from pathlib import Path

from cmu_cli.storage import canvas_material_folder, local_material_paths

# One filename per routing branch, chosen so each lands in a different folder.
ROUTED_FILENAMES = [
    "OLD - Dataset List 2023.pdf",  # legacy
    "syllabus.pdf",  # course information
    "recitation01.pdf",  # recitations
    "hw2-handout.pdf",  # numbered homework
    "homework-guidelines.pdf",  # unnumbered homework
    "lecture1.pdf",  # lectures (default)
]


def _fill(root: Path, names=ROUTED_FILENAMES) -> list[Path]:
    written = []
    for name in names:
        folder = canvas_material_folder(root, name)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(b"%PDF-1.4 synthetic\n")
        written.append(path)
    return written


def test_every_routing_destination_is_distinct(tmp_path):
    """Guards the premise: these filenames must exercise six different folders."""
    folders = {canvas_material_folder(tmp_path, name) for name in ROUTED_FILENAMES}
    assert len(folders) == len(ROUTED_FILENAMES)


def test_every_routed_download_is_listed(tmp_path):
    written = _fill(tmp_path)
    assert sorted(local_material_paths(tmp_path)) == sorted(written)


def test_generated_indexes_are_not_materials(tmp_path):
    """The Markdown indexes sync writes are navigation, not course materials."""
    _fill(tmp_path)
    (tmp_path / "02_作业").mkdir(parents=True, exist_ok=True)
    (tmp_path / "02_作业" / "Canvas作业索引.md").write_text("# index", encoding="utf-8")
    assert not [p for p in local_material_paths(tmp_path) if p.suffix == ".md"]


def test_retained_earlier_revisions_are_not_listed_twice(tmp_path):
    """The metadata cache keeps prior revisions under ordinary downloadable names."""
    written = _fill(tmp_path, names=["lecture1.pdf"])
    versions = tmp_path / ".cmucw" / "versions" / "1234"
    versions.mkdir(parents=True)
    (versions / "lecture1.pdf").write_bytes(b"%PDF-1.4 older\n")
    assert local_material_paths(tmp_path) == written


def test_missing_course_root_is_empty_not_an_error(tmp_path):
    assert local_material_paths(tmp_path / "never-synced") == []
