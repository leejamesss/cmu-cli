"""Local release gate. Run with the project's dev dependencies installed."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
from pathlib import Path
from urllib.parse import unquote, urlsplit


def check_links(root):
    root = root.resolve()
    checked = 0
    for page in root.rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        # Inline and reference links, including images; skip code fences.
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        targets = re.findall(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", text)
        targets += re.findall(r"^\s*\[[^\]]+\]:\s*(\S+)", text, re.MULTILINE)
        for target in targets:
            parts = urlsplit(target.strip("<>"))
            if parts.scheme or parts.netloc:
                continue
            dest = (page.parent / unquote(parts.path)).resolve() if parts.path else page
            if not dest.is_relative_to(root):
                raise AssertionError(f"{page}: link escapes archive: {target}")
            if not dest.exists():
                raise AssertionError(f"{page}: missing {target}")
            if parts.fragment and dest.suffix == ".md":
                headings = re.findall(r"^#+\s+(.+)$", dest.read_text(), re.MULTILINE)
                anchors = [
                    re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-")
                    for h in headings
                ]
                if unquote(parts.fragment) not in anchors:
                    raise AssertionError(f"{page}: missing anchor {target}")
            checked += 1
    return checked


def check_sdist(archive_path, destination):
    """Extract only regular files/directories inside one package root."""
    from pathlib import PurePosixPath

    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        roots = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or not path.parts
                or not (member.isfile() or member.isdir())
            ):
                raise AssertionError("Unsafe sdist member")
            roots.add(path.parts[0])
        if len(roots) != 1:
            raise AssertionError("sdist must have one package root")
        destination.mkdir(parents=True, exist_ok=False)
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    extracted = destination / roots.pop()
    return extracted, check_links(extracted)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--links-only", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    count = check_links(source)
    print(f"Local documentation links: {count} passed", flush=True)
    if args.links_only:
        return
    output = Path(tempfile.mkdtemp(prefix="cmu-cli-release-")).resolve()
    print(f"Artifacts and logs: {output}", flush=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONPATH", "PYTHONHOME")
        and not k.startswith(("CMU_CLI_", "CMUCW_"))
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    (output / "home").mkdir()
    env["HOME"] = str(output / "home")

    def run(label, command, cwd=output, environment=env, expected=0):
        result = subprocess.run(
            list(map(str, command)),
            cwd=cwd,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        (output / (label + ".log")).write_text(result.stdout + result.stderr)
        print(f"{label}: exit {result.returncode}", flush=True)
        if result.returncode != expected:
            raise RuntimeError(f"{label} failed; see {output / (label + '.log')}")
        return result.stdout

    # Build in a copy: setuptools must not leave generated files in the checkout.
    checkout = output / "source"
    shutil.copytree(
        source,
        checkout,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            "*.egg-info",
            "build",
            "dist",
            ".pytest_cache",
            ".DS_Store",
        ),
    )
    run("lint", [sys.executable, "-m", "ruff", "check", "--no-cache", "."], checkout)
    run(
        "format",
        [sys.executable, "-m", "ruff", "format", "--no-cache", "--check", "."],
        checkout,
    )
    source_env = dict(env, PYTHONPATH=str(checkout / "src"))
    run(
        "source-tests",
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        checkout,
        source_env,
    )
    run("build", [sys.executable, "-m", "build", "--outdir", output / "dist", checkout])
    run(
        "metadata",
        [sys.executable, "-m", "twine", "check", *sorted((output / "dist").iterdir())],
    )
    sdist = next((output / "dist").glob("*.tar.gz"))
    extracted, sdist_links = check_sdist(sdist, output / "sdist")
    print(f"sdist documentation links: {sdist_links} passed", flush=True)
    venv.create(output / "sdist-env", with_pip=True)
    sdist_python = output / "sdist-env/bin/python"
    run("sdist-install", [sdist_python, "-m", "pip", "install", sdist])
    run("sdist-dependency-check", [sdist_python, "-m", "pip", "check"])
    run("sdist-demo", [sdist_python, "-m", "cmu_cli", "demo", "--json"])
    run(
        "sdist-links",
        [sdist_python, extracted / "scripts/release_check.py", "--links-only"],
    )
    venv.create(output / "env", with_pip=True)
    python = output / "env/bin/python"
    wheel = next((output / "dist").glob("*.whl"))
    run("install", [python, "-m", "pip", "install", wheel, "pytest", "jsonschema"])
    run("dependency-check", [python, "-m", "pip", "check"])
    run(
        "import-location",
        [
            python,
            "-c",
            'import cmu_cli; assert "site-packages" in cmu_cli.__file__; print(cmu_cli.__file__)',
        ],
    )
    run(
        "package-identity",
        [
            python,
            "-c",
            """
