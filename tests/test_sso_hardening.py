"""Synthetic regressions; no browser/account/profile access or protocol success claim."""

import contextlib
import json
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from cmu_cli import cli
from cmu_cli import sso_session as sso
from cmu_cli import sso_state as state
from cmu_cli.models import load_config, load_provider_config
from cmu_cli.sio_client import SEMESTER_SCHEDULE_URL, SIOClient, SIOError

URL = SEMESTER_SCHEDULE_URL
COOKIE = {
    "name": "_shibsession_synthetic",
    "value": "SYNTHETIC_SECRET",
    "domain": sso.SIO_HOST,
    "path": "/",
    "secure": True,
}


def test_registration_still_requires_explicit_path(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cmu-cli", "auth", "check-registration"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "requires --registration" in capsys.readouterr().err


def test_config_retains_pre_sso_positional_field_order(tmp_path):
    from cmu_cli.models import Config

    config = Config(
        "https://canvas.example.edu",
        tmp_path,
        (),
        "current",
        "America/New_York",
        None,
        "https://edstem.org/api",
    )
    assert config.ed_api_base_url == "https://edstem.org/api"
    assert config.sso is None


def candidate_state():
    return {"cookies": [dict(COOKIE)], "origins": []}


def test_config_keywords_and_provider_only_login(tmp_path, monkeypatch, capsys):
    block = {"enabled": True, "state_file": str(tmp_path / "session.json")}
    config = tmp_path / "config.json"
    raw = {
        "canvas_base_url": "https://canvas.example.edu",
        "storage_root": "materials",
        "courses": [],
        "sso": block,
        "ed_api_base_url": "https://edstem.org/api",
    }
    config.write_text(json.dumps(raw))
    loaded = load_config(config)
    assert loaded.sso == block
    assert loaded.ed_api_base_url == raw["ed_api_base_url"]
    config.write_text(json.dumps({"sso": block}))
    assert load_provider_config(config).sso == block
    called = []
    monkeypatch.setattr(
        sso.SsoSession, "login", lambda self, url: called.append(url) or 1
    )
    monkeypatch.setattr(
        sys, "argv", ["cmu-cli", "--config", str(config), "auth", "sso-login", "--json"]
    )
    cli.main()
    assert called == ["https://s3.andrew.cmu.edu/sio/"]
    assert "signed_in" in capsys.readouterr().out


@pytest.mark.parametrize(
    "change",
    [
        {"domain": "other.invalid"},
        {"domain": ".cmu.edu"},
        {"domain": ".s3.andrew.cmu.edu"},
        {"domain": "login.cmu.edu"},
        {"path": "/other"},
        {"secure": False},
        {"name": "tracking"},
        {"name": "JSESSIONID", "path": "/"},
    ],
)
def test_state_excludes_unrelated_cookies_and_origin_storage(change):
    bad = dict(COOKIE, **change)
    result = state.minimal_state(
        {
            "cookies": [COOKIE, bad],
            "origins": [
                {
                    "origin": "https://other.invalid",
                    "localStorage": [{"name": "secret", "value": "SYNTHETIC_LOCAL"}],
                }
            ],
        }
    )
    assert result == candidate_state()


def test_scope_is_reapplied_on_replay(tmp_path):
    path = tmp_path / "session.json"
    raw = {
        "cookies": [COOKIE, dict(COOKIE, domain="login.cmu.edu")],
        "origins": [{"origin": "https://other.invalid", "localStorage": []}],
    }
    path.write_text(json.dumps(raw))
    path.chmod(0o600)
    assert state.read_state(path) == candidate_state()


def test_permissions_exist_before_first_byte_and_old_tmp_symlink_is_untouched(
    tmp_path, monkeypatch
):
    path = tmp_path / "session.json"
    victim = tmp_path / "victim"
    victim.write_text("KEEP")
    old_temp = tmp_path / "session.json.tmp"
    old_temp.symlink_to(victim)
    dump = json.dump
    inspected = []

    def check_dump(value, handle):
        info = os.fstat(handle.fileno())
        inspected.append(stat.S_IMODE(info.st_mode))
        assert info.st_size == 0
        assert stat.S_IMODE(info.st_mode) == 0o600
        return dump(value, handle)

    monkeypatch.setattr(state.json, "dump", check_dump)
    previous = os.umask(0o022)
    try:
        assert state.write_state(candidate_state(), path) == 1
    finally:
        os.umask(previous)
    assert inspected == [0o600]
    assert victim.read_text() == "KEEP"
    assert old_temp.is_symlink()
    assert not list(tmp_path.glob(".sio-state-*"))


@pytest.mark.parametrize(
    "kind", ["target-link", "parent-link", "hardlink", "public-parent", "public-file"]
)
def test_unsafe_storage_is_rejected_without_mutating_victim(tmp_path, kind):
    path = tmp_path / "session.json"
    victim = tmp_path / "victim"
    victim.write_text("KEEP")
    victim.chmod(0o600)
    if kind == "target-link":
        path.symlink_to(victim)
    elif kind == "parent-link":
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        path = tmp_path / "link" / "session.json"
    elif kind == "hardlink":
        os.link(victim, path)
    elif kind == "public-parent":
        (tmp_path / "public").mkdir(mode=0o755)
        path = tmp_path / "public" / "session.json"
    else:
        path.write_text("KEEP")
        path.chmod(0o644)
    with pytest.raises(state.StateError):
        state.write_state(candidate_state(), path)
    assert victim.read_text() == "KEEP"
    assert not list(tmp_path.glob(".sio-state-*"))


@pytest.mark.parametrize("stage", ["dump", "fsync", "replace"])
def test_failed_write_preserves_old_state_and_cleans_temp(tmp_path, monkeypatch, stage):
    path = tmp_path / "session.json"
    state.write_state(candidate_state(), path)
    original = path.read_bytes()

    def fail(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_SECRET")

    monkeypatch.setattr(state.json if stage == "dump" else state.os, stage, fail)
    with pytest.raises(state.StateError) as error:
        state.write_state(candidate_state(), path)
    assert "SYNTHETIC_SECRET" not in str(error.value)
    assert error.value.__suppress_context__
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".sio-state-*"))


