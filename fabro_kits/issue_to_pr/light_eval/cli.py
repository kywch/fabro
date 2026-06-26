"""CLI for lightweight issue-to-PR evals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .issue_workflow_smoke import run_issue_workflow_smoke
from .mini_swe import run_mini_swe
from .paths import DEFAULT_SYNTHETIC_DOCKER_IMAGE
from .prompt_review import run_prompt_review
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

    mini_swe = subparsers.add_parser("mini-swe")
    mini_swe.add_argument("--case", default="all")
    mini_swe.add_argument("--suite", choices=("dev", "locked", "shadow", "all"), default="all")
    mini_swe.add_argument("--output-dir", type=Path)
    mini_swe.add_argument(
        "--attempt",
        choices=("scripted", "model"),
        default="scripted",
    )
    mini_swe.add_argument("--substrate", choices=("local", "docker"), default="local")
    mini_swe.add_argument("--fabro-bin", type=Path, default=Path("target/debug/fabro"))
    mini_swe.add_argument("--docker-image", default=DEFAULT_SYNTHETIC_DOCKER_IMAGE)
    mini_swe.add_argument("--model")
    mini_swe.add_argument("--provider")
    mini_swe.add_argument(
        "--workflow-timeout-seconds",
        type=int,
        default=600,
        help="Timeout for each mini-SWE model workflow run.",
    )
    mini_swe.add_argument(
        "--credential-bridge",
        choices=("off", "openai-codex"),
        default="off",
        help="Copy selected model credentials into the throwaway mini-SWE storage.",
    )
    mini_swe.add_argument(
        "--auth-storage-dir",
        type=Path,
        help="Source Fabro storage root for --credential-bridge openai-codex.",
    )
    mini_swe.add_argument(
        "--credential-preflight",
        action="store_true",
        help="Run `fabro model test` before a mini-SWE model attempt.",
    )
    mini_swe.add_argument("--seed", type=int)
    mini_swe.add_argument("--format", choices=("json", "text"), default="json")
    mini_swe.add_argument("--fail-fast", action="store_true")

    prompt_review = subparsers.add_parser("prompt-review")
    prompt_review.add_argument("--case", default="all")
    prompt_review.add_argument("--output-dir", type=Path)
    prompt_review.add_argument("--fabro-bin", type=Path, default=Path("target/debug/fabro"))
    prompt_review.add_argument("--provider")
    prompt_review.add_argument("--model")
    prompt_review.add_argument(
        "--credential-bridge",
        choices=("off", "openai-codex"),
        default="off",
        help="Copy selected model credentials into the throwaway prompt-review storage.",
    )
    prompt_review.add_argument(
        "--auth-storage-dir",
        type=Path,
        help="Source Fabro storage root for --credential-bridge openai-codex.",
    )
    prompt_review.add_argument(
        "--credential-preflight",
        action="store_true",
        help="Run `fabro model test` before prompt-review model execution.",
    )
    prompt_review.add_argument(
        "--review-mode",
        choices=("adversarial", "moderated"),
        default="adversarial",
        help="Run only adversarial recall, or also run moderator-filter calibration.",
    )
    prompt_review.add_argument("--format", choices=("json", "text"), default="json")

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
    if args.command == "mini-swe":
        try:
            report = run_mini_swe(
                args.case,
                suite=args.suite,
                output_dir=args.output_dir,
                attempt=args.attempt,
                substrate=args.substrate,
                fabro_bin=args.fabro_bin,
                docker_image=args.docker_image,
                model=args.model,
                provider=args.provider,
                workflow_timeout_seconds=args.workflow_timeout_seconds,
                credential_bridge=args.credential_bridge,
                auth_storage_dir=args.auth_storage_dir,
                credential_preflight=args.credential_preflight,
                seed=args.seed,
                fail_fast=args.fail_fast,
            )
        except SystemExit as exc:
            mini_swe.error(str(exc))
        _print_report(report, output_format=args.format)
        return 0 if not report["failures"] else 1
    if args.command == "prompt-review":
        try:
            report = run_prompt_review(
                args.case,
                output_dir=args.output_dir,
                fabro_bin=args.fabro_bin,
                provider=args.provider,
                model=args.model,
                credential_bridge=args.credential_bridge,
                auth_storage_dir=args.auth_storage_dir,
                credential_preflight=args.credential_preflight,
                review_mode=args.review_mode,
            )
        except SystemExit as exc:
            prompt_review.error(str(exc))
        _print_prompt_review_report(report, output_format=args.format)
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


def _print_report(report: dict, *, output_format: str = "json") -> None:
    if output_format == "text":
        print(
            "mini-swe: "
            f"total={report.get('total', 0)} "
            f"failed={report.get('failed', 0)} "
            f"patch_pass={report.get('patch_pass', 0)} "
            f"artifact_pass={report.get('artifact_pass', 0)} "
            f"export_pass={report.get('export_pass', 0)} "
            f"truthful_pass={report.get('truthful_pass', 0)} "
            f"hand_wavy_pass={report.get('hand_wavy_pass', 0)} "
            f"false_exports={report.get('false_exports', 0)} "
            f"process_blocked={report.get('process_blocked', 0)} "
            f"b2_eligible={report.get('b2_eligible', 0)}"
        )
        failures = report.get("failures")
        if failures:
            print(json.dumps(failures, indent=2, sort_keys=True))
        return
    print(json.dumps(report, indent=2, sort_keys=True))


def _print_prompt_review_report(report: dict, *, output_format: str = "json") -> None:
    if output_format == "text":
        print(
            "prompt-review: "
            f"total={report.get('total', 0)} "
            f"failed={report.get('failed', 0)} "
            f"process_failed={report.get('process_failed', 0)} "
            f"artifact_missing={report.get('artifact_missing', 0)} "
            f"artifact_invalid={report.get('artifact_invalid', 0)} "
            f"prompt_miss={report.get('prompt_miss', 0)} "
            f"artifact_valid={report.get('artifact_valid', 0)} "
            f"row_recall={report.get('row_recall', 0):.3f} "
            f"not_hidden={report.get('not_hidden', 0)} "
            f"precision={report.get('precision', 0):.3f} "
            f"moderator_prompt_miss={report.get('moderator_prompt_miss', 0)} "
            f"moderator_artifact_valid={report.get('moderator_artifact_valid', 0)} "
            "moderator_expected_open_recall="
            f"{report.get('moderator_expected_open_recall', 0):.3f}"
        )
        failures = report.get("failures")
        if failures:
            print(json.dumps(failures, indent=2, sort_keys=True))
        return
    print(json.dumps(report, indent=2, sort_keys=True))
