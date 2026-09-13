# Migrating from cmucw

The distribution is `cmu-cli`, the primary executable is `cmu-cli`, and the Python
package is `cmu_cli`. `cmucw` and `python -m cmucw` are launch aliases only; change
Python API imports from `cmucw.<module>` to `cmu_cli.<module>`.

For an old environment, uninstall the old distribution **before** installing the
new one, because both distributions own the legacy executable. This removes package
files, not configuration or downloaded coursework. Alternatively use a fresh venv.

```sh
python -m pip uninstall cmucw
python -m pip install "git+https://github.com/leejamesss/cmu-cli.git@main"
cmu-cli --help
cmu-cli demo --json
```

Configuration precedence: explicit `--config`, then `CMU_CLI_CONFIG`, then
`CMUCW_CONFIG`, then `~/.config/cmu_cli/config.json` if it exists, then the existing
`~/.config/cmucw/config.json`. Without either file the new path is reported missing.
A present new environment variable takes precedence over its legacy counterpart,
even when empty. `CMUCW_CANVAS_TOKEN` and `CMUCW_ED_TOKEN` remain supported as
fallbacks for `CMU_CLI_CANVAS_TOKEN` and `CMU_CLI_ED_TOKEN`. No credentials are moved
or persisted. Config init remains explicit and never overwrites an existing file.

Keep your existing config and storage root: per-course `.cmucw` metadata, manifests,
and version archives deliberately retain their old names for both commands. There
is no separate `.cmu-cli` cache, automatic migration, or second implementation.
Existing manifests continue to identify unchanged downloads, avoiding duplicate
fetches or overwriting locally edited materials. Do not delete or rename `.cmucw`.
The original contributor branch's `.cmu-cli` cache format was never released; if you
experimented with that branch, back up both trees and return to the original
`.cmucw` manifest before syncing. Do not combine competing manifests blindly.
