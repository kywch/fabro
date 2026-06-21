"""Expected-result checks for lightweight eval bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..artifacts import build_prediction_record
from .process import read_json


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
    audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
    for key in ("changed_files", "test_files_changed"):
        expected_key = f"expected_audit_{key}"
        if expected_key in expected and audit.get(key) != expected[expected_key]:
            failures.append(
                {
                    "kind": "audit_mismatch",
                    "field": key,
                    "expected": expected[expected_key],
                    "actual": audit.get(key),
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

    closure_check_failures = [
        item for item in gate.get("closure_check_failures") or [] if isinstance(item, dict)
    ]
    for expected_closure_failure in expected.get("expected_closure_check_failures") or []:
        matching = [
            item
            for item in closure_check_failures
            if all(item.get(key) == value for key, value in expected_closure_failure.items())
        ]
        if not matching:
            failures.append(
                {
                    "kind": "missing_closure_check_failure",
                    "expected": expected_closure_failure,
                    "actual": closure_check_failures,
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

def synthetic_expected_for_task(task_id: str) -> dict[str, Any]:
    if task_id != "claimed-test-mismatch":
        raise SystemExit(f"unknown synthetic task: {task_id}")
    return _claimed_test_mismatch_expected(task_id)

def issue_workflow_smoke_expected() -> dict[str, Any]:
    return _claimed_test_mismatch_expected("issue-workflow-smoke")

def _claimed_test_mismatch_expected(task_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "expected_decision": "blank",
        "expected_result_status": "failed",
        "expected_route_decision": "fixup",
        "expected_process_failures_exact": ["tests_not_executed_successfully"],
        "expected_test_gate_status": "failed",
        "expected_test_gate_hard_failures": [
            "validation_claims_tests_but_diff_has_no_test_files",
        ],
        "expected_audit_changed_files": ["src/greeting.py"],
        "expected_audit_test_files_changed": [],
        "expect_prediction_blank": True,
        "expect_patch_preserved": True,
        "expected_candidate_state": "failed_with_patch",
        "expected_candidate_reuse": "continuation_candidate",
    }
