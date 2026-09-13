# Local browser-session setup

Install the optional loader (Edge first, Chrome also supported):

```sh
python -m pip install 'cmu-cli[browser]'
cmu-cli config init --output cmu-cli.json
```

Log in normally in your chosen browser, including any MFA. Open `edge://version`
(or `chrome://version`) and inspect **Profile Path** yourself. Select that profile's
`Cookies` or `Network/Cookies` database explicitly. No browser/profile discovery
or broad automatic scan is performed. OS permission prompts may occur locally;
if access is denied, stop and resolve browser/OS access normally. Never disable TLS
or bypass access controls. Do not paste cookies, tokens or passwords into chat.

Add this non-secret object to your local configuration, replacing the example path
and Canvas hostname; list only hosts you want this tool to authenticate against:

```json
"browser_auth": {
  "enabled": true,
  "browser": "edge",
  "cookie_file": "/absolute/path/to/selected/profile/Cookies",
  "hosts": ["canvas.example.edu", "piazza.com", "www.gradescope.com"]
}
```

The explicit database path is the profile selector. Chrome uses `"browser": "chrome"`.
Use the exact Gradescope hostname in your configured course link (www and non-www
are different origins). Configure `piazza_url` as `https://piazza.com/class/NETWORK_ID`
and `gradescope_url` as `https://www.gradescope.com/courses/COURSE_ID` in each course.
Network IDs come only from those links, never from course-number guesses.

```sh
cmu-cli --config cmu-cli.json config validate --json
cmu-cli --config cmu-cli.json posts --course DEMO-101 --json
cmu-cli --config cmu-cli.json assignments --course DEMO-101 --json
```

Canvas uses `CMU_CLI_CANVAS_TOKEN` if present; browser fallback occurs only if absent
and explicitly configured, never after a rejected token. Remove `browser_auth` or
set `enabled` false to revoke local loader consent. Existing in-memory sessions end
with the process; logout/revocation is managed in the service/browser normally.
No cookie export, credential file, or credential log is created by cmu-cli.
Browser-cookie3 may use local OS key storage to decrypt the selected database.

Piazza reads feed summaries (not full thread histories); pagination is bounded to
100 pages of 100 posts per class. Repeated pages, failure, or limits fail the source
rather than claiming a complete collection. Completeness is the service feed view,
not an atomic snapshot during concurrent changes. Gradescope preserves its student
assignment-table parser; layout/login changes report unavailability, not empty data.
These integrations are covered by synthetic offline tests, not live-account validation.
