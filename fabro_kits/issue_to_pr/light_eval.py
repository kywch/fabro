"""Lightweight issue-to-PR eval replay harness."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from .artifacts import (
    build_prediction_record,
    load_or_init_manifest,
    run_id_for_task,
    update_manifest_for_run,
    write_manifest,
    write_run_bundle,
)
from .review_accountability_gate import (
    evaluate_review_accountability,
    load_json_object,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "tier1_artifact_replay"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m fabro_kits.issue_to_pr.light_eval",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay = subparsers.add_parser("replay")
    replay.add_argument("--fixture", default="all")
    replay.add_argument("--output-dir", type=Path)

    args = parser.parse_args(argv)
    if args.command == "replay":
        report = run_replay(args.fixture, output_dir=args.output_dir)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if not report["failures"] else 1
    return 2


def run_replay(
    fixture: str = "all",
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    fixture_dirs = list_fixtures(fixture)
    if output_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return _run_replay_to_dir(fixture_dirs, Path(tmp))
    output_dir.mkdir(parents=True, exist_ok=True)
    return _run_replay_to_dir(fixture_dirs, output_dir)


def list_fixtures(fixture: str) -> list[Path]:
    if fixture == "all":
        return sorted(path for path in FIXTURE_ROOT.iterdir() if path.is_dir())
    path = FIXTURE_ROOT / fixture
    if not path.is_dir():
        raise SystemExit(f"unknown replay fixture: {fixture}")
    return [path]


def _run_replay_to_dir(fixture_dirs: list[Path], output_dir: Path) -> dict[str, Any]:
    results = []
    failures = []
    manifest = load_or_init_manifest(output_dir)

    for fixture_dir in fixture_dirs:
        case_result = run_replay_fixture(fixture_dir, output_dir=output_dir)
        results.append(case_result["result"])
        update_manifest_for_run(
            manifest,
            task_id=case_result["task_id"],
            run_id=case_result["run_id"],
            attempt_id="001",
            output_dir=output_dir,
        )
        failures.extend(case_result["failures"])

    write_manifest(output_dir, manifest)
    (output_dir / "results.jsonl").write_text(
        "".join(json.dumps(result, sort_keys=True) + "\n" for result in results)
    )
    (output_dir / "predictions.jsonl").write_text(
        "".join(json.dumps(build_prediction_record(result), sort_keys=True) + "\n" for result in results)
    )
    summary = {
        "total": len(results),
        "failed": len(failures),
        "false_exports": sum(1 for failure in failures if failure["kind"] == "false_export"),
        "failures": failures,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def run_replay_fixture(fixture_dir: Path, *, output_dir: Path) -> dict[str, Any]:
    expected = read_json(fixture_dir / "expected.json")
    input_dir = fixture_dir / "input"
    task_id = expected.get("task_id") or fixture_dir.name
    patch = (input_dir / "patch.diff").read_text()

    adversarial, adversarial_error = load_json_object(input_dir / "adversarial_review.json")
    moderator, moderator_error = load_json_object(input_dir / "moderator_filter.json")
    test_gate, test_gate_error = load_json_object(input_dir / "test_evidence_gate.json")
    materialization, materialization_error = load_json_object(
        input_dir / "review_materialization.json"
    )
    settings_ref_diff = read_optional_text(input_dir / "settings_ref.diff")
    gate = evaluate_review_accountability(
        adversarial=adversarial,
        moderator=moderator,
        test_gate=test_gate,
        materialization=materialization,
        adversarial_error=adversarial_error,
        moderator_error=moderator_error,
        test_gate_error=test_gate_error,
        materialization_error=materialization_error,
        settings_ref_diff=settings_ref_diff,
    )
    status = "completed" if gate["route_decision"] == "export" else "failed"
    result = {
        "instance_id": task_id,
        "model_name_or_path": "light-eval-replay",
        "model_patch": patch,
        "status": status,
        "error": gate.get("failure_reason") if status != "completed" else None,
        "duration_s": 0,
        "fabro_run_id": None,
        "fabro_dump_dir": None,
        "trajectory_path": None,
        "test_evidence_gate": test_gate,
        "adversarial_review": adversarial,
        "moderator_filter": moderator,
        "review_materialization": materialization,
        "review_accountability_gate": gate,
    }

    config_dir = prepare_config_dir(output_dir, task_id)
    write_run_bundle(
        instance=instance_for_task(task_id),
        result=result,
        output_dir=output_dir,
        config_dir=config_dir,
        sandbox_provider="light-eval",
    )
    run_id = run_id_for_task(task_id)
    failures = check_expected(
        expected,
        output_dir=output_dir,
        run_id=run_id,
        result=result,
        gate=gate,
        patch=patch,
    )
    return {
        "task_id": task_id,
        "run_id": run_id,
        "result": result,
        "failures": failures,
    }


def check_expected(
    expected: dict[str, Any],
    *,
    output_dir: Path,
    run_id: str,
    result: dict[str, Any],
    gate: dict[str, Any],
    patch: str,
) -> list[dict[str, Any]]:
    failures = []
    expected_reasons = set(expected.get("expected_failure_reasons") or [])
    actual_reasons = set(gate.get("process_failures") or [])
    missing_reasons = sorted(expected_reasons - actual_reasons)
    if missing_reasons:
        failures.append({"kind": "missing_failure_reason", "missing": missing_reasons})

    expected_decision = expected.get("expected_decision")
    if expected_decision == "blank" and result["status"] == "completed":
        failures.append({"kind": "false_export", "reason": expected.get("must_not_export_reason")})

    run_dir = output_dir / "runs" / run_id
    prediction = read_json(run_dir / "output" / "prediction.json")
    patch_text = (run_dir / "output" / "patch.diff").read_text()
    run_record = read_json(run_dir / "run.json")

    if expected.get("expect_prediction_blank") and prediction.get("model_patch"):
        failures.append({"kind": "prediction_not_blank", "path": "output/prediction.json"})
    if expected.get("expect_patch_preserved") and patch_text != patch:
        failures.append({"kind": "patch_not_preserved"})
    expected_candidate_state = expected.get("expected_candidate_state")
    candidate_state = (run_record.get("candidate") or {}).get("state")
    if expected_candidate_state and candidate_state != expected_candidate_state:
        failures.append(
            {
                "kind": "candidate_state_mismatch",
                "expected": expected_candidate_state,
                "actual": candidate_state,
            }
        )
    return failures


def prepare_config_dir(output_dir: Path, task_id: str) -> Path:
    config_dir = output_dir / "_configs" / task_id
    dump_dir = config_dir / "run_dump"
    dump_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "goal.txt").write_text(f"Replay fixture {task_id}\n")
    (config_dir / "workflow.fabro").write_text("digraph Replay {}\n")
    (config_dir / "workflow.toml").write_text("[workflow]\n")
    (dump_dir / "events.jsonl").write_text("")
    return config_dir


def instance_for_task(task_id: str) -> dict[str, Any]:
    return {
        "instance_id": task_id,
        "repo": "light-eval/replay",
        "version": "fixture",
        "base_commit": "fixture",
    }


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def read_optional_text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
