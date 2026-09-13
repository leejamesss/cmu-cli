# Discussion material Python APIs

This module adds **Ed thread attachment discovery** and **Piazza supplied-post
attachment parsing**, not a universal Resources downloader. No CLI commands are
added here. All development checks use synthetic payloads and temporary storage;
no browser, credentials, enrolled course, or live provider API was accessed.

## APIs

```python
from cmu_cli.discussion_materials import (
    ed_materials, extract_attachments, piazza_post_materials, download_attachment,
)

# The caller supplies an explicitly configured existing EdClient.
result = ed_materials(ed_client, course_id, page_size=100, max_pages=100)
for item in result["items"]:
    if item["download_url"]:
        saved = download_attachment(item, new_output_directory, max_bytes=10_000_000)

# Pure parser: caller supplies an already-authorized full-post export.
result = piazza_post_materials(post, network_id="configured_network", post_id="observed_id")

# Lower-level pure parser; parent_id identifies a returned reply.
items = extract_attachments(
    "ed", "10", "20", '<a href="/files/notes.pdf">Notes</a>',
    source_url="https://us.edstem.org/api/threads/20", parent_id="30",
)
```

`extract_attachments` understands HTML/XML `a`, `link`, `file`, `img`, and
`image` references using `href`, `src`, or `url`; optional `filename`, `name`,
and `download` attributes supply the name. These are parser conventions, **not
a verified universal Ed attachment-node schema**. Arbitrary JSON `attachments`
fields are intentionally not guessed. Ordinary external hyperlinks remain links,
even when their URL ends in `.pdf`. Exact trusted-origin known file extensions,
explicit file/image/download nodes, and the evidenced Ed static `/files/` prefix
produce candidates. Candidates are not claims that a file is available.

Each item exposes `id`, `platform`, `course_id`, `post_id`, `filename`, `kind`,
`url`, `download_url`, `availability`, and `provenance` (source URL and parent ID).
URLs are resolved relative to the supplied source URL, never HTML `<base>`.
Credentials, ambiguous URLs, HTTP, and unsafe schemes are rejected. Source URLs
must have an exact supported HTTPS origin. Repeated references are deduplicated
within a post; provenance from distinct replies is preserved. Identity includes
platform, course, post and the full fragment-free URL. Signed URL query rotation
**changes identity**; do not silently strip arbitrary query parameters. Separate
posts intentionally retain separate identities, even when linking the same file.

## Ed scope and completeness

`ed_materials` reuses `EdClient.threads` pagination and its partial-error result,
then `EdClient.thread` identity/tree validation. It parses returned content and
document strings in the thread and embedded replies, checks `reply_count`, and
retains earlier successes on inaccessible/malformed details. Duplicate listing
pages, page caps, unauthorized details and missing/mismatched reply counts yield
`complete: false` and constant, sanitized issue reasons. `complete` refers only
to successful enumeration/parsing of these recognized references in that scope;
it does not establish coverage of unknown attachment encodings. Offset feeds
are not a consistent snapshot. No hidden/global-ID enumeration is performed.

Resources, Lessons, Workspaces, gradebooks, and regional CDN variants other than
the evidenced US static origin are **unsupported**. No new Ed endpoint is added.

## Piazza scope and evidence gate

`piazza_post_materials` parses supplied `content`/`subject`, `history` revisions,
and nested `children`, with top-level post/network identity checks. These nested
export conventions are synthetic-fixture tested, not a live payload guarantee.
It deliberately ignores feed `content_snipet`. All supplied revisions are parsed;
this is not an assertion that a revision is the latest. `parsed_supplied_bodies`
reports parser success; `complete` is **always false**, because neither network
coverage, hidden children, nor revision completeness can be proven by an export.

Public unofficial source establishes POST `/logic/api` with method `content.get`
and params `nid`, `cid`, `student_view: null`, and feed-then-detail enumeration.
It does **not** establish that reads preserve unread/view state. Therefore this
change does not widen `PiazzaClient.call`, invoke a new RPC, or claim network-wide
post attachment retrieval. Full authorized post acquisition, sanitized observed
schema validation, unread-state semantics and Resources enumeration remain gates
for a later adapter. The parser is functional, not a placeholder network method.

## Safe download policy

`download_attachment` deliberately accepts **no authenticated session/token**.
It uses fresh `configured_session`, shared `safe_request(..., anonymous=True,
origin=exact_origin)`, shared `bounded_content`, and shared storage
`download_file`/`checked_destination`/`safe_filename`. It does not duplicate the
storage implementation or reuse a Canvas credential context.

Only exact `https://us.edstem.org`, `https://edstem.org`,
`https://static.us.edusercontent.com`, and (for Piazza) `https://piazza.com` are
supported. Provider sets are separate. Arbitrary external URLs, IP-literal
private targets and off-origin redirects are not fetched. Every redirect remains
pinned to its initial exact origin, so another CDN or signed cross-origin handoff
fails closed before transmission. This conservative policy intentionally does
not promise support for every returned Piazza/Ed storage host or authenticated
asset. Trusted provider DNS remains part of the transport trust model; this is
not a general-purpose arbitrary-host SSRF-hardened downloader.

Both advertised and streamed decompressed sizes are bounded (default hard cap
100 MiB, callers may lower it). HTTP failures, HTML/login responses and invalid
PDF signatures are rejected. Files are never executed or unpacked. Output uses
`root/platform/SHA256(identity)/safe_filename`, preventing source-ID/name/path
collisions and traversal. Shared exclusive atomic writes and no-follow storage
protect user files and symlinks. Identical bytes return `unchanged`; changed local
bytes raise `FileExistsError`, never overwrite. Results include checksum, size,
relative path and provenance. Caller owns explicit selection and per-item error
aggregation. No automatic manifest/cache write occurs; caller should protect
provenance URLs, which can contain sensitive signed query strings.

## Public protocol evidence inspected

- [smartspot2/edapi endpoint implementation](https://github.com/smartspot2/edapi/blob/master/edapi/edapi.py): GET course threads, GET thread detail, and static US `/files/` origin. Implementation not copied (GPL).
- [Ed thread types](https://github.com/smartspot2/edapi/blob/master/edapi/types/api_types/thread.py): content/document, answers/comments, reply_count and reply identities.
- [Ed ContentString](https://github.com/smartspot2/edapi/blob/master/edapi/types/api_types/content.py): content is XML text; does not specify every attachment tag.
- [hfaran/piazza-api network](https://github.com/hfaran/piazza-api/blob/master/piazza_api/network.py): full post retrieval differs from feed snippets; iterates feed IDs then details.
- [Piazza RPC source](https://github.com/hfaran/piazza-api/blob/master/piazza_api/rpc.py): exact content.get envelope and transport. Unofficial source is not an official permission or no-side-effect guarantee.

These sources establish protocol facts, not live success. No new Resources
endpoint, attachment JSON schema, CDN wildcard, or auth behavior is inferred.

## Offline verification

```sh
PYTHONPATH=src python3 -m pytest tests/test_discussion_materials.py -q
PYTHONPATH=src python3 -m pytest -q
uvx ruff==0.15.6 check src/cmu_cli/discussion_materials.py tests/test_discussion_materials.py
```

Fixtures exercise real EdClient GET pagination/detail composition, nested replies,
repeated pages, denied/inaccessible posts, partial retention, malformed Piazza
children, link/file distinctions, stable IDs, duplicate provenance, unsafe names,
size limits, HTML/PDF rejection, off-origin redirects, anonymous requests,
non-overwrite/idempotence and symlink refusal.
