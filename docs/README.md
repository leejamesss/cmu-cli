# Documentation

- **Try it without an account:** [synthetic workflow](walkthrough.md), including actual generated output.
- **Configure your courses:** [configuration](configuration.md) and the [example config](../examples/config.example.json).
- **Set up live access:** [authorization](auth.md), including explicit local browser-session configuration.
- **Consume JSON:** [envelope and payload reference](json-contract.md), [JSON Schema](result.schema.json).
- **Extend or debug:** [architecture](architecture.md), [contributor workflow](../CONTRIBUTING.md).
- **Understand boundaries:** [security](../SECURITY.md), [provenance](../PROVENANCE.md), [license](../LICENSE).
- **Command overview and install:** [README](../README.md).

## Output

Interactive output is rendered as tables, with the tri-state submission label coloured
so it reads at a glance. Everything else is unchanged.

The split is on whether stdout is a terminal. Piped, redirected, under a test runner,
with `NO_COLOR`, or with `TERM=dumb`, each command prints exactly the lines it printed
before — Rich is never constructed, so it cannot re-wrap or re-colour them. `--json`
returns before any of this. `CLICOLOR_FORCE=1` forces the interactive rendering.
