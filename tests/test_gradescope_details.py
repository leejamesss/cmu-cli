"""Entirely synthetic DOM and transport fixtures; no account records or network."""

from types import SimpleNamespace

import pytest

from cmu_cli.gradescope_client import GradescopeError
from cmu_cli.gradescope_details import (
    parse_course_grades,
    parse_score,
    parse_submission_details,
    read_course_grades,
)
from cmu_cli.models import Course
from cmu_cli.web_session import SessionError

BASE = "https://www.gradescope.com/courses/101"
DETAIL = BASE + "/assignments/202/submissions/303"
COURSE = Course("DEMO", 1, "Demo", "Demo", gradescope_url=BASE)


def table(status="0 / 10", links="", attributes=""):
    return f"""<table id="assignments-student-table"><tbody><tr {attributes}>
    <th scope="row"><span data-assignment-title="Demo" data-assignment-id="202">Demo</span></th>
    <td class="submissionStatus--text">{status}</td><td>{links}</td>
    </tr></tbody></table>"""


@pytest.mark.parametrize(
    "text,score,maximum",
    [
        ("0 / 10", 0, 10),
        (" 1.5 / 2 ", 1.5, 2),
        ("11/10", 11, 10),
        ("-1 / 10", -1, 10),
        ("0/0", 0, 0),
        (".5 / 1", 0.5, 1),
        ("1\u00a0/\u00a02", 1, 2),
    ],
)
def test_score_pairs(text, score, maximum):
    assert parse_score(text) == {
        "score": score,
        "points_possible": maximum,
        "release_state": "released",
    }


@pytest.mark.parametrize(
    "text",
    [
        "",
        "No Submission",
        "Not Submitted",
        "Ungraded",
        "Graded",
        "A",
        "90%",
        "9",
        "1,5 / 2",
        "1,000 / 2,000",
        "1/2 attempts",
        "1/2/3",
        "NaN/2",
        "1/-2",
        "∞/1",
        "1e3/2",
        "9" * 400 + "/10",
        "Graded 1/2",
    ],
)
def test_ambiguous_scores_unknown(text):
    assert parse_score(text) == {
        "score": None,
        "points_possible": None,
        "release_state": "unknown",
    }


@pytest.mark.parametrize(
    "status,state,release",
    [
        ("Submitted", "submitted", "not_released"),
        ("Not Submitted", "not_submitted", "unknown"),
        ("No submission", "not_submitted", "unknown"),
        ("Graded", "unknown", "unknown"),
        ("0/10", "submitted", "released"),
    ],
)
def test_course_states(status, state, release):
    row = parse_course_grades(COURSE, table(status))[0]
    assert row["submission_state"] == state
    assert row["release_state"] == release
    assert row["url"] is None  # Do not synthesize from data-assignment-id.
    assert row["assignment_id"] == "202"


def test_links_observed_and_deduplicated():
    links = f'<a href="{DETAIL}">View</a><a href="{DETAIL}">Again</a>'
    row = parse_course_grades(COURSE, table(links=links))[0]
    assert row["detail_links"] == [DETAIL]
    assert row["score"] == 0
    assert row["unavailable_reason"] is None


@pytest.mark.parametrize(
    "href",
    [
        "https://gradescope.com/courses/101/assignments/202",
        "/courses/999/assignments/202",
        "/courses/101/assignments/202/export",
        "javascript:alert(1)",
        "https://www.gradescope.com.evil.test/courses/101/assignments/202",
        "http://www.gradescope.com/",
        "https://user@www.gradescope.com/courses/101/assignments/202",
        "/logout",
        DETAIL + "?act=delete",
        DETAIL + "#x",
        " " + DETAIL,
        "//127.0.0.1/x",
    ],
)
def test_unsafe_or_unscoped_detail_link_ignored(href):
    assert (
        parse_course_grades(COURSE, table(links=f'<a href="{href}">View</a>'))[0]["url"]
        is None
    )


@pytest.mark.parametrize(
    "attrs",
    [
        "hidden",
        'aria-hidden="true"',
        'style="display: none"',
        'style="visibility:hidden"',
    ],
)
def test_hidden_rows_omitted(attrs):
    assert parse_course_grades(COURSE, table(attributes=attrs)) == []


@pytest.mark.parametrize(
    "html", ["", "<html>Login</html>", '<input type="password">' + table()]
)
def test_missing_table_or_login_rejected(html):
    with pytest.raises(GradescopeError):
        parse_course_grades(COURSE, html)


