"""The quiz line must not present an open time where a deadline is read.

``quiz_rows`` records ``due_at`` and ``unlock_at`` separately and the JSON contract
keeps both. The plain line printed ``starts_local`` -- derived from ``unlock_at`` --
in the deadline position with no label, so a quiz that opened two months ago read as
though that were its date, and a quiz with a real due date and no unlock time read as
having no date at all.
"""

from unittest.mock import Mock

from cmu_cli import cli
from cmu_cli.models import Course

COURSE = Course("A", 1, "A", "a")


def _row(**quiz):
    client = Mock()
    client.quizzes.return_value = [{"id": 4, "title": "quiz", **quiz}]
    return cli.quiz_rows(client, [COURSE])[0]


DUE = "2030-01-02T00:00:00Z"
OPEN = "2029-11-01T00:00:00Z"


def test_due_date_is_shown_when_canvas_sets_one():
    row = _row(due_at=DUE, unlock_at=None)
    assert row["due_at"] == DUE
    assert cli.quiz_time(row) == f"due {cli.format_time(DUE)}"


def test_due_date_wins_over_the_open_time():
    """Both are set: the deadline is the one a reader is looking for."""
    row = _row(due_at=DUE, unlock_at=OPEN)
    shown = cli.quiz_time(row)
    assert shown == f"due {cli.format_time(DUE)}"
    assert cli.format_time(OPEN) not in shown


def test_an_open_time_alone_is_labelled_as_one():
    row = _row(due_at=None, unlock_at=OPEN)
    assert cli.quiz_time(row) == f"opens {cli.format_time(OPEN)}"


def test_no_dates_at_all_reports_no_deadline():
    row = _row(due_at=None, unlock_at=None)
    assert cli.quiz_time(row).startswith("due ")


def test_json_contract_still_separates_the_two_times():
    row = _row(due_at="2030-01-02T00:00:00Z", unlock_at="2029-11-01T00:00:00Z")
    assert row["due_at"] != row["unlock_at"] == row["starts_at"]
    assert row["due_local"] != row["starts_local"]
