"""Synthetic public CLI dispatch, auth, completeness and download regressions."""

import json
import sys

import pytest
import requests

from cmu_cli import cli, ed_client
from cmu_cli import discussion_materials as dm
from cmu_cli import gradescope_commands as gc
from cmu_cli.public_transport import PublicHTTPSAdapter
from tests.test_discussion_materials import (
    Response,
    Session,
    json_response,
    thread,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(
        requests.Session, "send", lambda *a, **k: pytest.fail("Unexpected network")
    )
    monkeypatch.setattr(
        cli,
        "CanvasClient",
        lambda *a, **k: pytest.fail("Canvas auth must not be initialized"),
    )
    monkeypatch.setenv("CMU_CLI_ED_TOKEN", "synthetic-token")
    monkeypatch.delenv("CMU_CLI_CONFIG", raising=False)


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "canvas_base_url": "https://canvas.example.invalid",
                "storage_root": str(tmp_path / "storage"),
                "courses": [
                    {
                        "code": "DEMO-101",
                        "canvas_id": 2,
                        "name": "Demo",
                        "directory": "demo",
                        "gradescope_url": "https://www.gradescope.com/courses/10",
                    }
                ],
            }
        )
    )
    return path


def run(monkeypatch, capsys, config, *args):
    monkeypatch.setattr(sys, "argv", ["cmu-cli", "--config", str(config), *args])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    output = capsys.readouterr()
    return exc.value.code, output.out, output.err


def html(status="0 / 10", links=""):
    return (
        '<table id="assignments-student-table"><tbody><tr><th scope="row" data-assignment-id="20">Quiz</th>'
        f'<td class="submissionStatus--text">{status}</td><td>{links}</td></tr></tbody></table>'
    ).encode()


@pytest.mark.parametrize(
    "status,score,maximum,release",
    [
        ("0 / 10", 0, 10, "released"),
        ("Submitted", None, None, "not_released"),
        ("A", None, None, "unknown"),
        ("Not Submitted", None, None, "unknown"),
    ],
)
@pytest.mark.parametrize("mode", ["plain", "json"])
def test_gradescope_cli(
    monkeypatch, capsys, config, status, score, maximum, release, mode
):
    session = Session([Response(html(status), headers={"Content-Type": "text/html"})])
    monkeypatch.setattr(gc, "browser_cookie_session", lambda *a: session)
    code, out, err = run(
        monkeypatch,
        capsys,
        config,
        "grades",
        "--source",
        "gradescope",
        *(["--json"] if mode == "json" else []),
    )
    assert code == 0 and not err
    assert (
        len(session.calls) == 1
        and session.calls[0][0] == "https://www.gradescope.com/courses/10"
    )
    if mode == "json":
        row = json.loads(out)["data"]["assignments"][0]
        assert (row["score"], row["points_possible"], row["release_state"]) == (
            score,
            maximum,
            release,
        )
    else:
        assert "Quiz" in out and release in out


@pytest.mark.parametrize("mode", ["plain", "json"])
def test_gradescope_details_observed_partial(monkeypatch, capsys, config, mode):
    url = "https://www.gradescope.com/courses/10/assignments/20/submissions/30"
    links = f'<a href="{url}">view</a><a href="{url}">duplicate</a><a href="/courses/99/assignments/20">other</a><a href="/courses/10/assignments/20" data-method="post">write</a>'
    session = Session(
        [
            Response(html(links=links)),
            Response(b'<span class="submissionOutlineRubricItem">feedback</span>'),
        ]
    )
    monkeypatch.setattr(gc, "browser_cookie_session", lambda *a: session)
    code, out, _ = run(
        monkeypatch,
        capsys,
        config,
        "grades",
        "--source",
        "gradescope",
        "--details",
        *(["--json"] if mode == "json" else []),
    )
    assert code == 3 and len(session.calls) == 2
    assert session.calls[1][0] == url
    if mode == "json":
        value = json.loads(out)
        assert not value["complete"]
        assert value["data"]["assignments"][0]["details"][0]["status"] == "partial"
    else:
        assert "Feedback (partial)" in out


@pytest.mark.parametrize(
    "location", ["/courses/99", "/login", "https://evil.invalid/x"]
)
def test_grade_redirect_scope(monkeypatch, capsys, config, location):
    session = Session([Response(status=302, headers={"Location": location})])
    monkeypatch.setattr(gc, "browser_cookie_session", lambda *a: session)
    code, out, _ = run(
        monkeypatch, capsys, config, "grades", "--source", "gradescope", "--json"
    )
    assert code == 3 and len(session.calls) == 1
    assert json.loads(out)["sources"][0]["status"] == "error"


@pytest.mark.parametrize("mode", ["plain", "json"])
def test_gradescope_no_optin(monkeypatch, capsys, config, mode):
    code, out, err = run(
        monkeypatch,
        capsys,
        config,
        "grades",
        "--source",
        "gradescope",
        *(["--json"] if mode == "json" else []),
    )
    assert code == 3
    assert "SOURCE_UNAVAILABLE" in out if mode == "json" else "unavailable" in err


def ed_session(monkeypatch, responses):
    session = Session(responses)
    monkeypatch.setattr(ed_client, "configured_session", lambda: session)
    return session


def inventory_responses():
    return [
        json_response({"threads": [thread()]}),
        json_response({"threads": []}),
        json_response({"thread": thread()}),
    ]


