# CMU CLI documentation

## Start using it

- [Install and try the offline demo](../README.md#install-and-try-it)
- [See the walkthrough and generated output](walkthrough.md)
- [Connect Canvas, Piazza, Gradescope, Ed or SIO](provider-cli.md)
- [Configure courses and local storage](configuration.md)
- [Use an explicit Edge/Chrome session](browser-auth.md)
- [Understand token and institutional authorization](auth.md)

## Build your own workflow

- [JSON envelope, payloads and exit codes](json-contract.md) · [JSON Schema](result.schema.json)
- [Ed commands, authentication and search scope](ed.md)
- [Ed attachment listing/download and Piazza parser limits](discussion-materials.md)
- [Gradescope grades and partial feedback](provider-expansion-grades.md)
- [SIO schedule, history and rendered-HTML fallback](sio.md)
- [Architecture and Python modules](architecture.md)
- [Contribute a fix and run the release checks](../CONTRIBUTING.md)
- [Security and private exports](../SECURITY.md) · [Provenance](../PROVENANCE.md) · [MIT license](../LICENSE)

## Terminal output

Interactive output uses tables and colored submission states. When piped or
redirected, under a test runner, with `NO_COLOR`, or with `TERM=dumb`, commands use
plain text instead. `--json` selects structured output independently of terminal
rendering. `CLICOLOR_FORCE=1` forces interactive rendering.
