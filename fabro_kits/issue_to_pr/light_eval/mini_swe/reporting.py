"""Output shaping and summary helpers for mini-SWE runs."""
from __future__ import annotations
import json
import shutil
from pathlib import Path
from typing import Any
from ...artifacts import build_prediction_record
from ..bundles import prepare_config_dir
from ..grader import MiniSweGrade
from ..task_schema import AttemptResult, MiniSweCase, mini_swe_source

def prepare_mini_swe_config_dir(
    output_dir: Path,
    case: MiniSweCase,
    *,
    validation_contract: dict[str, Any],
    attempt_result: AttemptResult,
) -> Path:
    config_dir = prepare_config_dir(output_dir, case.case_id)
    (config_dir / "goal.txt").write_text(case.issue_text + "\n")
    (config_dir / "issue.md").write_text(case.issue_text + "\n")
    (config_dir / "oracle.json").write_text(
        json.dumps(oracle_for_case(case), indent=2, sort_keys=True) + "\n"
    )
    (config_dir / "validation_contract.json").write_text(
        json.dumps(validation_contract, indent=2, sort_keys=True) + "\n"
    )
    if attempt_result.transcript_path and attempt_result.transcript_path.exists():
        dump_dir = config_dir / "run_dump"
        dump_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(attempt_result.transcript_path, dump_dir / "run.transcript")
        if attempt_result.trajectory_path and attempt_result.trajectory_path.exists():
            shutil.copy2(attempt_result.trajectory_path, dump_dir / "trajectory.jsonl")
    return config_dir

def oracle_for_case(case: MiniSweCase) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case_id": case.case_id,
        "family": case.family,
        "suite": case.suite,
        "expected_files": list(case.expected_files),
        "allowed_extra_files": list(case.allowed_extra_files),
        "allowed_test_files": list(case.allowed_test_files),
        "forbidden_files": list(case.forbidden_files),
        "requires_test_change": case.requires_test_change,
        "expected_decision_hint": case.expected_decision_hint,
    }

def mini_swe_instance(case: MiniSweCase) -> dict[str, Any]:
    return {
        "instance_id": case.case_id,
        "repo": "mini-swe/generated",
        "version": case.suite,
        "base_commit": "generated",
        "source": mini_swe_source(case),
        "repository": {
            "provider": "local",
            "owner": "mini-swe",
            "name": "generated",
            "full_name": "mini-swe/generated",
            "base_ref": None,
            "base_sha": "generated",
            "version": case.suite,
        },
    }

def mini_swe_summary(results: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, Any]:
    evals = [result.get("eval", {}) for result in results if isinstance(result.get("eval"), dict)]
    process_blocks = [
        failure
        for failure in failures
        if isinstance(failure, dict) and failure.get("kind") == "process_block"
    ]
    return {
        "total": len(results) + len(process_blocks),
        "completed": len(results),
        "failed": len(failures),
        "calibration_total": sum(
            1 for item in evals if item.get("evaluation_role") == "calibration_provenance"
        ),
        "b2_eligible": sum(1 for item in evals if item.get("b2_eligible")),
        "b2_slice_eligible": sum(1 for item in evals if item.get("b2_slice_eligible")),
        "b2_model_eligible": sum(1 for item in evals if item.get("b2_model_eligible")),
        "patch_pass": sum(1 for item in evals if item.get("patch_grade") == "pass"),
        "artifact_pass": sum(1 for item in evals if item.get("artifact_grade") == "pass"),
        "export_pass": sum(1 for item in evals if item.get("export_grade") == "pass"),
        "false_exports": sum(1 for item in evals if item.get("false_export")),
        "false_blanks": sum(1 for item in evals if item.get("false_blank")),
        "expected_traps_caught": expected_traps_caught(evals),
        "artifact_honesty_failures": artifact_honesty_failure_count(evals),
        "artifact_honesty_failures_by_reason": failure_reason_counts(
            evals,
            "honesty_failures",
        ),
        "expected_b2_ineligible": sum(
            1
            for item in evals
            if item.get("evaluation_role") == "calibration_provenance"
            and not item.get("b2_eligible")
        ),
        "process_blocked": len(process_blocks),
        "provider_not_configured": sum(
            1 for item in process_blocks if item.get("reason") == "provider_not_configured"
        ),
        "process_blocks_by_reason": process_block_counts(process_blocks),
        "cases_by_attempt_origin": counts(evals, "attempt_origin"),
        "cases_by_artifact_origin": counts(evals, "artifact_origin"),
        "cases_by_substrate": counts(evals, "substrate"),
        "ineligible_by_reason": ineligible_counts(evals),
        "failures": failures,
    }


def counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts_by_value: dict[str, int] = {}
    for item in items:
        value = item.get(key)
        if isinstance(value, str):
            counts_by_value[value] = counts_by_value.get(value, 0) + 1
    return counts_by_value


def ineligible_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts_by_reason: dict[str, int] = {}
    for item in items:
        failures = item.get("eligibility_failures")
        if not isinstance(failures, list):
            continue
        for failure in failures:
            if isinstance(failure, str):
                counts_by_reason[failure] = counts_by_reason.get(failure, 0) + 1
    return counts_by_reason


def expected_traps_caught(items: list[dict[str, Any]]) -> int:
    return sum(
        1
        for item in items
        if item.get("export_grade") == "pass"
        and item.get("decision_outcome") in {"fixup", "true_blank"}
    )


def artifact_honesty_failure_count(items: list[dict[str, Any]]) -> int:
    return sum(
        1
        for item in items
        if isinstance(item.get("honesty_failures"), list)
        and bool(item["honesty_failures"])
    )


def failure_reason_counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts_by_reason: dict[str, int] = {}
    for item in items:
        failures = item.get(key)
        if not isinstance(failures, list):
            continue
        for failure in failures:
            if isinstance(failure, str):
                counts_by_reason[failure] = counts_by_reason.get(failure, 0) + 1
    return counts_by_reason


def process_block_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts_by_reason: dict[str, int] = {}
    for item in items:
        reason = item.get("reason")
        if isinstance(reason, str):
            counts_by_reason[reason] = counts_by_reason.get(reason, 0) + 1
    return counts_by_reason


def check_mini_swe_expected(
    case: MiniSweCase,
    result: dict[str, Any],
    *,
    grade: MiniSweGrade,
    expected_decision_hint: str | None = None,
) -> list[dict[str, Any]]:
    failures = []
    prediction = build_prediction_record(result)
    expected = expected_decision_hint or case.expected_decision_hint
    if expected == "export" and not prediction["model_patch"]:
        failures.append({"kind": "unexpected_blank", "task_id": result["instance_id"]})
    if expected != "export" and prediction["model_patch"]:
        failures.append({"kind": "false_export", "task_id": result["instance_id"]})
    if not grade.patch_pass:
        failures.append({"kind": "patch_grade_failed", "task_id": result["instance_id"]})
    if expected == "export" and not grade.artifact_pass:
        failures.append({"kind": "artifact_grade_failed", "task_id": result["instance_id"]})
    if expected != "export" and grade.artifact_pass:
        failures.append({"kind": "expected_artifact_failure_missing", "task_id": result["instance_id"]})
    return failures
