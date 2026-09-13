"""Synthetic own-grade API and CLI contract tests; no account access."""

import json
from unittest.mock import Mock

import pytest

from cmu_cli import cli
from cmu_cli.canvas_client import CanvasClient, CanvasEndpointUnavailable, CanvasError
from cmu_cli.canvas_grades import (
    normalize_grade,
    own_assignments,
    own_enrollments,
    unique,
)
from cmu_cli.models import Config, Course


@pytest.mark.parametrize("score", [0, None, -1, 101, 2.5])
@pytest.mark.parametrize("state", ["graded", "submitted", "unsubmitted", None])
def test_grade_preserves_server_values(score, state):
    row = normalize_grade(
        {"id": 1, "points_possible": 100},
        {"score": score, "workflow_state": state},
        "A",
    )
    assert row["score"] == score
    assert row["release_state"] == "unknown"
    assert row["submission_state"] == (state or "unknown")


def test_feedback_and_stale_excused():
    row = normalize_grade(
        {"id": 1},
        {
            "score": 0,
            "grade": "F",
            "posted_at": "2030-01-01",
            "excused": True,
            "missing": True,
            "late": True,
            "grade_matches_current_submission": False,
            "submission_comments": [],
            "rubric_assessment": {},
        },
        "A",
        True,
    )
    assert row["release_state"] == "posted"
    assert row["grade_matches_current_submission"] is False
    assert row["rubric_assessment_availability"] == "returned"
    assert row["submission_comments"] == []


@pytest.mark.parametrize("user,assignment", [(7, 1), (8, 2)])
def test_reject_wrong_scope(user, assignment):
    client = Mock()
    client.assignments.return_value = [{"id": 1}]
    client.get.return_value = [{"assignment_id": assignment, "user_id": user}]
    with pytest.raises(CanvasError):
        own_assignments(client, 2, 8)


def test_exact_own_requests_and_no_read_status():
    client = Mock()
    client.assignments.return_value = [{"id": 1}]
    client.get.return_value = [{"assignment_id": 1, "user_id": 8}]
    own_assignments(client, 2, 8, True)
    client.get.assert_called_once_with(
        "/api/v1/courses/2/students/submissions",
        {"per_page": 100, "include[]": ["submission_comments", "rubric_assessment"]},
    )


def test_totals_multiple_sections_no_unposted():
    client = Mock()
    client.get.return_value = [
        {
            "id": n,
            "course_id": 2,
            "user_id": 8,
            "type": "StudentEnrollment",
            "grades": {
                "current_score": 0,
                "final_score": 20,
                "unposted_current_score": 90,
            },
        }
        for n in (1, 2)
    ]
    rows = own_enrollments(client, 8, 2)
    assert len(rows) == 2
    assert all(
        r["current_score"] == 0
        and r["final_score"] == 20
        and "unposted_current_score" not in r
        for r in rows
    )
    assert client.get.call_args.args[1]["state[]"] == ["current_and_concluded"]


@pytest.mark.parametrize("details", [False, True])
@pytest.mark.parametrize("denied", [False, True])
def test_cli_synthetic_dispatch(monkeypatch, capsys, tmp_path, details, denied):
    client = Mock()
    client.assignments.return_value = [{"id": 1, "name": "Zero", "points_possible": 10}]

    def get(path, params=None):
        if path.endswith("profile"):
            return {"id": 8}
        if path.endswith("enrollments"):
            if denied:
                raise CanvasEndpointUnavailable(403)
            return []
        return [{"assignment_id": 1, "user_id": 8, "score": 0}]

    client.get.side_effect = get
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda _: Config(
            "https://canvas.example.edu", tmp_path, (Course("A", 2, "A", "a"),)
        ),
    )
    monkeypatch.setattr(cli, "CanvasClient", lambda *a, **kw: client)
    monkeypatch.setattr(
        "sys.argv", ["cmu-cli", "grades", "--json", *(["--details"] if details else [])]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    data = json.loads(capsys.readouterr().out)
    assert exc.value.code == (3 if denied else 0)
    assert data["complete"] is not denied
    assert data["data"]["assignments"][0]["score"] == 0
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("bad", [True, "1", "../1", 0, -1, None])
def test_ids_rejected(bad):
    with pytest.raises(CanvasError):
        unique([{"id": bad}])


def test_duplicate_conflicts():
    assert unique([{"id": 1}, {"id": 1}]) == [{"id": 1}]
    with pytest.raises(CanvasError):
        unique([{"id": 1}, {"id": 1, "score": 2}])


def test_pagination_and_foreign_next(monkeypatch):
    import requests

    client = CanvasClient("https://canvas.example.edu", session=requests.Session())
    page = Mock(
        side_effect=[
            ([{"id": 1}], "https://canvas.example.edu/api/v1/x?page=2"),
            ([{"id": 2}], None),
        ]
    )
    monkeypatch.setattr(client, "_page", page)
    assert client.get("/api/v1/x") == [{"id": 1}, {"id": 2}]
    assert page.call_count == 2
