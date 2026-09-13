# Provider setup and commands

Start with the [installation and offline demo](../README.md#install-and-try-it).
Connect only the platforms you use; each has its own authorization.

## Canvas

1. Run `cmu-cli config init --output cmu-cli.json` to create a template. It will not
   overwrite an existing file.
2. Set `canvas_base_url` to your Canvas HTTPS origin. In each `courses` entry, set
   `code` to the label you want to type, `canvas_id` to the number in your course URL,
   `name` to the course name, and `directory` to a single folder name. For example,
   `/courses/123` identifies Canvas course ID `123`.
3. Set `term` and a private `storage_root`. Relative storage paths resolve beside
   the config, not from the shell's current directory. See [configuration](configuration.md).
4. Configure an [explicit Edge/Chrome session](browser-auth.md), or supply an
   externally provisioned token as `CMU_CLI_CANVAS_TOKEN` through your approved
   secret-management workflow. Tokens never belong in the JSON config. See
   [authorization](auth.md) for institutional requirements; built-in OAuth login
   and refresh are not implemented.

```sh
cmu-cli --config cmu-cli.json config validate
cmu-cli --config cmu-cli.json doctor
cmu-cli --config cmu-cli.json courses
# Replace DEMO-101 with the code in your config
cmu-cli --config cmu-cli.json assignments --course DEMO-101
cmu-cli --config cmu-cli.json materials --course DEMO-101
cmu-cli --config cmu-cli.json sync --course DEMO-101 --metadata-only
cmu-cli --config cmu-cli.json sync --course DEMO-101
```

`config validate` and `doctor` check configuration offline, not login or permission.
`sync` writes metadata and readable indexes; without `--metadata-only`, it also
fetches supported Canvas files. Metadata can include private submission information
and signed URLs. Query commands do not write course files.

## Piazza and Gradescope

Follow [browser-session setup](browser-auth.md), then set each course's
`piazza_url` to its exact `https://piazza.com/class/NETWORK_ID` URL and
`gradescope_url` to its exact `https://www.gradescope.com/courses/COURSE_ID` URL.
Include the corresponding exact hosts in `browser_auth.hosts`.

```sh
cmu-cli --config cmu-cli.json posts --course DEMO-101 --json
cmu-cli --config cmu-cli.json assignments --course DEMO-101 --json
cmu-cli --config cmu-cli.json open canvas --course DEMO-101
```

`posts` reads Piazza feed summaries. `assignments` includes configured Gradescope
assignment/submission states alongside Canvas. Provider failures remain explicit
rather than turning into empty results. `open` only launches the configured URL
in your default browser; it does not authenticate an API request.

## Ed Discussion

Supply `CMU_CLI_ED_TOKEN` as described in the [Ed authentication guide](ed.md#authentication-and-origins).
No Canvas config or browser session is required.

```sh
cmu-cli ed courses --json
cmu-cli ed threads --course-id 12 --page-size 100 --max-pages 100 --json
cmu-cli ed thread --id 34 --course-id 12 --json
cmu-cli ed replies --id 34 --json
cmu-cli ed search --course-id 12 --query deadline --json
```

Replace `12` and `34` with actual Ed IDs. `--id` is a global thread ID, not its
course-local number. `--course-id` is required for threads/search and optional for
thread/replies identity checking. `--page-size` accepts 1–100; `--max-pages`
accepts 1–1000; both default to 100. Search matches fetched listing title/content/
document text, excluding replies and unfetched detail.

Optional config can set `ed_api_base_url` to `https://us.edstem.org/api` (default)
or `https://edstem.org/api`. There is no automatic cross-origin fallback. A
provider-only config may contain just this key. Per-course `ed_course_id` in a full
Canvas config is metadata; CLI IDs remain explicit. `config validate` validates
full Canvas configs, not provider-only configs. See [Ed behavior and verification](ed.md).

## SIO

Follow the [SIO guide](sio.md) to authorize a selected browser profile and allow
`s3.andrew.cmu.edu`, then run:

```sh
cmu-cli --config cmu-cli.json sio schedule --json
cmu-cli --config cmu-cli.json sio waitlist-history --json
```

These read the selected semester schedule and historical waitlist entries—not
current queue positions or enrollment actions. If you receive
`rendered_snapshot_required`, use the Python parsers with your own authorized
rendered HTML capture as described in the [guide](sio.md#python-apis). The CLI does
not capture the browser page for you. Authenticated HTTP rendering has not been
live-verified; parser fixtures cover observed page structures.

`cmu-cli sio probe --json` is a readiness diagnostic, not a schedule query. Its
returned results are always incomplete (exit 3), including HTTP 200; transport
errors exit 2.

## Configuration and result handling

Ed and SIO accept optional provider-only config without Canvas keys. Precedence is
`--config PATH` (before the provider command), then `CMU_CLI_CONFIG`, then an
existing default config. An explicit missing path fails rather than being ignored.

All `--json` results use the [versioned envelope](json-contract.md). Inspect source
warnings, top-level `status`, and the process exit code: 0 completed, 2 failed,
3 partial/unavailable/truncated. Argument-parser errors use stderr and exit 2.
An explicit `--limit N` on supported list commands reports truncation; no CLI list
limit is applied by default.

For the full command tree, run `cmu-cli --help` or `cmu-cli COMMAND --help`.
The optional `auth check-registration` command checks non-secret OAuth proposal
metadata offline; it is not login and returns exit 3 even for valid metadata.
See [authorization preflight](auth.md#offline-registration-preflight).
