<p align="center">
  <img src="docs/assets/logo.svg" width="104" alt="">
</p>

<h1 align="center">CMU CLI</h1>

<p align="center">
  <em>Your coursework spans five platforms. Your workflow shouldn't have to.</em>
</p>

<p align="center">
  <a href="https://github.com/leejamesss/cmu-cli/actions/workflows/ci.yml"><img alt="Offline core checks" src="https://github.com/leejamesss/cmu-cli/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Status" src="https://img.shields.io/badge/status-developer%20preview-orange">
</p>

Check Canvas for a deadline. Open Piazza or Ed for a clarification. Visit Gradescope for
an assignment, then SIO for your schedule. Download the handout. Find it again next week.
Keeping up with coursework should not start with another round of tabs.

**CMU CLI** (`cmu-cli`) is a command-line toolkit for CMU coursework across **Canvas,
Piazza, Gradescope, Ed Discussion, and SIO**. Query supported course data from one CLI,
use structured JSON in your own scripts, and keep Canvas materials organized locally.

![Canvas, Piazza, Gradescope, Ed Discussion and SIO feed cmu-cli read commands, with terminal output, versioned JSON and local materials through Canvas sync. Provider-specific authorization applies; Ed live accounts and SIO authenticated HTTP remain unverified.](docs/assets/coursework-workflow.svg)

- **Check the work, not every tab.** Assignment deadlines and submission states from
  Canvas and configured Gradescope courses; Canvas announcements, Piazza feeds and Ed
  threads through provider-specific commands.
- **Keep useful materials close.** Sync Canvas metadata, Markdown indexes and supported
  downloads into course/term folders, reusing unchanged files on repeat syncs.
- **Make the output work for you.** Terminal output, or versioned `--json` results
  carrying source status, warnings and explicit partial-result exit codes.
- **Inspect your semester.** SIO parsers cover the selected semester schedule and
  waitlist history — not current queue positions or registration actions.

