"""A generated index must be readable by date.

Canvas returns assignments in assignment-group order, which interleaves several
ascending runs. The index wrote them in that order, so a real course produced
2026-09-10, 2026-09-25, 2026-10-01, 2026-10-30, then back to 2026-08-28 -- while
``cmu-cli assignments``, reading the same data, sorted correctly.
"""

import re

from cmu_cli.models import Course
from cmu_cli.storage import (
    announcements_markdown,
    assignments_markdown,
    deadline_order,
    newest_first,
)

COURSE = Course("A", 1, "A", "a")


def _due_dates(markdown: str) -> list[str]:
    return re.findall(r"- 截止：(\d{4}-\d{2}-\d{2})", markdown)


def test_assignments_are_written_soonest_first():
    # Two interleaved ascending runs, as Canvas groups them.
    assignments = [
        {"name": "late in run one", "due_at": "2026-10-30T00:00:00Z"},
        {"name": "early in run two", "due_at": "2026-08-28T00:00:00Z"},
        {"name": "early in run one", "due_at": "2026-09-10T00:00:00Z"},
    ]
    dates = _due_dates(assignments_markdown(COURSE, assignments))
    assert dates == sorted(dates)


def test_undated_assignments_are_written_last():
    assignments = [
        {"name": "no deadline", "due_at": None},
        {"name": "has one", "due_at": "2026-09-10T00:00:00Z"},
    ]
    body = assignments_markdown(COURSE, assignments)
    assert body.index("has one") < body.index("no deadline")


def test_offsets_are_compared_as_instants_not_strings():
    """08:00+02:00 is earlier than 07:00Z; string order would disagree."""
    earlier = "2026-09-10T08:00:00+02:00"
    later = "2026-09-10T07:00:00Z"
    assert deadline_order(earlier) < deadline_order(later)
    assert earlier > later  # the naive comparison this replaces


def test_an_unparseable_date_does_not_break_the_sort():
    assignments = [
        {"name": "broken", "due_at": "not a date"},
        {"name": "dated", "due_at": "2026-09-10T00:00:00Z"},
        {"name": "absent", "due_at": None},
    ]
    body = assignments_markdown(COURSE, assignments)
    assert body.index("dated") < body.index("broken") < body.index("absent")


def test_announcements_are_written_newest_first():
    announcements = [
        {"title": "older", "posted_at": "2026-09-01T00:00:00Z"},
        {"title": "newest", "posted_at": "2026-09-20T00:00:00Z"},
        {"title": "middle", "posted_at": "2026-09-10T00:00:00Z"},
    ]
    body = announcements_markdown(COURSE, announcements)
    assert body.index("newest") < body.index("middle") < body.index("older")


def test_undated_announcements_stay_at_the_end_not_the_top():
    """Reversing the sort key directly would lift the undated band to the top."""
    items = [
        {"title": "undated"},
        {"title": "dated", "posted_at": "2026-09-01T00:00:00Z"},
    ]
    assert [item["title"] for item in newest_first(items, "posted_at")] == [
        "dated",
        "undated",
    ]
