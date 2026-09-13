# Authorization and credentials

See [local browser-session setup](browser-auth.md) for the shortest Edge/Chrome path.

## Current boundary

Canvas accepts an externally obtained `CMU_CLI_CANVAS_TOKEN` (preferred) or an explicitly selected local browser session. No OAuth login, refresh or revocation is implemented. `auth check-registration` is an optional offline OAuth metadata validator, not a prerequisite for local browser authentication.

Canvas distinguishes personal testing from applications used by other users: do not instruct third-party users to manually generate personal tokens. Applications used by multiple users must obtain tokens through OAuth. Provider approval for agent/MCP use must not be inferred from JSON output or read-only intent. This package has no MCP server or automatic model data transfer.

## What the official documentation establishes

- [OAuth overview](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth): institutional administrators issue hosted Canvas developer keys; the documented confidential flow exchanges a code with a client ID and secret. Never distribute a shared confidential secret inside a CLI, wheel, repository or example.
- [OAuth endpoints](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth_endpoints): the native-app redirect is currently documented as `urn:ietf:wg:oauth:2.0:oob`. The response lands on Canvas `/login/oauth2/auth` with a code. State must correlate the authorization response with the initiating request, including error responses. Do not assume localhost, random loopback ports or custom URI schemes are accepted merely because generic OAuth libraries support them.
- **Important documentation difference:** the newer [Developer Keys API](https://developerdocs.instructure.com/services/canvas/resources/developer_keys) explicitly documents immutable `client_type=public`, mandatory PKCE, short-lived access tokens and **rotating refresh tokens**. The OAuth overview/endpoints still describe required client secrets and reusable non-rotating refresh tokens. Therefore it is incorrect to claim Canvas categorically lacks public clients or PKCE, and equally incorrect to assume every institutional deployment supports the new public-client contract.
- [Developer-key administration](https://developerdocs.instructure.com/services/canvas/oauth2/file.developer_keys): the key must be enabled in the target account; scopes are administrator-controlled. Scoped requests using `include` parameters need the key's `allow_includes` option. An LTI key/client-credentials grant is not a replacement for a user's Canvas REST API authorization.

## Registration prerequisites for the institution/operator

Obtain these facts **before selecting and implementing the deployment-specific login flow**:

1. Exact Canvas HTTPS origin and root account; an enabled **Canvas API** developer key, client ID, and explicit permission for this application's distribution and intended data use. Confirm whether the key is restricted to test/beta.
2. Registered `client_type` and deployed support for PKCE S256, secretless exchange if public, and refresh rotation/reuse behavior. Do not infer these from the latest upstream API documentation alone. Ask the administrator/provider to reconcile the documentation difference for this installation.
3. Exact registered redirect URI and confirmation of how a native client receives the **full response including state**, not just a pasted code. OOB is the documented native baseline; require explicit deployment/provider evidence before adopting a bounded loopback listener. Never collect codes, secrets or tokens in chat or command-line arguments.
4. Least-privilege GET endpoint scopes for enabled commands (for example `url:GET|/api/v1/courses` for listing courses), plus `allow_includes` where needed. Have the administrator verify endpoint existence and coverage; syntactic scope validation is not scope approval.
5. For a confidential key, an institution-operated HTTPS broker that retains the secret, or an explicitly institution-approved, separately provisioned per-installation secret-custody model. No shared-secret distribution. A broker also requires an agreed CLI handoff protocol; an arbitrary HTTPS callback alone is not that protocol.
6. An authorized test account/environment and permission to validate consent/denial, missing/wrong/replayed state, expiry, refresh, revocation, account/domain isolation and data-access scope. No such registration or live validation has been supplied for this candidate.

A public key with confirmed S256 support is the preferred prospective native design: random one-use state and verifier, bounded authorization lifetime, exact response-origin/path checks, constant-time state validation, no secret fallback, and one serialized refresh writer with atomic replacement of rotated tokens. A confidential deployment must retain secrets under its approved custody model. Both need private OS-backed or audited POSIX storage, no symlink following, restrictive permissions, no tokens in URLs/logs, no credential-forwarding redirects, and honest remote revocation results. These are acceptance requirements, **not implemented features**.

The confidential lifecycle documented in the OAuth overview uses `POST /login/oauth2/token` for code exchange and refresh; its refresh response omits a new refresh token. Public-key documentation instead requires rotating refresh tokens. Logout uses authenticated `DELETE /login/oauth2/token`; do not request `expire_sessions=1` or revoke unrelated sessions by default. Removing a local file is not remote revocation.

## Offline registration preflight

Create a separate non-secret JSON file; do not export the raw DeveloperKey API object (it can contain `api_key`). All fields below are required. This synthetic example describes a proposed public native registration, **not a registered or enabled application**:

```json
{
  "canvas_base_url": "https://canvas.example.edu",
  "client_id": "12345",
  "client_type": "public",
  "deployment": "native",
  "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
  "scopes": ["url:GET|/api/v1/courses"],
  "pkce_method": "S256",
  "secret_custody": "none"
}
```

```bash
cmu-cli auth check-registration --registration registration.json --json
```

No normal coursework configuration is needed. Only the explicitly supplied file is read, bounded to 16 KiB; no token environment variable, browser, credential store, Keychain or network is used. Duplicate/unknown fields (including secrets), malformed IDs/origins, non-GET/duplicate scopes, symlinks and non-regular files are rejected. Values are not echoed. Keep all secrets out of this metadata file.

- Exit **2**: invalid/unreadable metadata, with a fixed `OAUTH_REGISTRATION_*` error code. `FIELDS` means missing or extra fields; `REDIRECT`, `PKCE`, `SECRET_CUSTODY`, `SCOPES`, `CLIENT_ID`, `CLIENT_TYPE`, `DEPLOYMENT` and `ORIGIN` identify the rejected category.
- Exit **3**: metadata is syntactically valid, but readiness is blocked. JSON has `status=partial`, `complete=false`, `registration_verified=false`, `oauth_implemented=false` and explicit blockers. There is deliberately no successful-login claim or readiness exit 0.

For confidential metadata set `client_type` to `confidential`; `pkce_method` may be `S256` or `none` as confirmed by the provider. Native deployment requires the documented OOB redirect and custody `institution_approved_per_installation`; server deployment requires a credential-free HTTPS redirect without query/fragment and custody `institution_server`. Public clients require `S256` and custody `none`. These combinations validate a registration proposal, not actual provider support, authorization or secret availability. The validator deliberately rejects undocumented native redirect alternatives until that contract is resolved.

## Other integrations and privacy

Use only your own authorized account and comply with applicable [Piazza terms](https://piazza.com/legal/terms), [Gradescope terms](https://www.gradescope.com/tos) and institutional rules; the MIT license does not grant service or course-data rights.

Do not store tokens in config, commit credentials, paste signed URLs into public issues, or upload course exports to an AI/cloud service without necessary permissions. CLI errors use safe codes, not raw exception messages. Successful JSON and local caches intentionally contain private metadata: they are not sanitized logs. See the [Canvas API policy](https://www.instructure.com/policies/canvas-api-policy).

`config validate`, `doctor`, `demo`, `auth check-registration` and `--version` require no authentication. Doctor does not read token values, cookie databases or keychains and never validates a live session. `open` uses the default browser and configured URLs, not an authenticated API session.