def rubric(label="Applied rubric item", extra=""):
    return f"""<div class="submissionOutlineRubricItem" {extra}>
    <span class="sr-only">{label}</span><span aria-label="minus two points">-2</span>
    <span class="submissionOutlineRubricItem--description">Explain <b>why</b></span></div>"""


def test_visible_rubric_only_no_inferred_association_or_points():
    html = '<div class="submissionOutlineQuestion--title">Question 1</div>'
    html += (
        rubric() + rubric("Unapplied rubric item") + rubric("") + rubric(extra="hidden")
    )
    result = parse_submission_details(COURSE, DETAIL, html)
    assert result["question_titles"] == ["Question 1"]
    assert [r["applied"] for r in result["rubric_items"]] == [True, False, None]
    assert all(r["question"] is None for r in result["rubric_items"])
    assert result["rubric_items"][0]["description"] == "Explain why"
    assert result["rubric_items"][0]["points_label"] == "minus two points"
    assert result["score"] is None
    assert result["status"] == "partial"


def test_material_links_not_endpoints_or_download_claims():
    html = """<a href="/observed/opaque?token=example">Download Graded Copy</a>
    <a href="https://assets.example.test/actual.pdf?signature=synthetic">Download Graded Copy</a>
    <a href="/generate" data-method="post">Download Graded Copy</a>
    <a href="/hidden" hidden>Download Graded Copy</a>
    <a href="javascript:alert(1)">Download Graded Copy</a>
    <a href="/instructor-export">Download All Submissions</a>"""
    result = parse_submission_details(COURSE, DETAIL, html)
    assert len(result["materials"]) == 2
    assert [m["credential_policy"] for m in result["materials"]] == [
        "same_origin_only",
        "anonymous_only",
    ]
    assert all(m["download_verified"] is False for m in result["materials"])
    assert result["materials"][0]["url"].endswith("/observed/opaque?token=example")


def test_missing_detail_content_is_unknown_not_empty_complete():
    result = parse_submission_details(
        COURSE, DETAIL, '<div data-react-props="private">Loading</div>'
    )
    assert result["status"] == "partial"
    assert result["feedback_availability"] == "unknown"
    assert result["materials"] == []


@pytest.mark.parametrize(
    "url",
    [
        BASE + "/assignments/202/export",
        "https://evil.test/x",
        DETAIL.replace("101", "999"),
        DETAIL + "?x=1",
    ],
)
def test_wrong_detail_scope_rejected(url):
    with pytest.raises(GradescopeError):
        parse_submission_details(COURSE, url, rubric())


def test_detail_login_rejected():
    with pytest.raises(GradescopeError):
        parse_submission_details(COURSE, DETAIL, '<input type="password">')


class Response:
    def __init__(self, body, status=200, headers=None):
        self.status_code, self.headers = status, headers or {}
        self.body, self.closed = body, False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


def test_read_uses_real_safe_transport_contract():
    response = Response(table().encode())
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    session = SimpleNamespace(get=get)
    assert read_course_grades(session, COURSE)[0]["score"] == 0
    assert calls[0][0] == BASE
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["stream"] is True
    assert response.closed


def test_cross_origin_redirect_never_sent():
    calls = []
    response = Response(b"", 302, {"Location": "https://evil.test/steal"})
    session = SimpleNamespace(get=lambda url, **kwargs: calls.append(url) or response)
    with pytest.raises(GradescopeError):
        read_course_grades(session, COURSE)
    assert calls == [BASE]
    assert response.closed


@pytest.mark.parametrize(
    "response",
    [
        Response(b"denied", 403),
        Response(b"\xff"),
        Response(b"", headers={"Content-Length": "999999999"}),
    ],
)
def test_read_errors_are_closed_sanitized(response):
    with pytest.raises(GradescopeError):
        read_course_grades(SimpleNamespace(get=lambda *a, **k: response), COURSE)
    assert response.closed


def test_transport_error_sanitized(monkeypatch):
    def fail(*args, **kwargs):
        raise SessionError("private diagnostics")

    monkeypatch.setattr("cmu_cli.gradescope_details.safe_request", fail)
    with pytest.raises(GradescopeError, match="^Gradescope course read failed$"):
        read_course_grades(object(), COURSE)
