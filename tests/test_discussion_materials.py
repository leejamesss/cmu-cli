import json
from types import SimpleNamespace

import pytest
import requests

from cmu_cli import discussion_materials as dm
from cmu_cli.ed_client import EdClient
from cmu_cli.web_session import SessionError


class Response:
    def __init__(self, body=b"%PDF-fixture", status=200, headers=None):
        self.body, self.status_code = body, status
        self.headers = headers or {"Content-Type": "application/pdf"}
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class Session(requests.Session):
    def __init__(self, responses):
        super().__init__()
        self.responses, self.calls = list(responses), []
        self.cookies = requests.cookies.RequestsCookieJar()
        self.headers = {}
        self.auth = None

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs, dict(self.headers), self.cookies.get_dict()))
        return self.responses.pop(0)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def extract(body, **kwargs):
    return dm.extract_attachments(
        "ed",
        "10",
        "20",
        body,
        source_url="https://us.edstem.org/api/threads/20",
        **kwargs,
    )


def attachment(name="x.pdf"):
    return extract(
        f'<file src="https://static.us.edusercontent.com/files/a" name="{name}"/>'
    )[0]


def test_file_link_external_and_dedup():
    rows = extract(
        '<a href="https://outside.example/paper.pdf">external</a>'
        '<a href="/files/a.pdf">pdf</a><a href="/files/a.pdf">again</a>'
        '<a href="/discussion">page</a><file src="https://static.us.edusercontent.com/files/opaque"/>'
    )
    assert len(rows) == 4
    assert [row["kind"] for row in rows] == ["link", "attachment", "link", "attachment"]
    assert rows[1]["url"] == "https://us.edstem.org/files/a.pdf"
    assert rows[0]["download_url"] is None


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://us.edstem.org/a.pdf",
        "https://user:password@us.edstem.org/a.pdf",
        "https://us.edstem.org\\evil/a.pdf",
    ],
)
def test_unsafe_references(url):
    assert extract(f'<a href="{url}">x</a>') == []


def test_exact_origin_and_external_explicit():
    rows = extract('<file src="https://us.edstem.org.evil.test/a.pdf"/>')
    assert rows[0]["download_url"] is None
    with pytest.raises(ValueError):
        dm.download_attachment(rows[0], "/unused")


def test_stable_identity_and_scoped_provenance():
    a, b = (
        extract('<file src="/a.pdf"/>'),
        extract('<file src="/a.pdf"/>', parent_id="2"),
    )
    merged = {}
    dm._combine(merged, a)
    dm._combine(merged, b)
    assert len(merged) == 1
    assert len(next(iter(merged.values()))["provenance"]) == 2
    other = dm.extract_attachments(
        "ed",
        "11",
        "20",
        '<file src="/a.pdf"/>',
        source_url="https://us.edstem.org/api/threads/20",
    )
    assert other[0]["id"] != a[0]["id"]


def json_response(payload, status=200):
    return Response(
        json.dumps(payload).encode(), status, {"Content-Type": "application/json"}
    )


def thread(tid=20):
    return {
        "id": tid,
        "course_id": 10,
        "answers": [],
        "comments": [],
        "reply_count": 0,
        "content": '<file src="/a.pdf"/>',
    }


def test_ed_actual_client_nested():
    row = thread()
    row["comments"] = [
        {
            "id": 30,
            "thread_id": 20,
            "comments": [],
            "content": '<image src="/image.png"/>',
        }
    ]
    row["reply_count"] = 1
    session = Session(
        [
            json_response({"threads": [thread()]}),
            json_response({"threads": []}),
            json_response({"thread": row}),
        ]
    )
    result = dm.ed_materials(EdClient(session=session), "10")
    assert result["complete"] and result["fetched_count"] == 2
    assert len(session.calls) == 3
    assert all(call[1]["allow_redirects"] is False for call in session.calls)


def test_ed_repeated_pages_retains_prefix():
    session = Session(
        [
            json_response({"threads": [thread()]}),
            json_response({"threads": [thread()]}),
            json_response({"thread": thread()}),
        ]
    )
    result = dm.ed_materials(EdClient(session=session), "10")
    assert not result["complete"]
    assert len(result["items"]) == 1