One command-line interface, **not one shared login or universal search**. Integrations
have different access requirements and coverage; see [what works where](#what-works-where)
and [usage notes](#usage-notes).

## Install

Python 3.10+. Installs from the public repository over HTTPS; no GitHub account needed,
though Git must be present. There is no PyPI release.

```sh
python -m pip install "git+https://github.com/leejamesss/cmu-cli.git@main"
cmu-cli demo --json
```

For a reproducible install, replace `main` with a reviewed full commit SHA. From a
source checkout instead:

```sh
python -m venv .venv && . .venv/bin/activate
python -m pip install .
cmu-cli --version
```

## Upgrading from cmucw

The distribution and primary command are now `cmu-cli`; Python APIs live only in
`cmu_cli`. The `cmucw` command and `python -m cmucw` remain thin launch aliases.
See [migration and precedence](docs/migration.md) before replacing an old install.

## Quick start

Everything here runs offline, with no credentials and no network:

```sh
cmu-cli demo                                    # synthetic end-to-end run
cmu-cli config init --output cmu-cli.json       # write a config template
cmu-cli --config cmu-cli.json config validate   # check it, offline
cmu-cli --config cmu-cli.json doctor            # diagnostics; does not authenticate
```

The demo syncs three synthetic assignments and an announcement, generates readable
indexes, and downloads a sample material twice using the real storage implementation. It
shows submitted / not-submitted / unknown states and proves the second sync needs no
fetch. Temporary files are removed, and the JSON includes their paths and index contents.
See the [walkthrough and executed output](docs/walkthrough.md).

`config init` refuses to overwrite an existing file, and the template ships inside the
wheel, so no checkout is required. `doctor` validates configuration offline; it does not
test authentication or permission.

## Using it with your own courses

Edit the template: the HTTPS Canvas origin, course IDs, storage directory and term. A
course URL such as `https://canvas.example.invalid/courses/123` identifies course ID
`123` — use your actual authorized course URL, not the placeholder.

**Credentials do not belong in the config.** Supply the token through your organization's
approved secret-injection workflow:

```sh
export CMU_CLI_CANVAS_TOKEN=...
cmu-cli --config cmu-cli.json courses --json
cmu-cli --config cmu-cli.json assignments --course DEMO-101 --json
cmu-cli --config cmu-cli.json sync --course DEMO-101 --metadata-only --json
```

Alternatively, explicitly select your local Edge/Chrome profile cookie database in
`browser_auth` — see [setup](docs/auth.md). No built-in OAuth login or refresh is
implemented.

`sync` without `--metadata-only` downloads files into configured local storage; that
metadata can contain private submission information and signed URLs. Other query commands
do not write course files. `open canvas --course CODE` opens a configured URL in your
default browser without touching authentication.

### Discussion and schedule commands

After [provider setup](docs/provider-cli.md), with explicit course IDs and browser
authorization where required:

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

`DEMO-101` and Ed ID `12` are placeholders, not live course data. Ed search matches only
fetched listing text, not replies or unfetched thread detail. SIO may require
caller-supplied rendered HTML through its Python parsers; it does not capture the browser
page for you; see the [SIO setup guide](docs/sio.md) for details.

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
| `sio schedule`, `sio waitlist-history`, `sio probe` | Selected semester schedule, historical waitlist entries, or readiness | Explicit browser auth for schedule/history; may need rendered HTML; no enrollment/current queue query; [limits](docs/sio.md) |
| Offline provider parsers (Python) | Parse already-authorized synthetic/local input | No CLI import command; see [architecture](docs/architecture.md) |

## Machine interface

All `--json` output uses a [versioned envelope](docs/json-contract.md); consumers must
inspect `status` **and** the process exit code.

| Exit | Meaning |
|---|---|
| `0` | Completed the requested scope |
| `2` | Configuration or operation failure |
| `3` | Partial, unavailable or truncated results |

Argument-parser errors use exit 2 and stderr, not an envelope. Default list output is not
CLI-truncated; an explicit `--limit N` reports truncation. Human output is English-first;
existing storage folder names remain compatible.

## Security

Authenticated traffic never leaves its origin: redirects are validated hop by hop, and a
download that redirects to pre-signed storage is fetched *without* your credentials.
Tokens come from the environment, never from config files, and never appear in output or
diagnostics.

Browser-session reading is opt-in, requires an absolute path to a profile you name
yourself, and is limited to hosts you list. See [`docs/auth.md`](docs/auth.md) and
[`SECURITY.md`](SECURITY.md) — and note that the MIT license grants no access to any
service, nor rights to course or student data.

## Usage notes

Configure each platform with your own account using the [provider setup guide](docs/provider-cli.md).
For platform-specific setup, known limitations, and testing details, see the
[SIO guide](docs/sio.md) and [Ed guide](docs/ed.md).

**Read coursework; do not change it.** No assignment submission, grade changes,
discussion posting, enrollment or waitlist actions are provided. `sync` writes to your
local workspace; `config init` creates a local file. No telemetry, model calls, cloud
synchronization, scheduler or calendar integration is included.

## Documentation

| If you want to… | Read |
|---|---|
| Understand the module layout | [docs/architecture.md](docs/architecture.md) |
| Set up authorization properly | [docs/auth.md](docs/auth.md) |
| Use an existing browser session | [docs/browser-auth.md](docs/browser-auth.md) |
| Write a config file | [docs/configuration.md](docs/configuration.md) |
| Consume the JSON output | [docs/json-contract.md](docs/json-contract.md) |
| See a real run, start to finish | [docs/walkthrough.md](docs/walkthrough.md) |
| Use Ed or SIO | [docs/ed.md](docs/ed.md), [docs/sio.md](docs/sio.md) |

## Development

```sh
python -m pip install -e ".[dev]"
python -m ruff check . && python -m ruff format --check .
python -m pytest -q
python scripts/release_check.py
```

Supported systems are macOS and Linux with POSIX no-follow filesystem operations;
Windows storage writes fail closed and Windows is not supported. CI tests Linux and
macOS with Python 3.10 and 3.13. Tests are offline: synthetic fixtures, never real coursework.

## Community

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for what a
good reproduction looks like, and [PROVENANCE.md](PROVENANCE.md) for what this repository
does and does not claim about the origin of its code.

---

<sub>An independent, unofficial tool. Not affiliated with, endorsed by, or sponsored by
Carnegie Mellon University. The mark above is this project's own; no university logo,
seal, wordmark or mascot is used.</sub>
