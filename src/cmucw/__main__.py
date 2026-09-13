"""Compatibility for python -m cmucw; implementation lives in cmu_cli."""

from cmu_cli.cli import main

raise SystemExit(main())
