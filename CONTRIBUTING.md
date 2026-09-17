# Contributing

Bug reports, feature requests and pull requests are all welcome.

## Reporting a bug

Open an issue and include:

- What you expected to happen, and what actually happened.
- Your Home Assistant version and how you installed the integration.
- The relevant log output. Enable debug logging first (see below).
- The integration's diagnostics, downloaded from the device page. Credentials,
  account identifiers, serial numbers and your address are redacted before the
  file is written, so it is safe to attach.

Never paste your Emerald password or a bearer token into an issue.

## Development environment

The repository ships a devcontainer. Open it in VS Code and the environment
builds itself. Otherwise:

```bash
scripts/setup
```

This installs Poetry and the `dev` and `test` dependency groups into `.venv`.

### Running the checks

```bash
scripts/lint     # ruff check --fix, then ruff format
scripts/test     # the full pytest suite
```

Both run in CI on every pull request, alongside hassfest and HACS validation.
The test suite is fully mocked and needs no Emerald account.

### Running Home Assistant

```bash
scripts/develop
```

This starts a throwaway Home Assistant instance on
[localhost:8123](http://localhost:8123) with the integration loaded and debug
logging already enabled for it via `config/configuration.yaml`.

## Pull requests

1. Branch from `main`.
2. Add or update tests. Anything touching the API client or the statistics
   import needs coverage; those are the parts that fail quietly.
3. Make sure `scripts/lint` and `scripts/test` both pass.
4. Update the README if you changed behaviour a user would notice.

## A note on the API

The Emerald cloud API is undocumented and reverse engineered. It is not ours,
and it belongs to a company that never agreed to serve us. Please do not
increase the polling frequency, add retry storms, or add endpoints that have
not been observed in the wild.
