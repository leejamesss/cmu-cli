# Canvas own grades and module files

```sh
cmu-cli grades --source canvas --course COURSE --details --json
cmu-cli materials --course COURSE --json
cmu-cli sync --course COURSE
```

`grades` uses only GET requests. It joins assignments with the caller's submissions by assignment ID, verifies the profile user ID on every submission, and preserves score zero, null, letter grades, late/missing/excused flags and stale-attempt grade flags. `--details` requests returned submission comments and rubric assessments, not annotated PDFs. It never requests `read_status`, changes read state, posts grades, enumerates a roster or computes averages. No grades/feedback files are written by this command.

The existing JSON 1.0 envelope and exit codes apply: 0 complete requested reads, 3 source failure/partial, 2 fatal/config/identity failure. `data.assignments` and `data.enrollments` are separate arrays. Enrollment IDs are retained (multiple sections are not averaged). Current/final scores and grades are Canvas whole-course server values, **not official SIO/transcript grades**. Null does not mean zero or unpublished. `release_state` is `posted` only with returned `posted_at`; otherwise `unknown`. Per-field missing/null reasons deliberately do not speculate about instructor release policy. Complete reads do not imply hidden grades exist or are accessible.

Materials now resolve File module items through course-scoped file metadata. Missing/short inline module items trigger the paginated item endpoint; contradictory counts fail the source. File IDs deduplicate across course Files and modules, preserving module IDs, MIME metadata and locks. Non-file module items remain outside this file inventory: their HTML URLs are never fetched because visiting them can satisfy must-view requirements. Existing secure storage, filename selection, manifests, non-overwrite rules and download transport are reused. Unsupported extensions and locked/hidden files have explicit skip reasons. This is not universal crawling or all-platform downloading.

## Official protocol evidence

- [Users](https://canvas.instructure.com/doc/api/users.html): `GET /api/v1/users/:user_id/profile`; `self` identifies the caller.
- [Submissions](https://canvas.instructure.com/doc/api/submissions.html): `GET /api/v1/courses/:course_id/students/submissions`, paginated; omitted `student_ids` means caller only; `submission_comments` and `rubric_assessment` includes. The documented `read_status` include marks read and is deliberately excluded.
- [Enrollments](https://canvas.instructure.com/doc/api/enrollments.html): `GET /api/v1/users/:user_id/enrollments`, StudentEnrollment, `current_and_concluded`, `current_points`. Only allowlisted posted-grade fields are emitted, never `unposted_*`.
- [Modules](https://canvas.instructure.com/doc/api/modules.html): `GET /api/v1/courses/:course_id/modules/:module_id/items`, `include[]=content_details`; inline items can be omitted.
- [Files](https://canvas.instructure.com/doc/api/files.html): `GET /api/v1/courses/:course_id/files/:id`; downloads use returned URLs rather than fabricated paths.

Official documentation plus synthetic tests establish this implementation's intended protocol. No private account execution or real grade/UI comparison was performed for this expansion.

## Remaining work / release gate

The local unversioned `cmucw` workspace adapter has **not** been installed or changed. Its config and private datasets were not loaded. A separately backed-up thin route, explicit config/auth mapping, and installed-engine synthetic parity test remain required; public tests alone do not establish local parity.

Assignment-body/page attachment traversal, feedback-file downloads, finer per-file partial recovery, stronger anonymous-asset SSRF/DNS policy, and exact missing-submission completeness audits remain follow-ons. Gradescope scores/graded copies, Ed attachments/Resources/Lessons, Piazza Resources/attachments and SIO official grades/academic records remain separate unimplemented expansions requiring endpoint evidence. This slice must not be described as completing those platforms.
