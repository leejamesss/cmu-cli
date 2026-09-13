"""A course with no meeting pattern is not a parse failure.

Directed research, independent study and thesis units are normally enrolled and
normally have no day, time or room. SIO writes "TBA" in some of those cells and
leaves others empty. Treating any empty meeting cell as a missing field marked the
reader's entire semester complete=false, status=partial, exit 3 -- over a row that
had been read correctly.

Structural drift is already caught before this point: a row whose columns are not
recognised is rejected as ROW_SCHEMA_UNRECOGNIZED and never reaches these fields.
So an empty cell here is the schedule saying "not scheduled".
"""

import pytest

from cmu_cli.sio_client import parse_semester_schedule

HEADERS = ["Title / Number & Section", "Instructor", "Dates", "Times", "Bldg/Room"]
LABELS = ["Course", "Instructor", "Dates", "Times", "Bldg/Room"]


def _page(rows):
    header = "".join(
        f'<th data-title="{x}"><div class="grid-hdr">{x}</div></th>' for x in HEADERS
    )
    body = ""
    for values in rows:
        first, *rest = zip(LABELS, values, strict=True)
        cells = f'<th scope="row" data-title="{first[0]}">{first[1]}</th>'
        cells += "".join(f'<td data-title="{k}">{v}</td>' for k, v in rest)
        cells += '<td><a href="#">more</a></td>'
        body += f"<tr>{cells}</tr>"
    return (
        "<title>CMU Student Information Online</title><h1>Semester Schedule</h1>"
        '<select id="semester-code-select"><option value="X99" selected>Term</option>'
        f'</select><table class="course-list-tbl"><tr>{header}</tr>{body}</table>'
    )


def _course(title, number, instructor, dates, times, room):
    return [
        f'{title}<div class="display-block">{number}</div>',
        f'<div class="display-block">{instructor}</div>',
        dates,
        times,
        room,
    ]


SCHEDULED = _course(
    "EXAMPLE LECTURE",
    "10001 A",
    "Example Teacher",
    "T Th",
    "11:00AM to 12:20PM",
    "EX 1",
)
# The shape a directed-research row takes: TBA where a day/room would be, and an
# empty times cell.
UNSCHEDULED = _course(
    "EXAMPLE RESEARCH", "10002 O", "Example Teacher", "TBA", "", "TBA TBA"
)


def test_an_unscheduled_course_does_not_make_the_semester_incomplete():
    result = parse_semester_schedule(_page([SCHEDULED, UNSCHEDULED]))
    assert result["rows_parsed"] == 2
    assert "SCHEDULE_FIELDS_MISSING" not in result["warnings"]
    assert result["complete"] is True
    assert result["status"] == "ok"


def test_the_unscheduled_row_is_still_returned_with_its_identity():
    result = parse_semester_schedule(_page([UNSCHEDULED]))
    row = result["schedule"][0]
    assert row["course_code"] == "10002" and row["section"] == "O"
    assert row["title"] == "EXAMPLE RESEARCH"
    assert row["times"] is None


@pytest.mark.parametrize("blank", ["", "TBA", "tba", "N/A", "-", "  "])
def test_every_unscheduled_spelling_is_recognised(blank):
    row = _course("EXAMPLE RESEARCH", "10002 O", "Teacher", blank, blank, blank)
    result = parse_semester_schedule(_page([row]))
    assert "SCHEDULE_FIELDS_MISSING" not in result["warnings"]


def test_a_partly_filled_meeting_pattern_is_still_reported():
    """The shape a real extraction failure takes: some cells read, others empty."""
    broken = _course("EXAMPLE LECTURE", "10001 A", "Teacher", "T Th", "", "EX 1")
    result = parse_semester_schedule(_page([broken]))
    assert "SCHEDULE_FIELDS_MISSING" in result["warnings"]
    assert result["complete"] is False


def test_a_missing_title_is_still_reported():
    row = _course("", "10001 A", "Teacher", "T Th", "11:00AM", "EX 1")
    result = parse_semester_schedule(_page([row]))
    assert "SCHEDULE_FIELDS_MISSING" in result["warnings"]


def test_a_missing_instructor_is_still_reported():
    row = _course("EXAMPLE LECTURE", "10001 A", "", "TBA", "", "TBA TBA")
    result = parse_semester_schedule(_page([row]))
    assert "SCHEDULE_FIELDS_MISSING" in result["warnings"]
