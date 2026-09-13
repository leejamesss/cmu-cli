"""Synthetic Ed protocol fixtures only; never opens browser or live accounts."""

import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter

from cmu_cli.ed_client import EdAuthError, EdClient, EdError
from cmu_cli.web_session import configured_session

# Restore Requests preparation only on synthetic sessions whose HTTPS adapter is
# in-memory; retain the suite-wide network guard for every other session.
_ORIGINAL_REQUEST = requests.Session.request


def synthetic_session():
    session = configured_session()
    session.request = _ORIGINAL_REQUEST.__get__(session, requests.Session)
    return session


class Transport(BaseAdapter):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []

    def send(self, request, **kwargs):
        self.sent.append(request)
        assert request.method == "GET"
        status, payload, headers = next(self.responses)
        response = requests.Response()
        response.status_code = status
        response.headers.update({"Content-Type": "application/json", **headers})
        response.raw = io.BytesIO(json.dumps(payload).encode())
        response.url = request.url
        response.request = request
        return response

    def close(self):
        pass


def client(*payloads):
    session = synthetic_session()
    session.headers["Authorization"] = "Bearer synthetic-secret"
    session.headers["X-Token"] = "synthetic-custom-secret"
    adapter = Transport(payloads)
    session.mount("https://", adapter)
    return EdClient(session), adapter


def ok(payload):
    return 200, payload, {}


def post(pid=9, **extra):
    return {
        "id": pid,
        "course_id": 3,
        "number": 1,
        "title": "Alpha",
        "created_at": "2026-09-12T13:00:00.123456+10:00",
        "updated_at": None,
        **extra,
    }


def test_course_membership_and_no_identity_time_loss():
    membership = {"course": {"id": 3, "code": "15-123"}, "role": {"role": "student"}}
    c, transport = client(
        ok({"courses": [membership]}), ok({"threads": [post()]}), ok({"threads": []})
    )
    assert c.courses() == [membership]
    result = c.threads("3")
    assert result["items"] == [post()]
    assert result["complete"] is True
    assert result["snapshot_consistent"] is False
    assert result["pages_fetched"] == 2
    assert urlsplit(transport.sent[0].url).path == "/api/user"
    assert parse_qs(urlsplit(transport.sent[2].url).query) == {
        "limit": ["100"],
        "offset": ["1"],
        "sort": ["new"],
    }


def test_multiple_full_pages_then_empty():
    c, t = client(
        ok({"threads": [post(9)]}), ok({"threads": [post(10)]}), ok({"threads": []})
    )
    result = c.threads(3, page_size=1)
    assert [p["id"] for p in result["items"]] == [9, 10]
    assert result["complete"]
    assert [parse_qs(urlsplit(r.url).query)["offset"] for r in t.sent] == [
        ["0"],
        ["1"],
        ["2"],
    ]


def test_cap_is_partial():
    c, _ = client(ok({"threads": [post()]}))
    result = c.threads(3, max_pages=1)
    assert not result["complete"]
    assert result["reason"] == "page limit reached"


@pytest.mark.parametrize(
    "second,error",
    [
        ((401, {"secret": "do-not-print"}, {}), EdAuthError),
        ((403, {}, {}), EdAuthError),
        ((500, {}, {}), EdError),
        (ok({"threads": [post()]}), EdError),
        (ok({"threads": {}}), EdError),
        (ok({"threads": [post(10, course_id=4)]}), EdError),
    ],
)
def test_later_error_retains_prefix_without_claiming_success(second, error):
    c, _ = client(ok({"threads": [post()]}), second)
    with pytest.raises(error) as caught:
        c.threads(3)
    assert caught.value.partial["items"] == [post()]
    assert not caught.value.partial["complete"]
    assert "do-not-print" not in str(caught.value)


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/steal",
        "https://us.edstem.org:444/api/user",
        "http://us.edstem.org/api/user",
        "https://edstem.org/api/user",
    ],
)
def test_authenticated_redirect_never_sends_second_request(location):
    c, transport = client((302, {}, {"Location": location}))
    with pytest.raises(EdError):
        c.courses()
    assert len(transport.sent) == 1
    assert transport.sent[0].headers["Authorization"] == "Bearer synthetic-secret"
    assert transport.sent[0].headers["X-Token"] == "synthetic-custom-secret"


