# SSO integration release blockers

This is a **draft**, not an assertion that SIO login works. Do not merge #19 or
this integration merely because offline CI is green.

## Evidence available without an account

Anonymous HTTPS GETs (no saved browser profile, credentials, or account access)
observed the following pre-authentication chain:

- `https://s3.andrew.cmu.edu/sio/` → `/sio/s3Login` →
  `https://login.cmu.edu/idp/profile/SAML2/Redirect/SSO`.
- The visible username/password form posts back to that same IdP endpoint with
  a dynamic `execution` query parameter. The observed stylesheets were exactly
  `/css/cmu-general-purpose.css`, `/css/login.css`, `/css/ie.css`, `/css/iphone.css`
  on `login.cmu.edu`. No credential was entered or submitted.
- Public [SIO SP metadata](https://s3.andrew.cmu.edu/Shibboleth.sso/Metadata)
  advertises the SAML2 HTTP-POST assertion consumer at
  `https://s3.andrew.cmu.edu/Shibboleth.sso/SAML2/POST`.
- CMU [Web Login configuration](https://www.cmu.edu/computing/services/security/identity-access/authentication/how-to/provider-configure.html)
  documents the exact institution IdP `https://login.cmu.edu/idp/shibboleth`.

No SAML payload, relay state, execution value, cookie value, password, MFA code,
or private page content is included in these notes or tests.

## Implemented defensive work

- Config and provider config are constructed with keywords. `sso-login` does not
  require unrelated Canvas/course/storage config.
- Only the three existing exact SIO page targets are accepted initially and at
  completion; scheme, userinfo, port, fragments, encoded paths, and arbitrary
  destinations are rejected. No `*.cmu.edu`/`*.duosecurity.com` trust.
- Context-wide routing restricts methods, origins, resource types, frame, and
  paths. Each fetch explicitly sets `max_redirects=0` before fulfillment rather
  than relying on `route.continue_` to re-intercept redirects. Service workers,
  downloads, subframes, popups and WebSockets are restricted. Read contexts disable
  JavaScript and do not silently reuse IdP/MFA sessions.
- Browser lifecycle failures become fixed-message `SsoError` exceptions with
  suppressed chaining; cleanup independently closes context and browser.
- State is re-filtered both on save and read. No origins/localStorage/IndexedDB,
  IdP cookies, domain-wide cookies, unrelated application cookies, or insecure
  cookies are retained. Candidate cookie names/path scopes are intentionally
  narrow; the filtered pair was sufficient in the resumed-session probe below.
- Live reads initially failed because blocked presentation assets were fatal.
  Read mode now aborts GET stylesheet/image/font requests without transmitting
  them and without making their absence fatal. No CDN, analytics, or arbitrary
  origin has been added to the allowlist. Login mode and active request failures
  remain fail-closed; JavaScript stays disabled for reads. Synthetic regressions
  cover passive suppression and continued script/document/POST rejection.
- State files are created with exclusive/no-follow 0600 permissions before writing,
  using random names in a pinned directory descriptor. All directory components
  are checked, the final directory must be private, target links/hardlinks and
  unsafe files are rejected, and failed writes clean up only their own temp file.

## Still blocking release

1. **Institution-specific Duo/MFA endpoints are not available in the anonymous
   pre-login page.** There is no evidence for the precise tenant origin, iframe
   behavior, GET/POST targets, or protocol-required resources after password
   submission. The draft currently rejects these unknown requests, including
   subframes. This can break legitimate MFA; widening to a guessed Duo/CMU
   wildcard would instead undermine the security boundary. Need sanitized
   institution documentation or a user-supervised protocol review, never copied
   credential values or a credential-bearing HAR in chat/repository.
2. **Real-engine request isolation remains unverified.** Synthetic route doubles
   verify the policy callbacks and fail paths, not Chromium redirect semantics,
   service-worker/frame races, popup first requests, or non-HTTP browser channels.
   Before merge, add an isolated synthetic Chromium harness (no real profiles or
   accounts) with an upstream request ledger and adversarial redirect/script/form,
   WebSocket, worker, popup, and wrong-final-page cases. No policy bypass to make
   those tests pass.
3. **Resumed reads verified; fresh login termination remains unverified.** A
   user-authorized, manually authenticated dedicated browser supplied two cookies
   accepted by the existing exact SIO cookie filter. A fresh isolated transport
   context using an installed wheel successfully read both supported views with
   JavaScript disabled. The semester schedule parsed 3/3 rows, complete, with all
   schedule fields available; waitlist history parsed 1/1 row, complete, with its
   optional confirmation date absent. Both selected terms were available, page
   identity passed, and neither parser emitted warnings. No private row content
   or cookie values were retained in repository files. Temporary 0700/0600 scoped
   state was removed after each probe; the user's original browser remained open.
   This proves sufficiency of the filtered pair for this resumed session, not
   necessity of each cookie or a successful instrumented IdP/Duo login. Login
   termination can still mistake an unauthenticated same-URL page for success.
   Reads redirected to the IdP still fail closed as transport failure; refine
   that into `login_required` only once the blocked-auth transition is explicitly
   distinguished from a hostile/unknown request.

4. **Browser response resource limits are incomplete.** Stored-state reads are
   bounded, but Playwright `route.fetch` buffers responses before fulfillment and
   the parser's later truncation does not bound browser/response memory. The
   request timeout is not a total login deadline or a response-byte limit. A
   bounded response strategy and tests remain required before release.

The draft is useful repair work, not a substitute for these acceptance criteria.
Main must stay unchanged and PR #19 must remain open until they are satisfied.
