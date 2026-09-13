"""Parent-owned manual SSO runner; use PYTHONPATH=src. No existing browser access."""

from cmu_cli.sso_probe import main

if __name__ == "__main__":
    raise SystemExit(main())
