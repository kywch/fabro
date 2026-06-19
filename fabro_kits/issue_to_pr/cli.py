"""Command-line entry point for the issue-to-PR kit."""

from __future__ import annotations

import sys

from . import evidence_gate, light_eval


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "evidence-gate":
        return evidence_gate.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "light-eval":
        return light_eval.main(sys.argv[2:])
    print(
        "usage: python -m fabro_kits.issue_to_pr.cli evidence-gate ... | light-eval ...",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
