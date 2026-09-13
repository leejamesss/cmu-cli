# SIO read-only semester schedule and waitlist history

## CLI

```sh
cmu-cli --config cmu-cli.json sio schedule --json
cmu-cli --config cmu-cli.json sio waitlist-history --json
cmu-cli --config cmu-cli.json sio probe --json
```

Configure the selected browser session as described below. These commands do not
register, drop, confirm or modify any course. History is not a current queue.

## Python APIs

```python
from cmu_cli.sio_client import (
    SIOClient, parse_semester_schedule, parse_waitlist_history,
)

client = SIOClient(browser_auth=config.browser_auth)
schedule = client.semester_schedule()
history = client.waitlist_history()

# Alternative: caller-authorized HTML captured from the selected browser page.
schedule = parse_semester_schedule(html)
history = parse_waitlist_history(html)
```

`semester_schedule()` GETs only the verified route
`https://s3.andrew.cmu.edu/sio/mpa/semesterschedule`.
`waitlist_history()` GETs only
`https://s3.andrew.cmu.edu/sio/mpa/schedule/waitlisthistory`.
Neither accepts guessed term parameters or changes the selected semester.
Constants are `SEMESTER_SCHEDULE_URL` and `WAITLIST_HISTORY_URL`.

Both require explicitly passed `browser_auth` for online reads. Without it they
return `login_required` with `EXPLICIT_BROWSER_AUTH_REQUIRED` without accessing
cookies or the network. Construction has no credential side effects. The existing
shared loader requires `enabled: true`, a host allowlist including
`s3.andrew.cmu.edu`, an explicit absolute profile `cookie_file`, and `edge` or
`chrome`. A disabled configuration fails closed with sanitized `SIOError`.
No browser profile or cookies were read during this implementation's verification.

### Structure confirmed against the live pages

On 2026-09-12 an authorised student session was used to confirm that the selectors
this parser depends on exist and carry the expected labels. `tests/test_sio_live_shape.py`
encodes the observed structure with synthetic content; no page content was retained.

| Selector | Schedule page | Waitlist page |
| --- | --- | --- |
| `table.course-list-tbl` / `table.schedule-waitlist-tbl` | present, once | present, once |
| `#semester-code-select` / `#semesters-select` | present | present |
| `.grid-hdr` | wraps every header cell | wraps every header cell |
| `.display-block` | wraps each instructor | — |

Two details are worth recording because a parser can plausibly get them wrong and
neither is visible from a screenshot:

- the header row and the data rows carry **different** `data-title` values. The header
  says `Title / Number & Section`; every data row says `Course`. Keying off the header
  finds no course cell in any row.
- each data row leads with `<th scope="row">`, not `<td>`, and ends with an unlabelled
  details link. Collecting only `td` drops the course cell and shifts every remaining
  value one column left.

The current parser handles both; the fixtures exist so that stays true.

## Output contract

Results include `source`, `status`, `complete`, `warnings`, `provenance`, `view`,
`term`, `term_code`, `rows_seen`, and `rows_parsed`.

- Semester `view` is `semester_schedule`; `schedule` contains rows with
  `course_code`, `section`, `title`, `instructors` (list), `dates`, `times`, and
  `building_room`. Date/time/location text is preserved rather than guessed into
  calendar events or timezones. Instructor email links are not extracted.
- History `view` is `waitlist_history`; `waitlist_history` contains rows with
  `course_code`, `section`, `data_entry_by`, `on_date`, `removed_by`, `off_date`,
  and `confirm_date`. Blank event cells are null, not inferred events.
- **History is not the current queue.** No position, active waitlist status,
  enrollment, seat availability, or confirmation action is inferred. `waitlist`
  and `registered_courses` remain null in both views.
- `complete: true` means the recognized rows of this selected-semester view were
  parsed with known term and no schema warnings; it does not mean all semesters,
  current registration status, or a complete account export.
- Missing/empty templates are unknown, not a verified empty list. Malformed rows
  are counted in `rows_seen`, omitted from parsed rows, and produce warnings.
  Partial rows and ambiguous selected terms make `complete: false`.

`status` is `ok`, `partial`, or `rendered_snapshot_required` for HTML parsing;
HTTP can additionally return `login_required`, `unavailable`, or
`auth_redirect_blocked`. Propagate result warnings and completeness into any CLI
envelope; do not label every returned dictionary a successful source fetch.

## Verified structures and boundaries

User-authorized local rendered-page snapshots established the heading
`Semester Schedule`, `table.course-list-tbl`, `#semester-code-select`, and cells
labelled `Course`, `Instructor`, `Dates`, `Times`, and `Bldg/Room` via `data-title`.
Course cells have separate title and five-digit-number/section text nodes; row
headers can be `th`, not only `td`. Instructor names use `.display-block`.
The generated calendar tables are ignored.

The other snapshot was checked for SIO title and the exact `Waitlist History`
heading, not merely a sidebar link. It uses `table.schedule-waitlist-tbl`,
`#semesters-select`, and the six historical column labels represented above.

The selected option must have a `selected` attribute or be the sole option.
With several options and no selected attribute the parser will not assume the
first is the user's current term. A browser capture should reflect the selected
option in serialized HTML if JS changed only its DOM property. Parsers do not
execute scripts or inspect embedded account state. Heading/table identity checks
reject unrelated views. Caller-supplied HTML is not proof of URL or freshness:
callers must verify the exact route before capture. Provenance URLs identify the
expected source, not an authenticated attestation of arbitrary HTML input.

Authenticated HTTP rendering has not been live-verified. A server response may
contain only a shell; in that case use a user-authorized rendered HTML capture.
No undocumented JSON endpoints, asset paths, GET query parameters, or business
requests are guessed. Static HTML versus rendered HTML is distinguished by the
presence of recognized rows, not HTTP 200 alone.

## Security and privacy

Requests use the shared bounded HTTPS transport with the exact SIO origin.
Cross-origin redirects, including CMU login, are blocked before transmission;
responses are bounded and closed. Only GET is used. Login/MFA must be completed
manually. There is no enrollment, plan edit, drop, waitlist confirmation, browser
execution, automatic profile discovery, or credential logging.

Parsed schedules/history contain private academic information: avoid public logs
and treat saved output as sensitive. Raw DOM, hidden fields, script data, student
identity banners, and tokens are not returned or included in test fixtures.
Tests use wholly synthetic examples, not copied private records.

## Retained legacy APIs

`SIOClient().probe()` retains anonymous, origin-bound entry-point readiness
behavior; it is not an enrollment query. `parse_snapshot(*, url, title, text)`
retains its legacy visible-text contract for `/sio/#schedule-plan` and
`/sio/#schedule-registration`. It recognizes only plan units, never course rows.
Plan totals are not enrollment. `SIO_URL`, `PLAN_URL`, `REGISTRATION_URL`, and
`SIOError` remain available.

## Verification

```sh
PYTHONPATH=src python -m pytest -q
python -m ruff check .
```

Local private-snapshot parser verification recovered three of three semester
rows with all supported schedule fields and one of one history rows. The history
confirmation cell was blank and preserved as null. Both selected terms were
available; no parser warnings were produced. This verifies captured HTML parsing,
not live authenticated HTTP transport or current queue status.
