"""Command-line entry point for the issue-to-PR kit."""

from __future__ import annotations

from .run_attempt import main


if __name__ == "__main__":
    raise SystemExit(main())
