"""Offline regressions for release artifacts and first-run setup."""

import io
import json
import re
import sys
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from cmu_cli import cli
from cmu_cli.models import load_config
from scripts.release_check import check_links, check_sdist


def example():
    text = (Path(__file__).resolve().parents[1] / "docs/provider-cli.md").read_text()
    return json.loads(re.search(r"```json\n(.*?)```", text, re.DOTALL)[1])


def test_documented_full_config_offline(tmp_path, monkeypatch, capsys):
    network = Mock(side_effect=AssertionError("network forbidden"))
    monkeypatch.setattr(requests.Session, "send", network)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(example()))
    config = load_config(path)
    assert config.courses[0].canvas_id == 123
    assert config.storage_root == tmp_path / "private-coursework"
    monkeypatch.setattr(
        sys, "argv", ["cmu-cli", "--config", str(path), "config", "validate", "--json"]
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["data"]["valid"]
    network.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        ["sio", "schedule"],
        ["sio", "waitlist-history"],
        ["sio", "probe"],
        ["courses"],
        ["posts"],
    ],
)
@pytest.mark.parametrize("as_json", [False, True])
def test_missing_browser_extra(tmp_path, monkeypatch, capsys, command, as_json):
    monkeypatch.setitem(sys.modules, "browser_cookie3", None)
    monkeypatch.delenv("CMU_CLI_CANVAS_TOKEN", raising=False)
    network = Mock(side_effect=AssertionError("network forbidden"))
    monkeypatch.setattr(requests.Session, "send", network)
    raw = example()
    raw["browser_auth"]["hosts"].append("s3.andrew.cmu.edu")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(
        sys,
        "argv",
        ["cmu-cli", "--config", str(path), *command, *(["--json"] if as_json else [])],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    captured = capsys.readouterr()
    if as_json:
        error = json.loads(captured.out)["error"]
        assert error["code"] == "BROWSER_DEPENDENCY_MISSING"
        message = error["message"]
    else:
        message = captured.err
    assert "BROWSER_DEPENDENCY_MISSING" in captured.out + captured.err
    assert (
        "cmu-cli[browser] @ https://github.com/leejamesss/cmu-cli/archive/refs/heads/main.zip"
        in message
    )
    assert "/synthetic/profile" not in captured.out + captured.err
    network.assert_not_called()


@pytest.mark.parametrize("action", ["threads", "search"])
def test_ed_help_is_concise(action, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(["ed", action, "--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert len(help_text) < 2000
    assert "--max-pages N" in help_text
    assert "1,2,3" not in help_text


@pytest.mark.parametrize("flag,limit", [("--page-size", 100), ("--max-pages", 1000)])
@pytest.mark.parametrize("boundary", ["low", "high", "below", "above"])
def test_ed_page_boundaries(flag, limit, boundary):
    value = {"low": 1, "high": limit, "below": 0, "above": limit + 1}[boundary]
    args = ["ed", "search", "--course-id", "12", "--query", "test", flag, str(value)]
    if boundary in {"below", "above"}:
        with pytest.raises(SystemExit):
            cli.parser().parse_args(args)
    else:
        assert (
            getattr(cli.parser().parse_args(args), flag[2:].replace("-", "_")) == value
        )


def make_archive(path, files, kind=None):
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            if kind:
                member.type = kind
                member.linkname = "/outside"
            else:
                member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def test_sdist_assets_required(tmp_path):
    archive = tmp_path / "source.tar.gz"
    files = {
        "pkg/README.md": b"![workflow](docs/assets/workflow.svg) ![screen](docs/assets/screen.png)",
        "pkg/docs/assets/workflow.svg": b"<svg/>",
        "pkg/docs/assets/screen.png": b"synthetic",
    }
    make_archive(archive, files)
    _, count = check_sdist(archive, tmp_path / "good")
    assert count == 2
    del files["pkg/docs/assets/workflow.svg"]
    make_archive(archive, files)
    with pytest.raises(AssertionError, match="missing"):
        check_sdist(archive, tmp_path / "bad")


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", None),
        ("/absolute", None),
        ("pkg/../../escape", None),
        ("pkg/link", tarfile.SYMTYPE),
        ("pkg/hard", tarfile.LNKTYPE),
        ("pkg/device", tarfile.CHRTYPE),
    ],
)
def test_sdist_rejects_unsafe_members(tmp_path, name, kind):
    archive = tmp_path / "source.tar.gz"
    make_archive(archive, {name: b"bad"}, kind)
    with pytest.raises(AssertionError, match="Unsafe"):
        check_sdist(archive, tmp_path / "unpacked")
    assert not (tmp_path / "unpacked").exists()


def test_doc_link_cannot_escape(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "outside.md").write_text("outside")
    (tmp_path / "docs/README.md").write_text("[outside](../outside.md)")
    with pytest.raises(AssertionError, match="escapes"):
        check_links(tmp_path / "docs")
