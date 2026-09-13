"""The SSO path must stay opt-in, origin-bound, and invisible when unconfigured.

Reaching SIO needs a browser engine, which the default transport deliberately does
not use. So the property under test is not "it fetches" -- that needs a live account
-- but that nothing changes for anyone who has not asked for it, and that the
guarantees the default path provides are kept where they still apply.

No test here launches a browser or needs a session.
"""

import json
import os
import stat

import pytest

from cmu_cli import sso_session
from cmu_cli.sio_client import SIOClient
from cmu_cli.sso_session import (
    NeedsLogin,
    SsoError,
    SsoSession,
    at_login_wall,
    landed,
    session_from_config,
)

STATE = "/tmp/example-session.json"


# --- opt-in ---------------------------------------------------------------------


def test_no_configuration_means_no_session():
    assert session_from_config(None) is None
    assert session_from_config({}) is None


def test_the_block_must_be_explicitly_enabled():
    assert session_from_config({"state_file": STATE}) is None
    assert session_from_config({"enabled": False, "state_file": STATE}) is None
    assert session_from_config({"enabled": "yes", "state_file": STATE}) is None


def test_an_enabled_block_needs_an_absolute_state_file():
    with pytest.raises(SsoError):
        session_from_config({"enabled": True, "state_file": "relative.json"})
    with pytest.raises(SsoError):
        session_from_config({"enabled": True})


def test_an_enabled_block_builds_a_session():
    session = session_from_config({"enabled": True, "state_file": STATE})
    assert isinstance(session, SsoSession)


# --- origin discipline ----------------------------------------------------------


def test_a_host_outside_the_allowlist_is_refused():
    session = SsoSession(STATE, hosts=["s3.andrew.cmu.edu"])
    with pytest.raises(SsoError):
        session._check_host("https://example.invalid/page")


def test_plain_http_is_refused():
    session = SsoSession(STATE, hosts=["s3.andrew.cmu.edu"])
    with pytest.raises(SsoError):
        session._check_host("http://s3.andrew.cmu.edu/sio/")


def test_the_allowed_host_passes():
    session = SsoSession(STATE, hosts=["s3.andrew.cmu.edu"])
    assert session._check_host("https://s3.andrew.cmu.edu/sio/") == "s3.andrew.cmu.edu"


# --- landing checks -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://login.cmu.edu/idp/profile/SAML2/Redirect/SSO",
        "https://api-abc.duosecurity.com/frame",
        "https://duo.com/anything",
    ],
)
def test_login_hosts_are_recognised_as_the_wall(url):
    assert at_login_wall(url)


def test_a_blank_tab_is_not_a_successful_landing():
    """The negative form -- "not a login host" -- is satisfied by about:blank."""
    assert not at_login_wall("about:blank")
    assert landed("about:blank", "s3.andrew.cmu.edu") is False


def test_landing_requires_the_exact_host():
    assert landed("https://s3.andrew.cmu.edu/sio/", "s3.andrew.cmu.edu") is True
    assert landed("https://evil.invalid/sio/", "s3.andrew.cmu.edu") is False
    assert landed("https://login.cmu.edu/x", "login.cmu.edu") is False


# --- stored state ---------------------------------------------------------------


def test_saved_state_is_owner_readable_only(tmp_path):
    class FakeContext:
        def storage_state(self):
            return {"cookies": [{"name": "a"}, {"name": "b"}]}

    path = tmp_path / "nested" / "session.json"
    assert sso_session.save_state(FakeContext(), path) == 2
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text())["cookies"]


def test_fetching_without_a_saved_session_asks_for_login(tmp_path):
    session = SsoSession(tmp_path / "absent.json", hosts=["s3.andrew.cmu.edu"])
    with pytest.raises(NeedsLogin):
        session.fetch("https://s3.andrew.cmu.edu/sio/")


# --- the SIO client ---------------------------------------------------------------


SCHEDULE_URL = "https://s3.andrew.cmu.edu/sio/mpa/semesterschedule"


def test_an_unconfigured_client_keeps_the_existing_transport():
    """Nobody who has not opted in sees any change."""
    result = SIOClient().semester_schedule()
    assert result["status"] == "login_required"
    assert result["warnings"] == ["EXPLICIT_BROWSER_AUTH_REQUIRED"]
    assert result["provenance"]["method"] == "not_requested"


def test_a_configured_session_reads_through_it_and_says_so():
    page = (
        "<title>CMU Student Information Online</title><h1>Semester Schedule</h1>"
        '<select id="semester-code-select"><option value="X99" selected>T</option>'
        '</select><table class="course-list-tbl"></table>'
    )

    class FakeSession:
        def fetch(self, url, timeout_s=60):
            assert url == SCHEDULE_URL
            return page

    result = SIOClient(sso=FakeSession()).semester_schedule()
    assert result["provenance"]["method"] == "sso_page_load"
    assert result["view"] == "semester_schedule"


def test_an_expired_session_is_login_required_not_a_crash():
    class Expired:
        def fetch(self, url, timeout_s=60):
            raise NeedsLogin()

    result = SIOClient(sso=Expired()).semester_schedule()
    assert result["status"] == "login_required"
    assert result["warnings"] == ["SSO_LOGIN_REQUIRED"]


def test_a_transport_failure_stays_sanitized():
    class Broken:
        def fetch(self, url, timeout_s=60):
            raise SsoError("https://internal.invalid/secret?token=abc")

    from cmu_cli.sio_client import SIOError

    with pytest.raises(SIOError) as caught:
        SIOClient(sso=Broken()).semester_schedule()
    assert "internal.invalid" not in str(caught.value)


def test_playwright_is_not_imported_until_a_session_is_used():
    """Installing the extra must not be required to run anything else.

    Checked on the module's own syntax tree rather than sys.modules, which another
    test could have populated, and without a subprocess, which the offline fixture
    forbids.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sso_session))
    top_level = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
    names = set()
    for node in top_level:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif node.module:
            names.add(node.module.split(".")[0])
    assert "playwright" not in names, f"top-level imports: {sorted(names)}"


def test_the_type_checking_import_is_not_a_runtime_one():
    """The annotation import is guarded, so it costs nothing at run time."""
    import inspect

    source = inspect.getsource(sso_session)
    guarded = source.index("if TYPE_CHECKING:")
    assert source.index("from playwright.sync_api import BrowserContext") > guarded
