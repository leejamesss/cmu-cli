"""Synthetic student-row regressions; no network or authentication."""

from unittest.mock import Mock

import pytest

from cmu_cli import cli
from cmu_cli.gradescope_client import GradescopeClient
from cmu_cli.gradescope_details import parse_course_grades
from cmu_cli.models import Course

COURSE = Course(
    "DEMO", 1, "Demo", "demo", gradescope_url="https://www.gradescope.com/courses/101"
)
LINK = "/courses/101/assignments/202/submissions/303"


@pytest.mark.parametrize(
    "text,score,link,status,submitted,points,possible,release",
    [
        (None, "100.0 / 100.0", LINK, "Graded", True, 100.0, 100.0, "released"),
        (None, "0 / 100", LINK, "Graded", True, 0.0, 100.0, "released"),
        (None, "0 / 0", None, "Graded", True, 0.0, 0.0, "released"),
        (None, None, LINK, "Submitted", True, None, None, "unknown"),
        (None, "— / 100", LINK, "Submitted", True, None, None, "unknown"),
        (None, None, None, "Unknown", None, None, None, "unknown"),
        ("Not submitted", None, LINK, "Not submitted", False, None, None, "unknown"),
        ("No Submission", None, None, "No Submission", False, None, None, "unknown"),
        ("Submitted", None, None, "Submitted", True, None, None, "unknown"),
        ("Graded", None, None, "Graded", True, None, None, "unknown"),
        (None, "not a score", None, "Unknown", None, None, None, "unknown"),
        (
            None,
            None,
            "https://evil.invalid" + LINK,
            "Unknown",
            None,
            None,
            None,
            "unknown",
        ),
        (
            None,
            None,
            "/courses/999/assignments/202/submissions/303",
            "Unknown",
            None,
            None,
            None,
            "unknown",
        ),
    ],
)
def test_score_and_submission_evidence(
    monkeypatch, text, score, link, status, submitted, points, possible, release
):
    title = f'<a href="{link}">Assignment 1</a>' if link else "Assignment 1"
    status_html = f'<div class="submissionStatus--text">{text}</div>' if text else ""
    score_html = f'<div class="submissionStatus--score">{score}</div>' if score else ""
    html = f'<table id="assignments-student-table"><tbody><tr><th scope="row">{title}</th><td class="submissionStatus">{status_html}{score_html}</td><td>Other numbers: 5 / 10</td></tr></tbody></table>'
    parsed = GradescopeClient.parse_assignments(COURSE, html)
    grade_row = parse_course_grades(COURSE, html)[0]
    assert grade_row["score"] == points
    assert grade_row["points_possible"] == possible
    if release == "released":
        assert grade_row["release_state"] == "released"
        assert grade_row["submission_state"] == "submitted"
    expected = {
        "status": status,
        "submitted": submitted,
        "score": points,
        "points_possible": possible,
        "grade_status": release,
    }
    assert {key: parsed[0][key] for key in expected} == expected
    monkeypatch.setattr(GradescopeClient, "assignments", lambda self, course: parsed)
    client = Mock()
    client.assignments.return_value = []
    row = cli.assignment_rows(client, [COURSE])[0]
    assert row["source"] == "gradescope"
    assert {key: row[key] for key in expected} == expected
    if link == LINK:
        assert row["url"] == "https://www.gradescope.com" + LINK


@pytest.mark.parametrize(
    "attribute", ["hidden", 'aria-hidden="true"', 'style="display:none"']
)
def test_hidden_score_is_not_released(attribute):
    html = f'<table id="assignments-student-table"><tbody><tr><th scope="row">Homework</th><td><div class="submissionStatus--score" {attribute}>0 / 10</div></td></tr></tbody></table>'
    assignment = GradescopeClient.parse_assignments(COURSE, html)[0]
    assert assignment["score"] is None
    assert assignment["submitted"] is None
    assert assignment["grade_status"] == "unknown"
    assert parse_course_grades(COURSE, html)[0]["release_state"] == "unknown"
