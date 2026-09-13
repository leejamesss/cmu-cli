"""Module metadata only: never follow must-view HTML URLs."""

from unittest.mock import Mock

import pytest
import requests

from cmu_cli.canvas_client import CanvasClient, CanvasError


def test_missing_inline_items_dedup_files(monkeypatch):
    client = CanvasClient("https://canvas.example.edu", session=requests.Session())
    get = Mock(
        side_effect=[
            [{"id": 1, "items_count": 2}, {"id": 2, "items_count": 1}],
            [
                {"id": 11, "type": "File", "content_id": 9},
                {
                    "id": 12,
                    "type": "ExternalUrl",
                    "html_url": "https://evil.example/view",
                },
            ],
            [{"id": 21, "type": "File", "content_id": 9}],
            {"id": 9, "filename": "slides.pdf", "locked_for_user": True},
        ]
    )
    monkeypatch.setattr(client, "get", get)
    rows = client.module_files(3)
    assert len(rows) == 1
    assert rows[0]["module_ids"] == [1, 2]
    assert rows[0]["locked_for_user"] is True
    assert [call.args[0] for call in get.call_args_list] == [
        "/api/v1/courses/3/modules",
        "/api/v1/courses/3/modules/1/items",
        "/api/v1/courses/3/modules/2/items",
        "/api/v1/courses/3/files/9",
    ]


@pytest.mark.parametrize("count", [2, True, -1])
def test_inconsistent_item_counts(monkeypatch, count):
    client = CanvasClient("https://canvas.example.edu", session=requests.Session())
    monkeypatch.setattr(
        client,
        "get",
        Mock(side_effect=[[{"id": 1, "items_count": count}], [{"id": 11}]]),
    )
    with pytest.raises(CanvasError):
        client.modules(3)


def test_duplicate_module_and_items(monkeypatch):
    client = CanvasClient("https://canvas.example.edu", session=requests.Session())
    module = {"id": 1, "items_count": 1, "items": [{"id": 11}]}
    monkeypatch.setattr(client, "get", Mock(return_value=[module, module.copy()]))
    assert len(client.modules(3)) == 1


@pytest.mark.parametrize("identity", ["../9", True, "https://evil.example/9"])
def test_file_id_boundary(monkeypatch, identity):
    client = CanvasClient("https://canvas.example.edu", session=requests.Session())
    monkeypatch.setattr(
        client,
        "modules",
        lambda _: [{"id": 1, "items": [{"type": "File", "content_id": identity}]}],
    )
    monkeypatch.setattr(client, "get", Mock())
    with pytest.raises(CanvasError):
        client.module_files(3)
    client.get.assert_not_called()
