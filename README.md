# CMU CLI

[![Offline core checks](https://github.com/leejamesss/cmu-cli/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/leejamesss/cmu-cli/actions/workflows/ci.yml)

**Your coursework spans five platforms. Your workflow shouldn’t have to.**

Check Canvas for a deadline. Open Piazza or Ed for a clarification. Visit Gradescope for an assignment, then SIO for your schedule. Download the handout. Find it again next week. Keeping up with coursework should not start with another round of tabs.

**CMU CLI** (`cmu-cli`) is a command-line toolkit for CMU coursework across **Canvas, Piazza, Gradescope, Ed Discussion, and SIO**. Query supported course data from one CLI, use structured JSON in your own scripts, and keep Canvas materials organized locally.

![Canvas, Piazza, Gradescope, Ed Discussion and SIO feed cmu-cli read commands, with terminal output, versioned JSON and local materials through Canvas sync. Provider-specific authorization applies; Ed live accounts and SIO authenticated HTTP remain unverified.](docs/assets/coursework-workflow.svg)

- **Check the work, not every tab.** Read assignment deadlines and submission states from Canvas and configured Gradescope courses; read Canvas announcements, Piazza feeds, and Ed threads with provider-specific commands.
- **Keep useful materials close.** Sync Canvas metadata, Markdown indexes, and supported downloads into course/term folders, reusing unchanged files on repeat syncs.
- **Make the output work for you.** Choose terminal output or versioned `--json` results with source status, warnings, and explicit partial-result exit codes.
- **Inspect your semester.** SIO parsers cover the selected semester schedule and waitlist history—not current queue positions or registration actions.

One command-line interface, **not one shared login or universal search**. Integrations have different access requirements and coverage; see [what works where](#what-works-where) and [release status](#release-status).

## Quickstart (Python 3.10+)

Install from the public GitHub repository over HTTPS; no GitHub account is
required. No PyPI release is available. Git must be installed:

```sh
python -m pip install "git+https://github.com/leejamesss/cmu-cli.git@main"
cmu-cli demo --json
```

For a reproducible install, replace `main` with a reviewed full commit SHA.
Alternatively, from a source checkout:

```sh
python -m venv .venv
# macOS/Linux
. .venv/bin/activate
python -m pip install .
cmu-cli --version
cmu-cli demo
cmu-cli demo --json
cmu-cli config init --output cmu-cli.json
cmu-cli --config cmu-cli.json config validate --json
cmu-cli --config cmu-cli.json doctor --json
```

The demo syncs three synthetic assignments and an announcement, generates readable indexes, and downloads a sample material twice using the real storage implementation. It shows submitted/not-submitted/unknown states and proves the second sync needs no fetch. Temporary files are removed; JSON includes their paths and index contents. No credentials or network are needed. See the [walkthrough and executed output](docs/walkthrough.md). The config template is bundled in the wheel: no checkout is required for `config init`. Init refuses to overwrite an existing file. Doctor validates config offline; it does not test authentication or permission.

## Authorized Canvas workflow

Canvas prefers `CMU_CLI_CANVAS_TOKEN`. Alternatively explicitly select your local Edge/Chrome profile cookie database in `browser_auth`; see [setup](docs/auth.md). No built-in OAuth login or refresh is implemented.

Edit the template: set the HTTPS Canvas origin, course IDs, storage directory and term. A Canvas course URL such as `https://canvas.example.invalid/courses/123` identifies course ID `123`; use the actual authorized course URL, not this placeholder. Credentials do not belong in config. Supply the OAuth token through your organization's approved secret-injection workflow, then:

```sh
cmu-cli --config cmu-cli.json courses --json
cmu-cli --config cmu-cli.json assignments --course DEMO-101 --json
cmu-cli --config cmu-cli.json quizzes --json
cmu-cli --config cmu-cli.json materials --json
cmu-cli --config cmu-cli.json sync --course DEMO-101 --metadata-only --json
```

Replace `DEMO-101` with your configured code. `sync` without `--metadata-only` downloads files into configured local storage; metadata can contain private submission information and signed URLs. Other query commands do not write course files. `open canvas --course CODE` opens a configured URL in the default browser without accessing authentication. Piazza posts and Gradescope assignments support live read-only browser sessions after explicit local configuration. Pure parsers remain available as Python APIs.

`posts` reads only exact configured Piazza class networks, with bounded pagination; unavailable or incomplete sources return partial JSON and exit 3. Gradescope course links enable read-only assignment parsing. `platforms` reports links; `announcements` reads Canvas announcements.

## Discussion and schedule commands

After [provider setup](docs/provider-cli.md), use explicit course IDs and selected browser authorization where required:

```sh
# Piazza: the exact class network configured for this course
cmu-cli --config cmu-cli.json posts --course DEMO-101 --json

# Ed: CMU_CLI_ED_TOKEN in the environment; no Canvas config required
cmu-cli ed courses --json
cmu-cli ed threads --course-id 12 --json
cmu-cli ed search --course-id 12 --query deadline --json

# SIO: explicitly authorized browser session; selected-semester views only
cmu-cli --config cmu-cli.json sio schedule --json
cmu-cli --config cmu-cli.json sio waitlist-history --json
```

`DEMO-101` and Ed ID `12` are placeholders, not live course data. Ed search matches only fetched listing text, not replies or unfetched thread detail. SIO may require caller-supplied rendered HTML through its Python parsers; it does not capture the browser page for you. These command forms are covered by offline CLI tests, **not a claim of successful live account access**.

## What works where

| Commands / API | Purpose | Access and side effects |
|---|---|---|
| `demo` | Synthetic end-to-end sync and readable exports | Offline; temporary files only |
| `config init`, `config validate`, `doctor` | Bootstrap and check configuration | Offline; init creates a file |
| `auth check-registration` | Validate non-secret registration metadata, not provider approval or login | Offline; exit 3 even when metadata is valid; see [authorization](docs/auth.md) |
| `courses`, `status`, `platforms` | Course availability and links | Authorized Canvas; no course-file writes |
| `assignments`, `quizzes`, `announcements` | Read coursework metadata | Authorized Canvas; optional sources may report unavailable |
| `materials` | List remote materials and local lecture/recitation files | Canvas plus configured public heuristics |
| `sync` | Private metadata, Markdown indexes and material downloads | Authorized Canvas; local writes; `--metadata-only` skips downloads |
| `open` | Open a configured platform URL | Default browser only; no authenticated API read |
| `posts` | Live configured Piazza class feed | Explicit browser opt-in; failures are partial, exit 3 |
| `ed courses`, `ed threads`, `ed thread`, `ed replies`, `ed search` | Ed membership, listings, detail/replies and local listing search | Explicit environment API token; no Canvas config required; [commands](docs/provider-cli.md) |
| `sio schedule`, `sio waitlist-history`, `sio probe` (partial integration) | Selected semester schedule, historical waitlist entries, or readiness | Explicit browser auth for schedule/history; may need rendered HTML; no enrollment/current queue query; [limits](docs/sio.md) |
| Offline provider parsers (Python) | Parse already-authorized synthetic/local input | No CLI import command; see [architecture](docs/architecture.md) |

## Machine interface

All command-result `--json` output uses a [versioned envelope](docs/json-contract.md); consumers must inspect `status` and the process exit code. Exit **0** means completed requested scope, **2** means configuration/operation failure, **3** means partial/unavailable/truncated results. Argument-parser errors use exit 2 and stderr, not an envelope. Default list output is not CLI-truncated; explicit `--limit N` reports truncation. Human output is English-first; existing storage folder names remain compatible.

## Release status

**v0.1.0 is an early source release, not a fully live-verified integration suite.** Start with the reproducible offline demo, then enable only the providers you are authorized to use.

| Evidence | What it establishes |
|---|---|
| Offline demo and source/installed-wheel tests | Synthetic workflows, CLI contracts, storage behavior, and tested security cases—not live service compatibility |
| SIO authorized rendered-page snapshots | Schedule and waitlist-history parser verification; authenticated HTTP access/rendering remains unverified |
| Ed protocol research and synthetic tests | Implemented token-based read adapter; live-account behavior remains unverified |
| Canvas, Piazza, and Gradescope adapters | Implemented authorized read paths; offline checks do not certify your account, permissions, or institution's deployment |

Tests and the demo use synthetic data, not copied live student records. See the detailed [SIO evidence](docs/sio.md#verification) and [Ed evidence](docs/ed.md#verification).

**Read coursework; do not change it.** No assignment submission, grade changes, discussion posting, enrollment, or waitlist actions are provided. `sync` writes to your local workspace; `config init` creates a local file. No telemetry, model calls, cloud synchronization, scheduler, or calendar integration is included.

Live use requires provider and institutional authorization. Credentials stay out of config and source control; treat successful JSON, signed URLs, downloaded materials, and local metadata as private. Login/MFA is manual; no shared login, built-in OAuth lifecycle, or automatic browser-profile discovery is provided. See [authorization](docs/auth.md), [browser setup](docs/browser-auth.md), and [security](SECURITY.md).

## Development and boundaries

```sh
python -m pip install -e '.[dev]'
python scripts/release_check.py
```

Supported systems are macOS and Linux with POSIX no-follow filesystem operations. Windows storage writes fail closed and Windows is not supported. [CI](https://github.com/leejamesss/cmu-cli/actions/workflows/ci.yml) runs offline core and installed-wheel tests on Linux and macOS with Python 3.10 and 3.13. A green job verifies only its tested commit and matrix, not live institution integrations. See [configuration](docs/configuration.md), [architecture](docs/architecture.md), [security](SECURITY.md), [contributing](CONTRIBUTING.md), and [provenance](PROVENANCE.md).

See the [documentation guide](docs/README.md) for task-oriented navigation.

## License

MIT, copyright 2026 Jiatao Li; see [LICENSE](LICENSE). The software license does not grant permission to access third-party services or redistribute course/student content.

An independent project, not affiliated with or endorsed by CMU or the supported platforms.
