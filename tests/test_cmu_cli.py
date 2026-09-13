from cmu_cli.models import Config, Course
from cmu_cli.storage import (
    canvas_material_folder,
    clean_html,
    ensure_layout,
    format_time,
    normalized_canvas_filename,
    safe_filename,
    sync_canvas_file,
)


def test_safe_filename_and_html_cleanup():
    assert safe_filename("HW: 1/2?.pdf") == "HW_ 1_2_.pdf"
    assert clean_html("<p>Hello&nbsp;<b>world</b></p>") == "Hello world"


def test_canvas_time_is_rendered_in_pittsburgh_timezone():
    assert format_time("2026-08-25T18:00:00Z") == "2026-08-25 Tue 14:00 EDT"


def test_layout_has_one_flat_lecture_folder(tmp_path):
    course = Course(code="X", canvas_id=1, name="Course", directory="Course")
    config = Config(
        canvas_base_url="https://example.test", storage_root=tmp_path, courses=(course,)
    )
    root = ensure_layout(config, course)
    assert (root / "01_讲义").is_dir()
    assert (root / "03_Recitations").is_dir()
    assert not (root / "01_讲义/Canvas同步").exists()


def test_canvas_material_routing_and_names(tmp_path):
    assert canvas_material_folder(tmp_path, "calendar.pdf") == tmp_path / "00_课程信息"
    assert (
        canvas_material_folder(tmp_path, "Multimodal Dataset List 2026.pdf")
        == tmp_path / "00_课程信息"
    )
    assert (
        canvas_material_folder(tmp_path, "OLD - Dataset List.pdf")
        == tmp_path / "legacy/课程资料"
    )
    assert (
        canvas_material_folder(tmp_path, "recitation_1.pdf")
        == tmp_path / "03_Recitations"
    )
    assert (
        canvas_material_folder(tmp_path, "hw2.zip")
        == tmp_path / "02_作业/Assignment_02"
    )
    assert canvas_material_folder(tmp_path, "lec4_2026.pdf") == tmp_path / "01_讲义"
    assert normalized_canvas_filename("lec4_2026.pdf") == "Lecture_04.pdf"


def test_canvas_download_deduplicates_and_tracks_canonical_path(tmp_path):
    root = tmp_path
    lecture = root / "01_讲义"
    lecture.mkdir()
    canonical = lecture / "Lecture_01_Introduction.pdf"
    canonical.write_bytes(b"same-pdf")
    manifest = {}
    item = {
        "id": 7,
        "display_name": "lec1_2026.pdf",
        "size": len(b"same-pdf"),
        "updated_at": "2026-08-24T15:02:44Z",
        "url": "https://example.test/lec1.pdf",
    }
    result = sync_canvas_file(item, root, manifest, lambda _: b"same-pdf")
    assert result["status"] == "deduplicated"
    assert result["relative_path"] == "01_讲义/Lecture_01_Introduction.pdf"
    assert list(lecture.iterdir()) == [canonical]

    # An unchanged source revision uses the manifest and does not fetch again.
    result = sync_canvas_file(
        item, root, manifest, lambda _: (_ for _ in ()).throw(AssertionError("fetched"))
    )
    assert result["status"] == "unchanged"

    # A changed Canvas revision updates the canonical path and archives the old bytes.
    updated = {**item, "updated_at": "2026-08-25T15:02:44Z", "size": len(b"new-pdf")}
    result = sync_canvas_file(updated, root, manifest, lambda _: b"new-pdf")
    assert result["status"] == "updated"
    assert canonical.read_bytes() == b"new-pdf"
    versions = list((root / ".cmucw/versions/7").iterdir())
    assert len(versions) == 1
    assert versions[0].read_bytes() == b"same-pdf"
