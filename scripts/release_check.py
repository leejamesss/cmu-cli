"""Local release gate. Run with the project's dev dependencies installed."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from urllib.parse import unquote, urlsplit


def check_links(root):
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
    for executable in ("cmu-cli", "cmucw"):
        run(executable + "-entry-help", [output / "env/bin" / executable, "--help"])
        run(
            executable + "-entry-demo",
            [output / "env/bin" / executable, "demo", "--json"],
        )
    run("legacy-module", [python, "-m", "cmucw", "--help"])
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
