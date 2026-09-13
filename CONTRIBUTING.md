# Contributing

Start with a focused bug, regression test, documentation correction or authorized adapter design. All fixtures must be synthetic: no personal tokens, cookie databases, keychains or live coursework.

## Local feedback loop

From an activated project virtual environment:

```sh
python -m pip install -e '.[dev]'
python -m ruff check --no-cache .
python -m ruff format --no-cache --check .
python -m pytest -q
python -m pytest -q tests/test_adoption.py
python scripts/release_check.py --links-only
python scripts/release_check.py
```

Ruff is pinned in the dev extra; project-local rules prevent inherited user configuration. Run `python -m ruff format .` to format edits. No lint ignores are configured.

The release check copies the source to a temporary directory, checks lint and formatting, runs the full suite, builds and checks wheel/sdist metadata, installs the wheel in a fresh environment, checks offline onboarding, and reruns the full suite outside the checkout. Logs and artifacts stay in the printed temporary directory. Build isolation and fresh-wheel dependency installation need package-index access. The tests/demo do not contact coursework providers. This is not an OS sandbox or live-provider certification.

## Where to make a change

| Change | Implementation | Start testing here |
|---|---|---|
| Submission interpretation | `src/cmu_cli/submissions.py` (shared by CLI/storage) | `tests/test_adoption.py` |
| CLI output or payload fields | `src/cmu_cli/cli.py`, `docs/result.schema.json` | `tests/test_cli_contract.py`, `tests/test_final_integration.py` |
| Material naming, revisions, indexes | `src/cmu_cli/storage.py` | `tests/test_canvas_homework.py`, `tests/test_storage_security.py` |
| Canvas pagination or transport | `src/cmu_cli/canvas_client.py`, `src/cmu_cli/web_session.py` | `tests/test_network_security.py` |
| Offline parsers | `src/cmu_cli/official_materials.py`, provider modules | Corresponding parser test module |

Add a failing synthetic regression first, change the smallest shared layer, then run targeted and full checks. Preserve provider failures as failures, not empty success. For storage changes test collisions, symlinks, metadata writes and preservation of user edits. Update the JSON reference for additive fields; incompatible contracts require a version decision. Keep pure semantics outside the CLI to avoid circular imports.

New adapters require synthetic fixtures, exact origin restrictions, explicit authentication consent, bounded pagination and sanitized failures. Never read real browser credentials in automated tests.

## Before proposing a change

Include the observed failure, expected behavior and exact tests run. Do not attach private exports or signed URLs. Identify adapted code/fixtures and attribution; contribute only material you have the right to offer under MIT. Security reporting follows [SECURITY.md](SECURITY.md).
