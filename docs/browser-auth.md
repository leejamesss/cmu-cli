# Local browser-session setup

In the virtual environment from the [install guide](../README.md#install-and-try-it),
install the optional loader from public GitHub (Edge and Chrome supported):

```sh
python -m pip install 'cmu-cli[browser] @ https://github.com/leejamesss/cmu-cli/archive/refs/heads/main.zip'
```

If you have not created a config yet, run `cmu-cli config init --output cmu-cli.json`.
For SIO alone, use the [minimal SIO config](sio.md#browser-setup-no-canvas-configuration-required)
instead; no Canvas configuration is required.

Log in normally in your chosen browser, including any MFA. Then list the cookie
databases that exist at the standard per-browser locations and pick the profile you
signed in with:

```sh
cmu-cli config browsers
```

This prints paths and a configuration block to copy. It opens, reads and decrypts
nothing, and performs no search outside those fixed locations. To find the path
yourself instead, open `edge://version` (or `chrome://version`) and inspect
**Profile Path**. Either way you select that profile's `Cookies` or
`Network/Cookies` database explicitly: no profile is discovered or used
automatically on an authenticated read path. OS permission prompts may occur locally;
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