def test_same_origin_redirect_works():
    c, transport = client(
        (307, {}, {"Location": "/api/user?retry=1"}), ok({"courses": []})
    )
    assert c.courses() == []
    assert len(transport.sent) == 2


@pytest.mark.parametrize("bad", [0, -1, True, "03", "3/threads", "3?x=1", " 3", 3.0])
def test_identifier_rejected_before_transport(bad):
    c, t = client()
    with pytest.raises(EdError):
        c.threads(bad)
    assert not t.sent


def test_nested_replies_and_counts():
    nested = {
        "id": 12,
        "thread_id": 9,
        "parent_id": 11,
        "comments": [],
        "created_at": "unchanged",
    }
    answer = {"id": 11, "thread_id": 9, "parent_id": None, "comments": [nested]}
    row = post(answers=[answer], comments=[], reply_count=2)
    c, _ = client(ok({"thread": row}), ok({"thread": row}))
    assert c.thread(9, course_id=3) == row
    result = c.replies(9)
    assert result["complete"]
    assert [r["id"] for r in result["items"]] == [11, 12]
    assert result["items"][1]["created_at"] == "unchanged"


def test_reply_count_mismatch_is_partial():
    c, _ = client(ok({"thread": post(answers=[], comments=[], reply_count=2)}))
    assert not c.replies(9)["complete"]


@pytest.mark.parametrize(
    "row",
    [
        post(10, answers=[], comments=[]),
        post(answers=None, comments=[]),
        post(answers=[], comments=[{"id": 2, "thread_id": 8, "comments": []}]),
    ],
)
def test_bad_thread_detail(row):
    c, _ = client(ok({"thread": row}))
    with pytest.raises(EdError):
        c.thread(9)


def test_search_scope_and_fetch_count():
    c, t = client(
        ok(
            {
                "threads": [
                    post(content="<p>hello &amp; world</p>"),
                    post(10, title="Other"),
                ]
            }
        ),
        ok({"threads": []}),
    )
    result = c.search(3, "HELLO & WORLD")
    assert result["matched_count"] == 1
    assert result["fetched_count"] == 2
    assert result["search_mode"] == "local"
    assert "excludes replies" in result["scope"]
    assert all("search" not in r.url for r in t.sent)


def test_environment_token_is_explicit(monkeypatch):
    monkeypatch.delenv("CMU_CLI_ED_TOKEN", raising=False)
    with pytest.raises(EdAuthError):
        EdClient().courses()
    session = synthetic_session()
    transport = Transport([ok({"courses": []})])
    session.mount("https://", transport)
    monkeypatch.setenv("CMU_CLI_ED_TOKEN", "synthetic-env-token")
    monkeypatch.setattr("cmu_cli.ed_client.configured_session", lambda: session)
    assert EdClient().courses() == []
    assert transport.sent[0].headers["Authorization"] == "Bearer synthetic-env-token"


@pytest.mark.parametrize(
    "base",
    [
        "https://evil.example/api",
        "http://us.edstem.org/api",
        "https://us.edstem.org/api/",
        "https://us.edstem.org:444/api",
    ],
)
def test_base_rejected(base):
    with pytest.raises(EdError):
        EdClient(base_url=base)


@pytest.mark.parametrize(
    "payload,headers,error",
    [
        ({"code": "bad_token"}, {}, EdAuthError),
        ([], {}, EdError),
        ({"courses": []}, {"Content-Type": "text/html"}, EdError),
        ({"courses": [{}]}, {}, EdError),
    ],
)
def test_invalid_or_auth_payload(payload, headers, error):
    c, _ = client((200, payload, headers))
    with pytest.raises(error):
        c.courses()
