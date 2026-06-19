"""CLI for lightweight issue-to-PR evals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .issue_workflow_smoke import run_issue_workflow_smoke
from .paths import DEFAULT_SYNTHETIC_DOCKER_IMAGE
from .replay import run_replay
from .synthetic import run_synthetic
from .workflow_smoke import run_workflow_smoke


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m fabro_kits.issue_to_pr.light_eval",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay = subparsers.add_parser("replay")
    replay.add_argument("--fixture", default="all")
    replay.add_argument("--output-dir", type=Path)

    synthetic = subparsers.add_parser("synthetic")
    synthetic.add_argument("--task", default="all")
    synthetic.add_argument("--output-dir", type=Path)
    synthetic.add_argument("--sandbox", choices=("local", "docker"), default="local")
    synthetic.add_argument("--docker-image", default=DEFAULT_SYNTHETIC_DOCKER_IMAGE)

    workflow_smoke = subparsers.add_parser("workflow-smoke")
    workflow_smoke.add_argument("--output-dir", type=Path)
    workflow_smoke.add_argument("--fabro-bin", type=Path, default=Path("target/debug/fabro"))

    issue_workflow_smoke = subparsers.add_parser("issue-workflow-smoke")
    issue_workflow_smoke.add_argument("--output-dir", type=Path)
    issue_workflow_smoke.add_argument("--fabro-bin", type=Path, default=Path("target/debug/fabro"))

    args = parser.parse_args(argv)
    if args.command == "replay":
        try:
            report = run_replay(args.fixture, output_dir=args.output_dir)
        except SystemExit as exc:
            replay.error(str(exc))
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if not report["failures"] else 1
    if args.command == "synthetic":
        try:
            report = run_synthetic(
                args.task,
                output_dir=args.output_dir,
                sandbox=args.sandbox,
                docker_image=args.docker_image,
            )
        except SystemExit as exc:
            synthetic.error(str(exc))
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if not report["failures"] else 1
    if args.command == "workflow-smoke":
        report = run_workflow_smoke(output_dir=args.output_dir, fabro_bin=args.fabro_bin)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if not report["failures"] else 1
    if args.command == "issue-workflow-smoke":
        report = run_issue_workflow_smoke(output_dir=args.output_dir, fabro_bin=args.fabro_bin)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if not report["failures"] else 1
    return 2
