# Guided first-time setup

```sh
cmu-cli setup
cmu-cli config validate
cmu-cli status
```

Setup writes `~/.config/cmu_cli/config.json`. For a project-specific file, use
`cmu-cli setup --output ./cmu-cli.json`, then pass `--config ./cmu-cli.json` before
subsequent commands. Existing files, symlinks, and concurrent creations are never
replaced. Materials are not downloaded or moved during setup.

## What the wizard asks

1. **Canvas origin.** CMU's `https://canvas.cmu.edu` is suggested. Enter a plain
   lowercase HTTPS hostname with no path, query, credentials, or nonstandard port.
2. **Browser profile.** Standard Edge/Chrome profiles are listed using filesystem
   metadata only. Select the profile where you are signed in, or skip it. This
   step does not read, copy, or decrypt cookies.
3. **Discovery consent.** If `CMU_CLI_CANVAS_TOKEN` is externally provisioned, the
   wizard asks before using it. Otherwise it asks before reading/decrypting the
   selected profile's cookies. Only GET requests to the confirmed Canvas origin
   are allowed; tokens and cookies never follow cross-origin redirects. Token
   consent denial does not silently fall back to cookies. Browser authentication
   requires the optional [browser extra](browser-auth.md).
4. **Courses.** Choose comma-separated menu numbers or exact course codes. Use
   `#2` to select menu item 2 when a code is ambiguous or numeric. Discovery
   completes all pages before showing results; a denied, failed, malformed or
   incomplete listing falls back to a course code and a pasted Canvas course URL
   such as `https://canvas.cmu.edu/courses/12345`. No course IDs are guessed.
5. **Names and folders.** Confirm suggested course codes, names and safe folders.
   Duplicate codes/folders require explicit disambiguation. Canvas term metadata
   supplies the term when unambiguous; otherwise choose one storage term for the
   selected courses (or use separate configs for different terms). Choose the
   materials root; setup does not create course-material folders.
6. **Other providers.** Canvas tab labels can flag Piazza/Gradescope, but launch
   URLs are never opened or treated as direct course IDs. Paste an optional direct
   `https://piazza.com/class/CLASS_ID` or
   `https://www.gradescope.com/courses/COURSE_ID` URL. The wizard accepts official
   provider hosts and course paths only, without query strings or credentials.
   Each browser host requires separate approval for future reads. No Piazza or
   Gradescope request is made during setup. SIO is a separate optional approval
   for exactly `s3.andrew.cmu.edu`; it does not inherit Canvas consent and setup
   makes no SIO request.
7. **Publish.** The complete configuration passes the same validator as
   `config validate`, then is published atomically and exclusively with private
   permissions only after your final confirmation.

Type `cancel`, press Ctrl-C, or decline the final confirmation to stop without
publishing. An interactive terminal is required; piped input fails with a hint to
use the unchanged manual alternative:

```sh
cmu-cli config init --output cmu-cli.json
# Edit the synthetic template, then:
cmu-cli --config cmu-cli.json config validate
```

## Authentication is not login

Setup does not obtain tokens, collect passwords, perform SSO/MFA, or refresh an
expired session. It uses only an externally supplied token or a profile you
explicitly authorize. Tokens, passwords, and cookie values are never stored in
configuration or printed in provider error messages. Profile paths and approved
hostnames are configuration metadata, not credentials. In token mode, provision
`CMU_CLI_CANVAS_TOKEN` again in the environment of later CLI commands; setup does
not persist it. A browser selected for other providers is not implicitly approved
for Canvas when token discovery is used.

A successfully written config does not prove provider access: discovery may have
fallen back to manual URLs, other providers are not probed, and existing sessions
can expire. Validation and doctor are offline checks. See [Canvas and provider
commands](provider-cli.md), [browser authentication](browser-auth.md), and
[token requirements](auth.md).
