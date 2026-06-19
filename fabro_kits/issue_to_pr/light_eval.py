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
        return sorted(
            path
            for path in FIXTURE_ROOT.iterdir()
            if path.is_dir() and (path / "expected.json").exists()
        )
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
    failures.extend(check_root_expected(fixture_dirs, output_dir=output_dir))
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
        patch_diff=patch,
        settings_ref_diff=settings_ref_diff,
    )
    status = expected.get("result_status") or (
        "completed" if gate["route_decision"] == "export" else "failed"
    )
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
    expected_exact_reasons = expected.get("expected_process_failures_exact")
    if expected_exact_reasons is not None and list(gate.get("process_failures") or []) != list(
        expected_exact_reasons
    ):
        failures.append(
            {
                "kind": "process_failures_mismatch",
                "expected": expected_exact_reasons,
                "actual": gate.get("process_failures"),
            }
        )

    expected_decision = expected.get("expected_decision")
    root_prediction = build_prediction_record(result)
    if expected_decision == "blank" and root_prediction.get("model_patch"):
        failures.append({"kind": "false_export", "reason": expected.get("must_not_export_reason")})
    elif expected_decision == "export" and not root_prediction.get("model_patch"):
        failures.append({"kind": "unexpected_blank_prediction"})
    expected_result_status = expected.get("expected_result_status")
    if expected_result_status and result.get("status") != expected_result_status:
        failures.append(
            {
                "kind": "result_status_mismatch",
                "expected": expected_result_status,
                "actual": result.get("status"),
            }
        )
    expected_route_decision = expected.get("expected_route_decision")
    if expected_route_decision and gate.get("route_decision") != expected_route_decision:
        failures.append(
            {
                "kind": "route_decision_mismatch",
                "expected": expected_route_decision,
                "actual": gate.get("route_decision"),
            }
        )
    test_gate = result.get("test_evidence_gate") or {}
    judgment = test_gate.get("judgment") if isinstance(test_gate, dict) else {}
    judgment = judgment if isinstance(judgment, dict) else {}
    expected_test_gate_status = expected.get("expected_test_gate_status")
    if expected_test_gate_status and test_gate.get("status") != expected_test_gate_status:
        failures.append(
            {
                "kind": "test_gate_status_mismatch",
                "expected": expected_test_gate_status,
                "actual": test_gate.get("status"),
            }
        )
    observed = test_gate.get("observed") if isinstance(test_gate.get("observed"), dict) else {}
    for key in ("tests_passed_count", "commands_reported_passed_count"):
        expected_key = f"expected_test_gate_{key}"
        if expected_key in expected and observed.get(key) != expected[expected_key]:
            failures.append(
                {
                    "kind": "test_gate_observed_mismatch",
                    "field": key,
                    "expected": expected[expected_key],
                    "actual": observed.get(key),
                }
            )
    for expected_failure in expected.get("expected_test_gate_hard_failures") or []:
        hard_failures = [str(item) for item in judgment.get("hard_failures") or []]
        if not any(expected_failure in failure for failure in hard_failures):
            failures.append(
                {
                    "kind": "missing_test_gate_hard_failure",
                    "expected": expected_failure,
                    "actual": hard_failures,
                }
            )
    for expected_warning in expected.get("expected_test_gate_warnings") or []:
        warnings = [str(item) for item in judgment.get("warnings") or []]
        if not any(expected_warning in warning for warning in warnings):
            failures.append(
                {
                    "kind": "missing_test_gate_warning",
                    "expected": expected_warning,
                    "actual": warnings,
                }
            )
    malformed_artifacts = [
        item for item in gate.get("malformed_artifacts") or [] if isinstance(item, dict)
    ]
    for expected_malformed in expected.get("expected_malformed_artifacts") or []:
        matching = [
            item
            for item in malformed_artifacts
            if item.get("artifact") == expected_malformed.get("artifact")
            and item.get("error") == expected_malformed.get("error")
        ]
        if not matching:
            failures.append(
                {
                    "kind": "missing_malformed_artifact",
                    "expected": expected_malformed,
                    "actual": malformed_artifacts,
                }
            )
            continue
        for expected_reason in expected_malformed.get("reason_contains") or []:
            if not any(expected_reason in str(item.get("reason", "")) for item in matching):
                failures.append(
                    {
                        "kind": "missing_malformed_artifact_reason",
                        "expected": expected_reason,
                        "actual": matching,
                    }
                )
        for expected_failure in expected_malformed.get("hard_failures_contains") or []:
            if not any(
                expected_failure in str(failure)
                for item in matching
                for failure in item.get("hard_failures") or []
            ):
                failures.append(
                    {
                        "kind": "missing_malformed_artifact_hard_failure",
                        "expected": expected_failure,
                        "actual": matching,
                    }
                )

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
    expected_candidate_reuse = expected.get("expected_candidate_reuse")
    candidate_reuse = (run_record.get("candidate") or {}).get("reuse")
    if expected_candidate_reuse and candidate_reuse != expected_candidate_reuse:
        failures.append(
            {
                "kind": "candidate_reuse_mismatch",
                "expected": expected_candidate_reuse,
                "actual": candidate_reuse,
            }
        )
    expected_run_exports = expected.get("expected_run_exports") or {}
    run_exports = run_record.get("exports") or {}
    for key, expected_value in expected_run_exports.items():
        actual_value = run_exports.get(key)
        if actual_value != expected_value:
            failures.append(
                {
                    "kind": "run_export_mismatch",
                    "key": key,
                    "expected": expected_value,
                    "actual": actual_value,
                }
            )
    return failures


def check_root_expected(
    fixture_dirs: list[Path],
    *,
    output_dir: Path,
) -> list[dict[str, Any]]:
    failures = []
    predictions = [
        json.loads(line)
        for line in (output_dir / "predictions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    prediction_by_task = {
        str(prediction.get("instance_id")): prediction for prediction in predictions
    }
    manifest = read_json(output_dir / "manifest.json")
    expected_manifest_exports = {
        "swebench_predictions": "predictions.jsonl",
        "swebench_results": "results.jsonl",
        "swebench_summary": "summary.json",
    }
    if (manifest.get("exports") or {}) != expected_manifest_exports:
        failures.append(
            {
                "kind": "manifest_exports_mismatch",
                "expected": expected_manifest_exports,
                "actual": manifest.get("exports"),
            }
        )

    for fixture_dir in fixture_dirs:
        expected = read_json(fixture_dir / "expected.json")
        task_id = expected.get("task_id") or fixture_dir.name
        prediction = prediction_by_task.get(task_id)
        if prediction is None:
            failures.append({"kind": "root_prediction_missing", "task_id": task_id})
            continue
        if expected.get("expect_root_prediction_blank") and prediction.get("model_patch"):
            failures.append(
                {
                    "kind": "root_prediction_not_blank",
                    "task_id": task_id,
                    "path": "predictions.jsonl",
                }
            )
        if expected.get("expect_root_prediction_nonblank") and not prediction.get("model_patch"):
            failures.append(
                {
                    "kind": "root_prediction_blank",
                    "task_id": task_id,
                    "path": "predictions.jsonl",
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