@pytest.mark.parametrize("mode", ["plain", "json"])
def test_ed_materials_cli(monkeypatch, capsys, config, mode):
    session = ed_session(monkeypatch, inventory_responses())
    code, out, _ = run(
        monkeypatch,
        capsys,
        config,
        "ed",
        "materials",
        "--course-id",
        "10",
        *(["--json"] if mode == "json" else []),
    )
    assert code == 0 and len(session.calls) == 3
    assert all(c[2]["Authorization"] == "Bearer synthetic-token" for c in session.calls)
    assert all(not c[0].endswith("/user") for c in session.calls)
    if mode == "json":
        result = json.loads(out)["data"]
        assert (
            result["complete"] and result["fetched_count"] == len(result["items"]) == 1
        )
    else:
        assert "id: ed:10:20:" in out


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("mode", ["plain", "json"])
def test_ed_materials_auth(monkeypatch, capsys, config, status, mode):
    ed_session(monkeypatch, [json_response({}, status)])
    code, out, err = run(
        monkeypatch,
        capsys,
        config,
        "ed",
        "materials",
        "--course-id",
        "10",
        *(["--json"] if mode == "json" else []),
    )
    assert code == 2 and "ED_AUTH_REQUIRED" in out + err
    assert "synthetic-token" not in out + err


def test_ed_no_token(monkeypatch, capsys, config):
    monkeypatch.delenv("CMU_CLI_ED_TOKEN")
    code, out, _ = run(
        monkeypatch, capsys, config, "ed", "materials", "--course-id", "10", "--json"
    )
    assert code == 2 and "Set CMU_CLI_ED_TOKEN" in out


@pytest.mark.parametrize("failure", ["cap", "repeat", "detail", "missing_body"])
def test_ed_incomplete(monkeypatch, capsys, config, failure):
    row = thread()
    responses = inventory_responses()
    flags = []
    if failure == "cap":
        responses.pop(1)
        flags = ["--max-pages", "1"]
    elif failure == "repeat":
        responses[1] = responses[0]
    elif failure == "detail":
        responses[-1] = json_response({}, 403)
    else:
        row.pop("content", None)
        row.pop("document", None)
        responses[-1] = json_response({"thread": row})
    ed_session(monkeypatch, responses)
    code, out, _ = run(
        monkeypatch,
        capsys,
        config,
        "ed",
        "materials",
        "--course-id",
        "10",
        "--json",
        *flags,
    )
    assert code == 3
    assert not json.loads(out)["data"]["complete"]


def test_ed_download_roundtrip_and_local_edits(monkeypatch, capsys, config, tmp_path):
    sessions = []

    def asset():
        session = Session([Response()])
        session.headers["Authorization"] = "must-be-cleared"
        sessions.append(session)
        return session

    monkeypatch.setattr(dm, "configured_session", asset)
    # Select actual recognized ID, rather than trusting a supplied URL/export.
    ed_session(monkeypatch, inventory_responses())
    _, out, _ = run(
        monkeypatch, capsys, config, "ed", "materials", "--course-id", "10", "--json"
    )
    identity = json.loads(out)["data"]["items"][0]["id"]
    args = (
        "ed",
        "download",
        "--course-id",
        "10",
        "--id",
        identity,
        "--output",
        str(tmp_path / "assets"),
        "--json",
    )
    for expected in ["downloaded", "unchanged"]:
        ed_session(monkeypatch, inventory_responses())
        code, out, _ = run(monkeypatch, capsys, config, *args)
        assert code == 0
        download = json.loads(out)["data"]["download"]
        assert download["status"] == expected
    path = tmp_path / "assets" / download["relative_path"]
    path.write_bytes(b"local edit")
    ed_session(monkeypatch, inventory_responses())
    code, out, _ = run(monkeypatch, capsys, config, *args)
    assert code == 2 and "local edits were preserved" in out
    assert path.read_bytes() == b"local edit"
    assert all(
        "Authorization" not in s.calls[0][2] and not s.calls[0][3] for s in sessions
    )
    assert all(
        isinstance(s.get_adapter("https://"), PublicHTTPSAdapter) for s in sessions
    )


def test_download_unknown_id_no_asset_request(monkeypatch, capsys, config, tmp_path):
    ed_session(monkeypatch, inventory_responses())
    monkeypatch.setattr(
        dm, "configured_session", lambda: pytest.fail("No asset should be requested")
    )
    code, out, _ = run(
        monkeypatch,
        capsys,
        config,
        "ed",
        "download",
        "--course-id",
        "10",
        "--id",
        "fake",
        "--output",
        str(tmp_path),
        "--json",
    )
    assert code == 3 and "attachment_id_not_in_returned_inventory" in out


def test_asset_dns_private_blocked(monkeypatch, tmp_path):
    import socket

    from cmu_cli.public_transport import PublicHTTPSConnection

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    monkeypatch.setattr(
        socket.socket, "connect", lambda *a: pytest.fail("Private socket connection")
    )
    # Exercise real adapter connection policy, not a test download double.
    from urllib3.exceptions import NewConnectionError

    with pytest.raises(NewConnectionError):
        PublicHTTPSConnection("static.us.edusercontent.com")._new_conn()


def test_detail_redirect_same_origin_other_course(monkeypatch, capsys, config):
    session = Session(
        [
            Response(html(links='<a href="/courses/10/assignments/20">view</a>')),
            Response(status=302, headers={"Location": "/courses/99/assignments/20"}),
        ]
    )
    monkeypatch.setattr(gc, "browser_cookie_session", lambda *a: session)
    code, out, _ = run(
        monkeypatch,
        capsys,
        config,
        "grades",
        "--source",
        "gradescope",
        "--details",
        "--json",
    )
    assert code == 3 and len(session.calls) == 2
    assert json.loads(out)["data"]["assignments"][0]["details"] == []