def test_exclusive_collision_does_not_unlink_existing_file(tmp_path, monkeypatch):
    collision = tmp_path / ".sio-state-fixed"
    collision.write_text("KEEP")
    monkeypatch.setattr(state.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    with pytest.raises(state.StateError):
        state.write_state(candidate_state(), tmp_path / "session.json")
    assert collision.read_text() == "KEEP"


def test_missing_read_does_not_create_directories(tmp_path):
    parent = tmp_path / "absent"
    with pytest.raises(FileNotFoundError):
        state.read_state(parent / "session.json")
    assert not parent.exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://s3.andrew.cmu.edu/sio/",
        "https://user:pass@s3.andrew.cmu.edu/sio/",
        "https://s3.andrew.cmu.edu:8443/sio/",
        "https://s3.andrew.cmu.edu:443/sio/",
        "https://127.0.0.1/private",
        "https://[::1]/private",
        "https://10.0.0.1/",
        "https://s3.andrew.cmu.edu.evil.invalid/sio/",
        "https://other.cmu.edu/sio/",
        "https://s3.andrew.cmu.edu/sio/drop",
        "https://s3.andrew.cmu.edu/sio/?action=drop",
        "https://s3.andrew.cmu.edu/sio/../drop",
        "https://s3.andrew.cmu.edu/sio/%2e%2e/drop",
        "https://s3.andrew.cmu.edu/sio/#write",
        "https://s3.andrew.cmu.edu/\nsio/",
        "https://s3.andrew.cmu.edu\\@evil.invalid/sio/",
        "about:blank",
    ],
)
def test_initial_and_network_boundaries_reject_unsafe_urls(tmp_path, url):
    with pytest.raises(sso.SsoError):
        sso.SsoSession(tmp_path / "state.json")._check_host(url)
    assert not sso.allowed_request(url, "GET", "document", login=True)


@pytest.mark.parametrize(
    "url,method,kind",
    [
        (URL, "POST", "document"),
        (URL, "DELETE", "fetch"),
        (URL, "GET", "script"),
        (URL, "POST", "fetch"),
        ("https://login.cmu.edu/idp/logout", "POST", "document"),
        ("https://api-unknown.duosecurity.com/frame", "GET", "document"),
        ("https://s3.andrew.cmu.edu/sio/register", "GET", "document"),
        ("https://s3.andrew.cmu.edu/Shibboleth.sso/SAML2/POST", "POST", "fetch"),
    ],
)
def test_forms_scripts_and_unknown_mfa_are_not_application_write_escape(
    url, method, kind
):
    assert not sso.allowed_request(url, method, kind, login=True)


@pytest.mark.parametrize(
    "url,method,kind",
    [
        (URL, "GET", "document"),
        ("https://s3.andrew.cmu.edu/sio/s3Login", "GET", "document"),
        (
            "https://login.cmu.edu/idp/profile/SAML2/Redirect/SSO?execution=synthetic",
            "GET",
            "document",
        ),
        (
            "https://login.cmu.edu/idp/profile/SAML2/Redirect/SSO?execution=synthetic",
            "POST",
            "document",
        ),
        ("https://s3.andrew.cmu.edu/Shibboleth.sso/SAML2/POST", "POST", "document"),
        ("https://login.cmu.edu/css/login.css", "GET", "stylesheet"),
    ],
)
def test_observed_or_metadata_documented_sso_transitions_are_allowed(url, method, kind):
    assert sso.allowed_request(url, method, kind, login=True)