@pytest.mark.parametrize("status", [401, 403, 404])
def test_ed_denial_partial(status):
    session = Session(
        [
            json_response({"threads": [thread(), thread(21)]}),
            json_response({"threads": []}),
            json_response({"thread": thread()}),
            json_response({}, status),
        ]
    )
    result = dm.ed_materials(EdClient(session=session), 10)
    assert not result["complete"] and len(result["items"]) == 1
    assert result["issues"][-1]["post_id"] == "21"


def test_ed_missing_count():
    row = thread()
    del row["reply_count"]
    client = SimpleNamespace(
        base_url="https://us.edstem.org/api",
        threads=lambda *a, **k: {"items": [row], "complete": True},
        thread=lambda *a, **k: row,
    )
    assert not dm.ed_materials(client, 10)["complete"]


def test_piazza_supplied_body_not_snippet():
    post = {
        "id": "p1",
        "nid": "n1",
        "content_snipet": '<file src="/ignored.pdf"/>',
        "history": [{"content": '<a href="/a.pdf">file</a>'}],
        "children": [{"id": "c1", "subject": '<img src="/i.png"/>'}],
    }
    result = dm.piazza_post_materials(post, network_id="n1", post_id="p1")
    assert not result["complete"] and result["parsed_supplied_bodies"]
    assert len(result["items"]) == 2
    with pytest.raises(ValueError):
        dm.piazza_post_materials(post, network_id="other", post_id="p1")


def test_piazza_malformed_partial():
    post = {"id": "p", "content": '<file src="/x.pdf"/>', "children": [None]}
    result = dm.piazza_post_materials(post, network_id="n", post_id="p")
    assert len(result["items"]) == 1 and result["issues"]


def test_storage_collision_traversal_idempotence_and_edits(tmp_path, monkeypatch):
    sessions = []

    def factory():
        session = Session([Response()])
        sessions.append(session)
        return session

    monkeypatch.setattr(dm, "configured_session", factory)
    item = attachment("../../bad.pdf")
    first = dm.download_attachment(item, tmp_path)
    assert first["status"] == "downloaded"
    assert dm.download_attachment(item, tmp_path)["status"] == "unchanged"
    destination = tmp_path / first["relative_path"]
    assert destination.is_relative_to(tmp_path)
    other = dict(item, id=item["id"] + "different")
    assert (
        dm.download_attachment(other, tmp_path)["relative_path"]
        != first["relative_path"]
    )
    destination.write_bytes(b"local edit")
    with pytest.raises(FileExistsError):
        dm.download_attachment(item, tmp_path)
    assert destination.read_bytes() == b"local edit"
    assert all(
        s.calls[0][2] == {"User-Agent": "cmu_cli/0.1"} and not s.calls[0][3]
        for s in sessions
    )


@pytest.mark.parametrize(
    "response",
    [
        Response(status=403),
        Response(
            b"<html>login</html>", headers={"Content-Type": "application/octet-stream"}
        ),
        Response(b"wrong"),
        Response(headers={"Content-Type": "text/html"}),
        Response(headers={"Content-Length": "1000"}),
    ],
)
def test_bad_downloads(tmp_path, monkeypatch, response):
    session = Session([response])
    monkeypatch.setattr(dm, "configured_session", lambda: session)
    with pytest.raises(SessionError):
        dm.download_attachment(attachment(), tmp_path, max_bytes=50)
    assert response.closed
    assert not list(tmp_path.rglob("*.pdf"))


def test_redirect_never_leaks_and_size_stream(tmp_path, monkeypatch):
    session = Session(
        [Response(status=302, headers={"Location": "https://127.0.0.1/a.pdf"})]
    )
    monkeypatch.setattr(dm, "configured_session", lambda: session)
    with pytest.raises(SessionError):
        dm.download_attachment(attachment(), tmp_path)
    assert len(session.calls) == 1
    session.responses = [Response()]
    with pytest.raises(SessionError):
        dm.download_attachment(attachment(), tmp_path, max_bytes=2)


def test_symlink_blocked(tmp_path, monkeypatch):
    (tmp_path / "ed").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(dm, "configured_session", lambda: pytest.fail("must not fetch"))
    with pytest.raises(ValueError):
        dm.download_attachment(attachment(), tmp_path)
