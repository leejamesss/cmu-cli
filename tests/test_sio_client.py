"""Synthetic snapshots use only labels verified in the SIO editing skill.

Transport fixtures model the publicly observed redirect chain, not a private API.
"""

import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from cmu_cli import sio_client as sio
from cmu_cli.web_session import configured_session


def snapshot(text="Plan Course Schedule\nUnits Scheduled :: 18.0", **kwargs):
    return sio.parse_snapshot(
        url=kwargs.get("url", sio.PLAN_URL),
        title=kwargs.get("title", sio.PAGE_TITLE),
        text=text,
    )


def test_plan_units_not_enrollment():
    row = snapshot()
    assert row["planned_units"] == "18.0"
    assert row["view"] == "planned"
    assert row["status"] == "partial"
    assert row["complete"] is False
    for field in (
        "registered_courses",
        "planned_courses",
        "schedule",
        "waitlist",
        "term",
    ):
        assert row[field] is None
    assert row["provenance"] == {
        "url": sio.PLAN_URL,
        "method": "user_supplied_visible_text",
    }


def test_zero_plan_is_not_empty_registered_schedule():
    row = snapshot("Plan Course Schedule\nUnits Scheduled :: 0.0")
    assert row["planned_units"] == "0.0"
    assert row["registered_courses"] is None
    assert not row["complete"]


def test_registration_route_does_not_parse_plan_labels():
    row = snapshot(url=sio.REGISTRATION_URL)
    assert row["view"] == "registration"
    assert row["planned_units"] is None
    assert row["status"] == "discovery_required"


@pytest.mark.parametrize(
    "url",
    [
        "http://s3.andrew.cmu.edu/sio/#schedule-plan",
        "https://s3.andrew.cmu.edu.evil.test/sio/#schedule-plan",
        "https://user@s3.andrew.cmu.edu/sio/#schedule-plan",
        "https://s3.andrew.cmu.edu:444/sio/#schedule-plan",
        "https://s3.andrew.cmu.edu/sio/?token=secret#schedule-plan",
        "https://s3.andrew.cmu.edu/sio/#unknown",
        "https://s3.andrew.cmu.edu/other/#schedule-plan",
        "https://login.cmu.edu/",
    ],
)
def test_snapshot_url_rejected(url):
    with pytest.raises(sio.SIOError):
        snapshot(url=url)


@pytest.mark.parametrize(
    "text",
    [
        "Plan Course Schedule",
        "Plan Course Schedule\nUnits Scheduled :: -2",
        "Plan Course Schedule\nUnits Scheduled :: 2e9",
        "Plan Course Schedule\nUnits Scheduled :: 2.0garbage",
        "Plan Course Schedule\nUnits Scheduled :: 2.0\nUnits Scheduled :: 3.0",
        "Plan Course Schedule\nUnits Scheduled :: NaN",
    ],
)
def test_missing_ambiguous_malformed_units(text):
    row = snapshot(text)
    assert row["planned_units"] is None
    assert row["status"] == "discovery_required"


def test_heading_and_identity_required():
    assert snapshot("Units Scheduled :: 18.0")["planned_units"] is None
    assert snapshot(title="Web Login")["planned_units"] is None


def test_no_raw_personal_text_in_output():
    row = snapshot("Private Person\nPlan Course Schedule\nUnits Scheduled :: 18.0")
    assert "Private Person" not in str(row)


def test_snapshot_size_limit(monkeypatch):
    monkeypatch.setattr(sio, "MAX_DOCUMENT_BYTES", 10)
    with pytest.raises(sio.SIOError):
        snapshot()


def response(status, location=None, content=b""):
    value = Mock(status_code=status)
    value.headers = {"Location": location} if location else {}
    value.iter_content.return_value = [content]
    return value


def session_for(monkeypatch, responses):
    session = configured_session()
    session.get = Mock(side_effect=responses)
    monkeypatch.setattr(sio, "configured_session", lambda: session)
    return session


def test_public_observed_redirect_chain_stops_before_login(monkeypatch):
    first = response(302, "/sio/s3Login")
    second = response(302, "https://login.cmu.edu/idp/profile/SAML2/Redirect/SSO")
    session = session_for(monkeypatch, [first, second])
    row = sio.SIOClient().probe()
    assert row["status"] == "auth_redirect_blocked"
    assert [call.args[0] for call in session.get.call_args_list] == [
        sio.SIO_URL,
        "https://s3.andrew.cmu.edu/sio/s3Login",
    ]
    assert session.get.call_count == 2
    first.close.assert_called_once()
    second.close.assert_called_once()
    for call in session.get.call_args_list:
        assert call.kwargs["allow_redirects"] is False


