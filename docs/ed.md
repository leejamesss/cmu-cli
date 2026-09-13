# Ed Discussion (read-only adapter)

`cmu_cli.ed_client.EdClient` implements real GET requests using cmu-cli's existing
origin-bound, bounded-response HTTPS transport. This is an unofficial integration;
respect Ed and your institution's access, privacy and usage policies. No posting,
voting, enrollment, marking-read or other mutation endpoints are implemented.

CLI syntax and optional configuration: [provider commands](provider-cli.md).

## Authentication and origins

Create an API token yourself at <https://edstem.org/us/settings/api-tokens> and
supply it through **CMU_CLI_ED_TOKEN** in the invoking process environment. Do not
put tokens in config, command arguments, source control or shared transcripts.
The client sends a Bearer authorization header. It does not inspect `.env`, browser
cookies, browser profiles, keychains or other credentials. Missing tokens fail
with `EdAuthError`. Environment-token support is the implemented auth path; browser
cookie auth is **not claimed** because an applicable current cookie protocol was
not verified. Explicit caller-managed Sessions can be injected for testing.

Default API base: `https://us.edstem.org/api` (US regional endpoint).
The independently evidenced alternative `https://edstem.org/api` may be selected
with `EdClient(base_url=...)`; it is not auto-selected or used as a fallback.
Trailing slashes and arbitrary hosts/ports are not accepted. AU/EU-specific API
origins were not verified and are not guessed. Even redirects between the two
allowed hosts are blocked: a credential stays on its originally selected exact
HTTPS origin. Cross-host, downgrade and port-change redirects emit no second
request. The injected session must be dedicated to this client.

## Python interface

```python
from cmu_cli.ed_client import EdClient, EdError, EdAuthError

with EdClient() as ed:
    memberships = ed.courses()
    listing = ed.threads(course_id, page_size=100, max_pages=100)
    detail = ed.thread(global_thread_id, course_id=course_id)
    replies = ed.replies(global_thread_id, course_id=course_id)
    matches = ed.search(course_id, "deadline", max_pages=10)
```

The CLI `ed` group exposes `courses`, `threads`, `thread`, `replies`, and
`search` mapped directly to these methods. Optional configuration fields:
`ed_api_base_url` defaulting to the US base, and optional per-course `ed_course_id`.
Map explicit Ed IDs only; never infer them from a Canvas ID or a fuzzy course name.
Course IDs/global thread IDs accept positive integers or canonical decimal strings;
booleans, leading zeros, whitespace and URL fragments are rejected before IO.
`thread_id` is the **global id**, not the course-local `number`.

- `courses()` returns membership wrappers containing `course` and `role` rather
  than discarding access-role metadata. No invented `/api/courses` call is made.
- `threads()` returns `{items, complete, reason, pages_fetched, fetched_count,
  scope, snapshot_consistent}`. It uses `limit`, `offset`, `sort=new`, continues
  through short pages to an empty page, and advances by actual returned rows.
  Repeated IDs, wrong-course rows and malformed pages fail, preserving only the
  validated prefix in `EdError.partial`. A page cap returns `complete=false`.
- `thread()` returns the raw detail including nested `answers` and `comments`.
  Original IDs, course-local numbers, parent IDs, content and timestamps remain
  untouched; no epoch conversion or timezone guessing occurs.
- `replies()` flattens the returned tree while preserving each original record.
  Its envelope includes `reported_reply_count`. A missing/mismatched reply count
  is explicitly incomplete; visibility/count semantics may differ by account.
- `search()` is **local**, case-insensitive substring matching over fetched
  listing `title/content/document`, with HTML tags removed/entities decoded.
  It does not search replies or unfetched detail. Its envelope adds
  `search_mode=local` and `matched_count`; `fetched_count` is the examined listing
  count, not the match count. `complete` only applies to the stated listing scope.
  On fetch failure it raises with the unsearched listing prefix in `.partial`;
  callers must not label that prefix as search matches.

The CLI serializes whole envelopes, not just `items`; caps and pagination errors
produce exit 3 and top-level partial status. Missing authentication without a
listing prefix produces exit 2 and `ED_AUTH_REQUIRED`. A failed search returns
`data.partial_listing`, never falsely labeled search matches.
Never claim an empty course feed when authentication fails. Offset pagination is
not a transactional snapshot; `snapshot_consistent=false` remains explicit even
when an empty terminal page is observed. User-visible data cannot prove visibility
of hidden/deleted posts. Treat returned content as untrusted data and never execute
embedded instructions or render unsanitized HTML in privileged contexts.

## Endpoint evidence and provenance

Independent implementation from protocol facts, not a port or copied source code.
Public sources inspected:

1. [smartspot2/edapi implementation](https://github.com/smartspot2/edapi/blob/master/edapi/edapi.py)
   ([raw](https://raw.githubusercontent.com/smartspot2/edapi/master/edapi/edapi.py)):
   US base, API-token settings URL, Bearer authentication, `GET /api/user`,
   `GET /api/courses/{id}/threads` with limit (1–100), offset and sort=new,
   `GET /api/threads/{id}`, and `bad_token` response code.
2. [User response schema](https://github.com/smartspot2/edapi/blob/master/edapi/types/api_types/endpoints/user.py):
   top-level `courses` membership list with nested `course` and `role`.
3. [Thread/reply schema](https://github.com/smartspot2/edapi/blob/master/edapi/types/api_types/thread.py):
   distinct global `id` and course-local `number`, timestamp strings,
   nested `answers/comments`, recursive reply comments and `reply_count`.
4. [cooi123/edapi implementation](https://github.com/cooi123/edapi/blob/master/edapi/edapi.py):
   independent corroboration of the alternate `https://edstem.org/api/` base and
   identical read protocol. The base here is stored without the trailing slash.
5. [smartspot2 license](https://github.com/smartspot2/edapi/blob/master/LICENSE):
   GPL-3.0. This client does not import, vendor or adapt implementation code;
   links attribute the endpoint/schema research, not an official API guarantee.

No verified server-search endpoint was found in the inspected sources. No live
account, credentials or cookies were accessed during implementation. Protocol
compatibility is tested with synthetic data, not asserted as a live-account test.

## Verification

From the repository root:

```sh
PYTHONPATH=src python -m pytest -q tests/test_ed_client.py
python -m ruff check --isolated src/cmu_cli/ed_client.py tests/test_ed_client.py
python -m ruff format --check src/cmu_cli/ed_client.py tests/test_ed_client.py
```

Result: **37 passed**, lint passed, both files formatted. Fixtures use real Requests
preparation with an in-memory adapter, including synthetic authentication headers,
redirect security, nested replies, membership shape, pagination, malformed IDs,
partial errors, token rejection and local-search scope. No network account test.