class FakeContext:
    def __init__(self, fail=None, final=URL):
        self.fail = fail
        self.closed = False
        self.final = final
        self.page = SimpleNamespace(main_frame=object(), url=final)
        self.page.goto = self.goto
        self.page.wait_for_url = lambda *a, **kw: None
        self.page.content = self.content

    def route(self, pattern, callback):
        self.callback = callback

    def route_web_socket(self, pattern, callback):
        self.socket_callback = callback

    def on(self, event, callback):
        self.popup_callback = callback

    def new_page(self):
        if self.fail == "new_page":
            raise RuntimeError("SYNTHETIC_SECRET")
        return self.page

    def goto(self, *args, **kwargs):
        if self.fail == "goto":
            raise RuntimeError("SYNTHETIC_SECRET")

    def content(self):
        if self.fail == "content":
            raise RuntimeError("SYNTHETIC_SECRET")
        return "<h1>SYNTHETIC</h1>"

    def storage_state(self):
        if self.fail == "storage":
            raise RuntimeError("SYNTHETIC_SECRET")
        return candidate_state()

    def close(self):
        self.closed = True
        if self.fail == "close":
            raise RuntimeError("SYNTHETIC_SECRET")


def install_fake_engine(monkeypatch, fail=None, final=URL):
    context = FakeContext(fail, final)
    browser = SimpleNamespace(closed=False)

    def new_context(**kwargs):
        assert kwargs["service_workers"] == "block"
        assert kwargs["accept_downloads"] is False
        if fail == "new_context":
            raise RuntimeError("SYNTHETIC_SECRET")
        return context

    browser.new_context = new_context
    browser.close = lambda: setattr(browser, "closed", True)

    @contextlib.contextmanager
    def engine():
        if fail == "engine":
            raise RuntimeError("SYNTHETIC_SECRET")
        yield SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: browser))

    monkeypatch.setattr(sso, "_playwright", lambda: engine)
    return context, browser


@pytest.mark.parametrize(
    "stage", ["engine", "new_context", "new_page", "goto", "content", "storage"]
)
def test_browser_failures_are_sanitized_through_library_and_cleanup(
    tmp_path, monkeypatch, stage
):
    path = tmp_path / "state.json"
    state.write_state(candidate_state(), path)
    context, browser = install_fake_engine(monkeypatch, fail=stage)
    with pytest.raises(SIOError) as error:
        SIOClient(sso=sso.SsoSession(path)).semester_schedule()
    assert "SYNTHETIC_SECRET" not in str(error.value)
    assert error.value.__suppress_context__
    if stage != "engine":
        assert browser.closed
    if stage not in {"engine", "new_context"}:
        assert context.closed


@pytest.mark.parametrize(
    "final",
    [
        "http://127.0.0.1/private",
        "https://s3.andrew.cmu.edu/sio/",
        "https://other.invalid/",
        "about:blank",
    ],
)
def test_wrong_final_page_never_returns_body_or_saves(tmp_path, monkeypatch, final):
    path = tmp_path / "state.json"
    state.write_state(candidate_state(), path)
    original = path.read_bytes()
    install_fake_engine(monkeypatch, final=final)
    with pytest.raises(sso.SsoError):
        sso.SsoSession(path).fetch(URL)
    assert path.read_bytes() == original


def test_separate_cleanup_does_not_skip_browser(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    state.write_state(candidate_state(), path)
    context, browser = install_fake_engine(monkeypatch, fail="close")
    assert sso.SsoSession(path).fetch(URL) == "<h1>SYNTHETIC</h1>"
    assert context.closed and browser.closed


def test_routing_revalidates_synthetic_redirect_and_prevents_script_posts(tmp_path):
    session = sso.SsoSession(tmp_path / "state.json")
    context = FakeContext()
    page, blocked = session._guard(context, login=True)
    ledger = []

    def request(url, method="GET", kind="document", frame=None):
        req = SimpleNamespace(
            url=url,
            method=method,
            resource_type=kind,
            frame=page.main_frame if frame is None else frame,
        )

        def fetch(**kwargs):
            assert kwargs["max_redirects"] == 0
            ledger.append((method, url))
            return SimpleNamespace(dispose=lambda: None)

        route = SimpleNamespace(
            request=req, fetch=fetch, fulfill=lambda **kw: None, abort=lambda: None
        )
        context.callback(route)

    request(URL)
    request("https://127.0.0.1/private")
    request(URL, "POST", "fetch")
    request(URL, frame=object())
    assert ledger == [("GET", URL)]
    assert len(blocked) == 3
    closed = []
    context.socket_callback(SimpleNamespace(close=lambda: closed.append(True)))
    context.popup_callback(SimpleNamespace(close=lambda: closed.append(True)))
    assert closed == [True, True]


@pytest.mark.parametrize("stage", ["new_context", "new_page", "goto", "storage"])
def test_login_failure_redaction_and_cleanup(tmp_path, monkeypatch, stage):
    context, browser = install_fake_engine(
        monkeypatch, fail=stage, final="https://s3.andrew.cmu.edu/sio/"
    )
    path = tmp_path / "state.json"
    with pytest.raises(sso.SsoError) as error:
        sso.SsoSession(path).login("https://s3.andrew.cmu.edu/sio/")
    assert "SYNTHETIC_SECRET" not in str(error.value)
    assert not path.exists()
    assert browser.closed
    if stage != "new_context":
        assert context.closed
