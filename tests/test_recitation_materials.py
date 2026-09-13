from cmu_cli.official_materials import public_materials
from cmu_cli.storage import canvas_material_folder, sync_canvas_file
from tests.test_official_materials import FakeSession


def test_all_recitation_columns_and_variants(monkeypatch, tmp_path):
    html = """<table><tr><td>Sep 4</td><td>Synthetic topic</td>
    <td><a href="recs/rec1.pdf">Recitation 1</a><a href="recs/rec1_slides.pdf">slides</a></td>
    <td><a href="recs/rec1_sol.pdf">Solutions</a></td></tr>
    <tr><td>Sep 18</td><td>Next</td><td>Not released</td><td></td></tr></table>"""
    monkeypatch.setattr(
        "cmu_cli.official_materials.public_document", lambda *a, **k: html.encode()
    )
    monkeypatch.setattr(
        "cmu_cli.official_materials.safe_request",
        lambda session, url, **kw: FakeSession().head(url, 30, False),
    )
    rows = public_materials("DEMO-101", "https://courses.example.invalid/")
    assert {r["display_name"] for r in rows} == {
        "Recitation_01_Handout.pdf",
        "Recitation_01_Slides.pdf",
        "Recitation_01_Solutions.pdf",
    }
    for row in rows:
        assert (
            canvas_material_folder(tmp_path, row["display_name"])
            == tmp_path / "03_Recitations"
        )


def test_recitation_sync_unchanged_and_revision(tmp_path):
    item = {
        "id": "course-site:https://courses.example.invalid/recs/rec1.pdf",
        "display_name": "Recitation_01_Handout.pdf",
        "url": "https://courses.example.invalid/recs/rec1.pdf",
        "updated_at": "v1",
        "size": 3,
    }
    manifest = {}
    assert (
        sync_canvas_file(item, tmp_path, manifest, lambda u: b"one")["status"]
        == "downloaded"
    )

    def no_fetch(url):
        raise AssertionError("unchanged files must not download again")

    assert sync_canvas_file(item, tmp_path, manifest, no_fetch)["status"] == "unchanged"
    assert (
        sync_canvas_file(
            {**item, "updated_at": "v2"}, tmp_path, manifest, lambda u: b"two"
        )["status"]
        == "updated"
    )
    assert (
        tmp_path / "03_Recitations/Recitation_01_Handout.pdf"
    ).read_bytes() == b"two"
    archived = [p for p in (tmp_path / ".cmucw/versions").rglob("*.pdf") if p.is_file()]
    assert len(archived) == 1
    assert archived[0].read_bytes() == b"one"
