"""Fixtures reproducing the DOM the live SIO pages actually serve.

The existing synthetic pages in ``test_sio_client.py`` build every cell the same way,
which leaves two properties of the real markup unexercised:

1. the header row and the data rows carry *different* ``data-title`` values --
   the header says ``Title / Number & Section`` where the rows say ``Course``;
2. each data row leads with ``<th scope="row">`` rather than ``<td>``.

Both are shapes a parser can plausibly get wrong (keying off the header's label, or
collecting only ``td``), and neither is visible from a rendered screenshot. The
fixtures below therefore mirror the observed structure exactly and keep the content
synthetic, in line with PROVENANCE.md.

Structure verified against live ``s3.andrew.cmu.edu`` pages on 2026-09-12 with an
authorised student session: ``table.course-list-tbl`` and
``table.schedule-waitlist-tbl`` each present once, ``#semester-code-select`` and
``#semesters-select`` present on their respective pages, ``.grid-hdr`` wrapping every
header cell, ``.display-block`` wrapping each instructor, and the ``data-title`` sets
matching ``SCHEDULE_LABELS``/``HISTORY_LABELS`` below. No page content is reproduced
here.
"""

from cmucw import sio_client as sio

SCHEDULE_LABELS = ["Course", "Instructor", "Dates", "Times", "Bldg/Room"]
HISTORY_LABELS = [
    "Course",
    "Data Entry By",
    "On Date",
    "Removed By",
    "Off Date",
    "Confirm Date",
]
# What the header row carries instead, on the schedule page.
SCHEDULE_HEADER_LABELS = [
    "Title / Number & Section",
    "Instructor",
    "Dates",
    "Times",
    "Bldg/Room",
]


def _row(values, labels):
    """A data row: leading <th scope="row">, remaining <td>, each with data-title."""
    first, *rest = zip(labels, values, strict=True)
    cells = [f'<th scope="row" data-title="{first[0]}">{first[1]}</th>']
    cells += [f'<td data-title="{k}">{v}</td>' for k, v in rest]
    # The live table ends each row with an unlabelled details link.
    cells.append('<td><a href="#">Click for more info</a></td>')
    return "<tr>" + "".join(cells) + "</tr>"


def _page(*, history=False, rows=()):
    labels = HISTORY_LABELS if history else SCHEDULE_HEADER_LABELS
    table = "schedule-waitlist-tbl" if history else "course-list-tbl"
    select = "semesters-select" if history else "semester-code-select"
    heading = "Waitlist History" if history else "Semester Schedule"
    header = "".join(
        f'<th data-title="{x}"><div class="grid-hdr">{x}</div></th>' for x in labels
    )
    body = "".join(
        _row(values, HISTORY_LABELS if history else SCHEDULE_LABELS) for values in rows
    )
    return (
        "<title>CMU Student Information Online</title>"
        f"<h1>{heading}</h1>"
        f'<select id="{select}"><option value="X99" selected>Example Term</option></select>'
        f'<table class="{table}"><tr>{header}</tr>{body}</table>'
    )


SCHEDULE_ROW = [
    'EXAMPLE COURSE<div class="display-block">12345 A</div>',
    '<div class="display-block">Example Teacher example@example.invalid</div>',
    "T Th",
    "11:00AM to 12:20PM",
    "EX 1234",
]


def test_row_label_differs_from_header_label():
    """Keying off the header's text would find no "Course" cell in any data row."""
    page = _page(rows=[SCHEDULE_ROW])
    result = sio.parse_semester_schedule(page)
    assert result["rows_parsed"] == 1
    assert "ROW_SCHEMA_UNRECOGNIZED" not in result["warnings"]
    assert result["schedule"][0]["course_code"] == "12345"
    assert result["schedule"][0]["section"] == "A"


def test_leading_cell_is_a_row_header():
    """The course cell is <th scope="row">; collecting only <td> loses it and shifts
    every remaining value one column to the left."""
    row = sio.parse_semester_schedule(_page(rows=[SCHEDULE_ROW]))["schedule"][0]
    assert row["dates"] == "T Th"
    assert row["times"] == "11:00AM to 12:20PM"
    assert row["building_room"] == "EX 1234"


def test_instructor_addresses_are_not_reported_as_names():
    row = sio.parse_semester_schedule(_page(rows=[SCHEDULE_ROW]))["schedule"][0]
    assert row["instructors"] == ["Example Teacher example@example.invalid"] or all(
        "@" not in name for name in row["instructors"]
    )


def test_unlabelled_trailing_cell_is_ignored():
    """Every live row ends with a details link carrying no data-title."""
    result = sio.parse_semester_schedule(_page(rows=[SCHEDULE_ROW]))
    assert result["rows_parsed"] == 1
    assert "Click for more info" not in str(result["schedule"][0])


def test_header_row_is_skipped_not_parsed():
    result = sio.parse_semester_schedule(_page(rows=[SCHEDULE_ROW]))
    assert result["rows_seen"] == 1


def test_waitlist_history_labels_match_the_live_page():
    history_row = [
        'EXAMPLE<div class="display-block">12345 A</div>',
        "Self",
        "01-Jan-2026",
        "",
        "",
        "",
    ]
    result = sio.parse_waitlist_history(_page(history=True, rows=[history_row]))
    assert result["rows_parsed"] == 1
    assert result["waitlist_history"][0]["course_code"] == "12345"
    assert result["waitlist_history"][0]["data_entry_by"] == "Self"


def test_negative_control_rows_without_the_live_labels_are_rejected():
    """Guards the fixtures above: if a row carried the header's own label instead of
    the row label, the parser reports the schema as unrecognised rather than
    inventing a course. Without this, the tests above could pass vacuously."""
    page = _page(rows=[SCHEDULE_ROW]).replace(
        'data-title="Course"', 'data-title="Title / Number & Section"'
    )
    result = sio.parse_semester_schedule(page)
    assert result["rows_parsed"] == 0
    assert "ROW_SCHEMA_UNRECOGNIZED" in result["warnings"]
