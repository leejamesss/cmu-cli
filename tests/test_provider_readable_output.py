"""Ed and SIO must print something a person can read without --json.

Every other command prints ◆ lines or a Rich table. These two dumped
json.dumps(result) to stdout in their human path -- and a *different*, unversioned
shape from the one --json returns. All content here is synthetic.
"""

from cmu_cli.cli import ed_lines, sio_lines

THREAD = {
    "id": 9,
    "number": 42,
    "title": "HW1 clarification",
    "category": "Homework",
    "created_at": "2026-09-12T13:00:00+00:00",
    "reply_count": 2,
}


def _joined(lines):
    return "\n".join(lines)


def test_ed_courses_are_listed_not_dumped():
    rows = [
        {
            "course": {"id": 3, "code": "15-123", "name": "Example Course"},
            "role": {"role": "student"},
        }
    ]
    text = _joined(ed_lines("courses", rows))
    assert "15-123" in text and "student" in text and "Ed 3" in text
    assert not text.lstrip().startswith("{")


def test_ed_threads_are_listed_not_dumped():
    text = _joined(ed_lines("threads", {"items": [THREAD], "complete": True}))
    assert "#42" in text and "HW1 clarification" in text and "Homework" in text
    assert not text.lstrip().startswith("{")


def test_a_partial_read_surfaces_its_reason():
    """The actionable sentence existed already; it was buried in a JSON dump."""
    partial = {
        "partial_listing": {
            "items": [],
            "complete": False,
            "reason": "Set CMU_CLI_ED_TOKEN to an Ed API token",
        }
    }
    text = _joined(ed_lines("threads", partial))
    assert "CMU_CLI_ED_TOKEN" in text
    assert not text.lstrip().startswith("{")


def test_an_empty_listing_says_so():
    assert ed_lines("threads", {"items": []}) == ["Nothing returned."]


def test_a_thread_detail_shows_its_header():
    text = _joined(ed_lines("thread", {**THREAD, "document": "<p>Use the key.</p>"}))
    assert "#42" in text and "Use the key." in text and "<p>" not in text


def test_missing_ed_fields_do_not_raise():
    text = _joined(ed_lines("threads", {"items": [{}]}))
    assert text  # a row with no recognised fields still renders one line


def test_sio_schedule_rows_are_listed_not_dumped():
    result = {
        "view": "semester_schedule",
        "status": "ok",
        "term": "Example Term",
        "rows_seen": 1,
        "rows_parsed": 1,
        "warnings": [],
        "provenance": {"url": "https://sio.example.invalid/schedule"},
        "schedule": [
            {
                "course_code": "12345",
                "section": "A",
                "title": "EXAMPLE COURSE",
                "instructors": ["Example Teacher"],
                "dates": "T Th",
                "times": "11:00AM to 12:20PM",
                "building_room": "EX 1234",
            }
        ],
    }
    text = _joined(sio_lines(result))
    assert "EXAMPLE COURSE" in text and "EX 1234" in text and "Example Teacher" in text
    assert "rows parsed: 1 of 1" in text
    assert not text.lstrip().startswith("{")


def test_sio_empty_view_is_not_claimed_as_an_empty_schedule():
    result = {
        "view": "semester_schedule",
        "status": "login_required",
        "warnings": ["EXPLICIT_BROWSER_AUTH_REQUIRED"],
        "schedule": None,
    }
    text = _joined(sio_lines(result))
    assert "not a verified empty result" in text
    assert "EXPLICIT_BROWSER_AUTH_REQUIRED" in text


def test_sio_probe_does_not_claim_missing_schedule_rows():
    """The readiness probe has no row view, so "no rows parsed" would mislead."""
    result = {
        "status": "auth_redirect_blocked",
        "view": None,
        "warnings": ["LOGIN_OR_ORIGIN_REDIRECT_BLOCKED"],
        "provenance": {"url": "https://sio.example.invalid/"},
    }
    text = _joined(sio_lines(result))
    assert "No rows parsed" not in text
    assert "LOGIN_OR_ORIGIN_REDIRECT_BLOCKED" in text


def test_waitlist_history_keeps_blank_events_visible():
    result = {
        "view": "waitlist_history",
        "status": "ok",
        "term": "Example Term",
        "waitlist_history": [
            {"course_code": "12345", "section": "A", "on_date": "01/05/2026"}
        ],
    }
    text = _joined(sio_lines(result))
    assert "01/05/2026" in text and "off: none" in text
