
## Ed CLI and partial SIO readiness

```sh
cmu-cli ed courses --json
cmu-cli ed threads --course-id 12 --page-size 100 --max-pages 100 --json
cmu-cli ed thread --id 34 --course-id 12 --json
cmu-cli ed replies --id 34 --json
cmu-cli ed search --course-id 12 --query deadline --json
cmu-cli sio probe --json
cmu-cli sio schedule --json
```

IDs above are synthetic: replace them with exact Ed IDs. `--id` is a global
thread ID, not the course-local number. `--course-id` is required for threads
and search, optional for thread/replies identity checking. `--page-size` is
1–100; `--max-pages` is 1–1000; both default to 100. Search is local fetched
listing title/content/document matching, excluding replies and unfetched detail.

Ed requires only environment `CMU_CLI_ED_TOKEN`, not Canvas or browser auth.
Neither provider requires a config file. Optional `--config PATH` (before the
provider command), `CMU_CLI_CONFIG`, or an existing default config can supply
`ed_api_base_url` and `browser_auth`. Explicit missing config paths fail closed.
A provider-only config may contain just `{"ed_api_base_url":"https://us.edstem.org/api"}`.
The alternate exact supported base is `https://edstem.org/api`; no automatic
cross-origin fallback. Full Canvas configs also accept optional per-course
`ed_course_id`, preserved as metadata only: CLI IDs remain explicit, never
inferred from Canvas IDs or names. `config validate` remains the full Canvas
configuration validator, not a provider-only validator.

SIO `probe` is readiness groundwork, not an enrolled-course, schedule or waitlist
query. `sio schedule` reads the observed selected-semester table with explicit
browser auth; it does not infer enrollment or current waitlist positions.
Unrendered HTML can report `rendered_snapshot_required`. Every returned probe
result is incomplete (exit 3), including HTTP 200;
transport errors exit 2. Unknown records remain null. Authorized frontend inspection now grounds the semester table parser; live
HTTP responses may still require rendered-page data. No live schedule success
is claimed by the CLI tests. No browser
login or cookie protocol is invented for Ed. See [Ed](ed.md) and [SIO](sio.md).
