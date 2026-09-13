# Configuration

Run `cmu-cli config init --output cmu-cli.json`, then `cmu-cli --config cmu-cli.json config validate --json`. Configuration precedence is explicit `--config`, then `CMU_CLI_CONFIG`, then `~/.config/cmu_cli/config.json`. Relative storage paths are resolved against the config directory, not the shell working directory.

Ed and SIO commands use optional provider-only configuration and do not require
Canvas keys; see [provider configuration and exact commands](provider-cli.md).
Optional `ed_api_base_url` accepts only the two evidenced exact API bases. Full
course records may include `ed_course_id` (canonical positive decimal string or
integer, never inferred from Canvas). The following required keys apply to the
existing Canvas workflows and `config validate`.

Required keys: `canvas_base_url` (plain HTTPS origin), `storage_root` (local directory), `courses` (list). Each course requires nonempty `code`, positive integer `canvas_id`, nonempty `name`, and single-component `directory`. Codes, IDs and directories must be unique. `term` defaults to `current`; term and course directories reject traversal, separators and Windows reserved names. `timezone` defaults to `America/New_York` and must be an IANA zone.

Optional top-level `browser_auth` selects one local browser/profile database and exact hosts; see [browser setup](browser-auth.md). It is absent by default and contains no cookie values.

Optional course keys: `public_site_url`, `piazza_url`, `gradescope_url` (credential-free HTTPS URLs or null), `assessment_year` (1900–9999 or null), `assessment_hour` (0–23). Public-site parsers are course-layout-specific heuristics, not general crawling or guaranteed deadline discovery. Their inferred assessment times use Eastern time, independent of display timezone, and are labeled inferred in CLI quiz JSON.

Do not put credentials in this file. `sync` caches provider metadata and may download private materials; use a private, non-shared directory, avoid cloud backup unless authorized, and delete exports according to your institution's retention rules. Some storage subdirectories retain historical Chinese names for compatibility. `--metadata-only` still writes private metadata and indexes.
