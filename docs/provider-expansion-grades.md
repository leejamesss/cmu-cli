# Gradescope grade discovery and SIO evidence gate

Read the configured student assignment table with existing explicit browser auth:

```sh
cmu-cli --config cmu-cli.json grades --source gradescope --course DEMO-101 --json
cmu-cli --config cmu-cli.json grades --source gradescope --course DEMO-101 --details --json
```

Use the normal full course configuration and exact `gradescope_url` described in
[provider setup](provider-cli.md#piazza-and-gradescope). No Canvas session is opened.
Omit `--course` to read configured courses; missing Gradescope configuration is
reported unavailable, not silently skipped. Scores and maxima come only from
unambiguous released decimal pairs; zero remains zero and unknown remains null.
Plain output shows score, maximum, release and submission state. JSON uses the
common envelope with `data.assignments`, empty `data.enrollments` and explicit scope.

`--details` follows deduplicated, observed same-course student links only. Every
request and redirect is pinned to its exact authorized URL, so login/other-course
redirects fail before the next request. Rendered feedback is always partial
(exit 3); complete question associations/all attempts are not available. Links
labeled Download Graded Copy are discovery only: no grade-artifact download is
implemented. Failed courses retain other courses' results and exit 3.

No authenticated account, browser database, report generation or student record
was accessed during development. Tests use synthetic HTML/transport responses;
this does not establish live grade or rubric completeness.

## Implemented Python APIs

Module: `cmu_cli.gradescope_details`. Results are plain dictionaries, not a second
canonical model. The CLI wraps them in the common versioned result envelope.

- `parse_score(status: str) -> dict`: `score`, `points_possible` (float or null),
  `release_state` (`released`, `not_released`, `unknown`). Only a complete decimal
  `earned / possible` status is numeric; zero is retained, extra credit is not
  clamped, commas/percent/ambiguous or overflowing values remain unknown.
  Exact normalized `Submitted` means not released. `Graded` alone is not numeric
  release evidence in this course-row parser.
- `parse_course_grades(course: Course, html: str) -> list[dict]`: each row has
  `source=gradescope`, `course` (configured code), `course_id`, `assignment_id`
  (observed attribute or null), `name`, raw `status`, score fields above,
  `submission_state` (`submitted`, `not_submitted`, `unknown`), `url` (first
  observed permitted link or null), `detail_links`, `provenance={url,selector}`,
  and `unavailable_reason` (null, `not_released`, or
  `no_unambiguous_score_in_status`). No fallback route is synthesized. An empty
  table produces `[]`; a missing table/login form raises `GradescopeError`.
- `read_course_grades(session, course: Course) -> list[dict]`: caller supplies an
  explicitly authorized requests-compatible session; reads only the already
  established configured course URL using existing exact-origin `safe_request`
  and bounded document transport. No cookie discovery, browser, file writes or
  detail-link following. Errors are sanitized; responses close on failure.
- `parse_submission_details(course, submission_url, html) -> dict`: accepts
  caller-supplied authorized rendered HTML and an **observed** same-course
  assignment/submission URL. Caller must establish provenance/own visibility;
  syntactic URL validation alone cannot establish identity or authorization.
  Fields: `source`, `course`, `url`, `question_titles` (strings), `rubric_items`
  (`description`, raw `points_label`, tri-state `applied`, `question=null`),
  `materials`, `status=partial`, `score=null`, `points_possible=null`,
  `feedback_availability` (`observed` or `unknown`), `unavailable_reason`, and
  `provenance={url,mode:caller_supplied_html}`. No question association or rubric
  arithmetic is invented. Unapplied items remain explicitly false; missing
  accessibility labels remain unknown, never implicitly applied.

Each material contains `source`, `kind=graded_submission`, `url`, `label`,
`source_url`, `availability=observed_link_not_verified`, `credential_policy`
(`same_origin_only` or `anonymous_only`), and `download_verified=false`.
Only observed anchors labeled `Download Graded Copy` are classified, based on
public source evidence. Non-GET `data-method` links and inline/attribute-hidden
content are ignored. No generated PDF routes, submission exports or arbitrary
resource endpoints are guessed. Arbitrary external HTTPS links can be represented
but **are not approved network targets**. A future downloader must apply the
shared asset anti-SSRF/redirect/size/signature/storage policy; authenticated
sessions must never follow cross-origin assets. These APIs do not download them.

## Evidence and limits

- Existing `gradescope_client.py` at base `92188c4` establishes
  `#assignments-student-table`, `[data-assignment-title]`, `th[scope=row]`,
  `.submissionStatus--text` and the configured course GET.
- [Official Gradescope student guide](https://guides.gradescope.com/hc/en-us/articles/21856421392525-Understand-Your-Submission-Feedback-and-Grades)
  confirms Submitted = unpublished, a score = published, visible question
  feedback/rubrics, and graded-copy UI. It does not document network schemas.
- Public protocol reconnaissance of
  [Grade-Saver/webScrape.py](https://github.com/Shrish-M/Grade-Saver/blob/main/webScrape.py)
  independently establishes `.submissionOutlineQuestion--title`,
  `.submissionOutlineRubricItem`, `.submissionOutlineRubricItem--description`,
  `span.sr-only` applied/unapplied labels, points `aria-label`, and the actual
  `Download Graded Copy` anchor lookup. No source implementation was copied;
  this parser is independently authored using selector observations. That script
  clicks questions before reading rubrics, so passive HTML cannot promise all
  question feedback. We intentionally do not adopt its missing-label inference,
  browser/session handling, URL concatenation or PDF modifications.

Rendered visibility is limited to `hidden`, `aria-hidden`, inline display and
visibility; CSS and JavaScript are not evaluated. No React props are decoded.
No total/question score selector is proven, so detail scores remain null.
Comments separate from rubric descriptions, original submission files,
programming/online artifacts, all attempts and rubric-question binding remain
unsupported. Grade records are not cached by these APIs.

## SIO: blocked on exact read-only navigation evidence

[Official SIO about page](https://www.cmu.edu/hub/sio/about.html) confirms grades,
QPA and enrollment viewing; its SIO navigation link is
`https://s3.andrew.cmu.edu/sio/mpa/`. The
[official academic-record page](https://www.cmu.edu/hub/registrar/student-records/academic.html)
links its UAR text to `https://s3.as.cmu.edu/sio/index.html`, with the same
`https://s3.andrew.cmu.edu/sio/mpa/` footer entry. Neither inspected page provides
an actual grade view or UAR retrieval endpoint. The linked
`https://www.cmu.edu/hub/docs/review-record.pdf` is a **request-to-review form**,
not the student's academic record. It must not be substituted for UAR.
CMU says it does not produce unofficial transcripts; currently enrolled students
may request an Unofficial Academic Record, including classes without final grades.
No `sio_records.py`, command, guessed route or fabricated record fixture is added.

Before implementing SIO grades/UAR, obtain explicit consent for narrowly scoped
live navigation, then record sanitized evidence of:

1. Exact grades/QPA navigation href, GET request URL/method and redirects; selected
   semester and own-student context, without exporting cookies or identifiers.
2. Sanitized rendered/response structure identifying course, units, final versus
   midsemester grade, term/cumulative QPA, missing grades and empty states.
3. The exact UAR navigation link and whether it displays an existing report or
   generates one; request method, returned link, content type, and redirect origins.
4. An existing download's GET semantics and anonymous signed-asset handoff, if any.
   A generation POST requires separate scope; transcript ordering/payment is out
   of bounds. Never treat a guessed route, navigation landing page, login page or
   the public request form as a functioning records adapter.

## Integration and live gate

CLI integration preserves the existing assignment parser schema. Detail parsing
remains partial until consented sanitized student DOM verifies exact question
associations, comments and artifact availability. Source and installed-wheel CLI
fixtures test dispatch, unknown/zero values, authorization, exact-link restrictions
and partial reporting; they do not establish live account success.
