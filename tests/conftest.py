"""Block unmocked Requests and subprocess.run; not a general OS sandbox."""

import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import subprocess

    import requests

    def forbidden(*args, **kwargs):
        raise AssertionError("Unmocked external access is forbidden in offline tests")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    for name in [
        "CMU_CLI_CANVAS_TOKEN",
        "CMU_CLI_ALLOW_BROWSER_COOKIES",
        "CMU_CLI_EDGE_COOKIE_FILE",
        "CMU_CLI_COOKIE_HOSTS",
        "CMU_CLI_CONFIG",
    ]:
        monkeypatch.delenv(name, raising=False)
