"""Command-line entry point for the issue-to-PR kit."""

from __future__ import annotations

import sys

from . import evidence_gate
from .run_attempt import main as run_attempt_main


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "evidence-gate":
        return evidence_gate.main(sys.argv[2:])
    return run_attempt_main()


if __name__ == "__main__":
    raise SystemExit(main())