def test_html_200_is_not_authenticated_course_data(monkeypatch):
    value = response(200, content=b"<html>SPA shell or login</html>")
    session_for(monkeypatch, [value])
    row = sio.SIOClient().probe()
    assert row["status"] == "discovery_required"
    assert row["registered_courses"] is None
    assert not row["complete"]
    value.close.assert_called_once()


@pytest.mark.parametrize(
    "status, expected",
    [(401, "login_required"), (403, "login_required"), (500, "unavailable")],
)
def test_http_errors(monkeypatch, status, expected):
    value = response(status)
    session_for(monkeypatch, [value])
    assert sio.SIOClient().probe()["status"] == expected
    value.close.assert_called_once()


def test_anonymous_default_never_reads_cookies(monkeypatch):
    session_for(monkeypatch, [response(200)])
    loader = Mock(side_effect=AssertionError("must not load cookies"))
    monkeypatch.setattr(sio, "browser_cookie_session", loader)
    sio.SIOClient().probe()
    loader.assert_not_called()


def test_explicit_auth_is_forwarded_without_construction_side_effect(monkeypatch):
    session = session_for(monkeypatch, [response(200)])
    loader = Mock(return_value=session)
    monkeypatch.setattr(sio, "browser_cookie_session", loader)
    options = {
        "enabled": True,
        "hosts": ["s3.andrew.cmu.edu"],
        "cookie_file": "/synthetic/Cookies",
        "browser": "edge",
    }
    client = sio.SIOClient(browser_auth=options)
    loader.assert_not_called()
    client.probe()
    loader.assert_called_once_with("s3.andrew.cmu.edu", options)


def test_disabled_auth_fails_closed():
    with pytest.raises(sio.SIOError, match="session or transport unavailable"):
        sio.SIOClient(browser_auth={"enabled": False}).probe()


def synthetic_page(history=False):
    labels = (
        ["Course", "Data Entry By", "On Date", "Removed By", "Off Date", "Confirm Date"]
        if history
        else ["Course", "Instructor", "Dates", "Times", "Bldg/Room"]
    )
    values = (
        ["99999 Z", "Synthetic actor", "Jan 2", "Synthetic actor", "Jan 3", ""]
        if history
        else [
            "<div>Example Studies</div><div>99999 Z</div>",
            '<div class="display-block">Example Teacher</div>',
            "Jan 1 - May 1",
            "MW 10:00 - 11:00",
            "EX 100",
        ]
    )
    heading = "Waitlist History" if history else "Semester Schedule"
    table = "schedule-waitlist-tbl" if history else "course-list-tbl"
    select = "semesters-select" if history else "semester-code-select"
    headers = "".join(
        f'<th data-title="{x}"><div class="grid-hdr">{x}</div></th>' for x in labels
    )
    cells = "".join(
        f'<td data-title="{k}">{v}</td>' for k, v in zip(labels, values, strict=True)
    )
    return f'<title>CMU Student Information Online</title><h1>{heading}</h1><select id="{select}"><option value="X99" selected>Example Term</option></select><table class="{table}"><tr>{headers}</tr><tr>{cells}</tr></table>'


def test_semester_structure_and_no_enrollment_inference():
    result = sio.parse_semester_schedule(synthetic_page())
    assert result["complete"] and result["rows_seen"] == 1
    assert result["schedule"][0] == {
        "course_code": "99999",
        "section": "Z",
        "title": "Example Studies",
        "instructors": ["Example Teacher"],
        "dates": "Jan 1 - May 1",
        "times": "MW 10:00 - 11:00",
        "building_room": "EX 100",
    }
    assert result["term_code"] == "X99"
    assert result["registered_courses"] is None


def test_history_is_not_current_queue():
    result = sio.parse_waitlist_history(synthetic_page(True))
    assert result["complete"]
    assert result["waitlist"] is None
    assert result["waitlist_history"][0]["confirm_date"] is None
    assert result["waitlist_history"][0]["off_date"] == "Jan 3"
    assert sio.parse_waitlist_history(synthetic_page())["waitlist_history"] is None


@pytest.mark.parametrize(
    "parser", [sio.parse_semester_schedule, sio.parse_waitlist_history]
)
def test_shell_is_not_empty_and_size_limit(parser, monkeypatch):
    result = parser("<title>CMU Student Information Online</title>")
    assert not result["complete"] and result["schedule"] is None
    monkeypatch.setattr(sio, "MAX_DOCUMENT_BYTES", 1)
    with pytest.raises(sio.SIOError):
        parser("too large")