from importlib.metadata import distribution
from importlib.util import find_spec
from pathlib import Path
import sys
import zipfile

dist = distribution('cmu-cli')
assert dist.metadata['Name'] == 'cmu-cli'
assert {(e.name, e.value) for e in dist.entry_points} == {
    ('cmu-cli', 'cmu_cli.cli:main')
}
assert find_spec('cmucw') is None
assert not (Path(sys.executable).parent / 'cmucw').exists()
with zipfile.ZipFile(sys.argv[1]) as archive:
    packages = {n.split('/')[0] for n in archive.namelist() if '/' in n}
    assert packages == {'cmu_cli', f'cmu_cli-{dist.version}.dist-info'}, packages
print('Only cmu-cli distribution/entry point and cmu_cli package shipped')
""",
            wheel,
        ],
    )
    run(
        "wheel-browser-extra-absent",
        [
            python,
            "-c",
            "import importlib.util; assert importlib.util.find_spec('browser_cookie3') is None",
        ],
    )
    (output / "missing-browser.json").write_text(
        json.dumps(
            {
                "browser_auth": {
                    "enabled": True,
                    "browser": "edge",
                    "cookie_file": "/synthetic/profile/Cookies",
                    "hosts": ["s3.andrew.cmu.edu"],
                }
            }
        )
    )
    for mode in ("plain", "json"):
        label = "wheel-missing-browser-" + mode
        run(
            label,
            [
                python,
                "-m",
                "cmu_cli",
                "--config",
                "missing-browser.json",
                "sio",
                "schedule",
                *(["--json"] if mode == "json" else []),
            ],
            expected=2,
        )
        diagnostic = (output / (label + ".log")).read_text()
        assert "BROWSER_DEPENDENCY_MISSING" in diagnostic
        assert "cmu-cli[browser] @ https://github.com/leejamesss/cmu-cli/" in diagnostic
    executable = output / "env/bin/cmu-cli"
    run("setup-pty", [python, source / "scripts/setup_smoke.py", executable])
    run("setup-nontty", [executable, "setup"], expected=2)
    for label, command in [
        ("help", ["--help"]),
        ("demo", ["demo", "--json"]),
        ("init", ["config", "init", "--output", "entry-config.json"]),
        ("validate", ["--config", "entry-config.json", "config", "validate", "--json"]),
    ]:
        run("entry-" + label, [executable, *command])
    for label, command in [
        ("help", ["--help"]),
        ("version", ["--version"]),
        ("demo-human", ["demo"]),
        ("demo", ["demo", "--json"]),
        ("init", ["config", "init", "--output", "cmu-cli.json"]),
        ("validate", ["--config", "cmu-cli.json", "config", "validate", "--json"]),
        ("doctor", ["--config", "cmu-cli.json", "doctor", "--json"]),
    ]:
        run(label, [python, "-m", "cmu_cli", *command])
    run(
        "posts-without-opt-in",
        [python, "-m", "cmu_cli", "--config", "cmu-cli.json", "posts", "--json"],
        expected=3,
    )
    run(
        "ed-without-token",
        [python, "-m", "cmu_cli", "ed", "courses", "--json"],
        expected=2,
    )
    for provider in ("ed", "sio"):
        run(provider + "-help", [python, "-m", "cmu_cli", provider, "--help"])
    shutil.copytree(
        source / "tests", output / "tests", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copytree(source / "docs", output / "docs")
    shutil.copytree(source / "scripts", output / "scripts")
    run(
        "wheel-tests", [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
    )
    demo = json.loads((output / "demo.log").read_text())["data"]
    assert demo["statuses"] == ["downloaded", "unchanged"]
    assert [a["submitted"] for a in demo["assignments"]] == [True, False, None]
    (output / "verification.json").write_text(
        json.dumps(
            {"passed": True, "local_links": count, "wheel": str(wheel)}, indent=2
        )
    )
    print(f"PASS: {output}", flush=True)


if __name__ == "__main__":
    main()