def test_malformed_course_and_ambiguous_term():
    result = sio.parse_semester_schedule(
        synthetic_page().replace("99999 Z", "99-999 Z")
    )
    assert result["rows_seen"] == 1 and result["rows_parsed"] == 0
    assert not result["complete"]
    page = (
        synthetic_page()
        .replace(" selected", "")
        .replace("</select>", '<option value="Y99">Other Term</option></select>')
    )
    assert sio.parse_semester_schedule(page)["term"] is None


@pytest.mark.parametrize(
    "method,url,history",
    [
        ("semester_schedule", sio.SEMESTER_SCHEDULE_URL, False),
        ("waitlist_history", sio.WAITLIST_HISTORY_URL, True),
    ],
)
def test_authenticated_read_only_methods(monkeypatch, method, url, history):
    value = response(200, content=synthetic_page(history).encode())
    session = session_for(monkeypatch, [value])
    loader = Mock(return_value=session)
    monkeypatch.setattr(sio, "browser_cookie_session", loader)
    result = getattr(sio.SIOClient(browser_auth={"enabled": True}), method)()
    assert result["complete"]
    assert session.get.call_args.args == (url,)
    assert session.get.call_args.kwargs["params"] is None
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert result["provenance"]["method"] == "https_get"
    value.close.assert_called_once()


def test_new_methods_require_explicit_auth_and_block_redirect(monkeypatch):
    loader = Mock(side_effect=AssertionError("no cookies"))
    monkeypatch.setattr(sio, "browser_cookie_session", loader)
    assert sio.SIOClient().semester_schedule()["status"] == "login_required"
    loader.assert_not_called()
    session = session_for(monkeypatch, [response(302, "https://other.test/private")])
    monkeypatch.setattr(sio, "browser_cookie_session", lambda *args: session)
    assert (
        sio.SIOClient(browser_auth={"enabled": True}).waitlist_history()["status"]
        == "auth_redirect_blocked"
    )
    assert session.get.call_count == 1


def test_transport_error_sanitized(monkeypatch):
    session_for(monkeypatch, [requests.ConnectionError("secret diagnostics")])
    with pytest.raises(sio.SIOError) as exc:
        sio.SIOClient().probe()
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize(
    "action,url,history",
    [
        ("schedule", sio.SEMESTER_SCHEDULE_URL, False),
        ("waitlist-history", sio.WAITLIST_HISTORY_URL, True),
    ],
)
@pytest.mark.parametrize("redirect", [False, True])
def test_documented_sio_only_config_cli(
    monkeypatch, tmp_path, capsys, action, url, history, redirect
):
    """Exercise the documented config through real CLI dispatch; no real cookies."""
    from cmu_cli import cli
    from cmu_cli.models import load_provider_config

    doc = (Path(__file__).resolve().parents[1] / "docs" / "sio.md").read_text()
    raw = json.loads(re.search(r"```json\n(.*?)\n```", doc, re.DOTALL).group(1))
    assert set(raw) == {"browser_auth"}
    path = tmp_path / "sio.json"
    path.write_text(json.dumps(raw))
    assert load_provider_config(path).browser_auth == raw["browser_auth"]
    value = (
        response(302, "https://login.cmu.edu/idp/profile/SAML2/Redirect/SSO")
        if redirect
        else response(200, content=synthetic_page(history).encode())
    )
    session = session_for(monkeypatch, [value])
    loader = Mock(return_value=session)
    monkeypatch.setattr(sio, "browser_cookie_session", loader)
    monkeypatch.setattr(
        "sys.argv", ["cmu-cli", "--config", str(path), "sio", action, "--json"]
    )
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == (3 if redirect else 0)
    output = json.loads(capsys.readouterr().out)
    result = output["data"]
    loader.assert_called_once_with("s3.andrew.cmu.edu", raw["browser_auth"])
    assert session.get.call_count == 1
    assert session.get.call_args.args == (url,)
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.kwargs["params"] is None
    if redirect:
        assert result["status"] == "auth_redirect_blocked"
        assert not result["complete"]
        assert output["warnings"]
    else:
        assert output["status"] == result["status"] == "ok"
        assert result["rows_seen"] == result["rows_parsed"] == 1
        assert result["complete"]
        assert result["provenance"]["method"] == "https_get"
        assert output["warnings"] == result["warnings"] == []
    value.close.assert_called_once()
