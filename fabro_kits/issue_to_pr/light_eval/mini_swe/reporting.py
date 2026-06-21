from __future__ import annotations
import json
import shutil
from pathlib import Path
from typing import Any
from ...artifacts import build_prediction_record, is_export_eligible
from ..bundles import prepare_config_dir
from ..grader import MiniSweGrade
from ..task_schema import AttemptResult, MiniSweCase, mini_swe_source
from .cases import task_contract_for_case

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
        "task_contract": task_contract_for_case(case),
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
        **truthfulness_counts(results),
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
        "failed_with_patch": sum(1 for result in results if result_has_patch(result) and not (result.get("eval") if isinstance(result.get("eval"), dict) else {}).get("b2_model_eligible", is_export_eligible(result))),
        "ineligible_by_reason": ineligible_counts(evals),
        "failures": failures,
    }


def truthfulness_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "truthful_pass": 0,
        "hand_wavy_pass": 0,
        "review_missed_required_row": 0,
        "weak_closure_accepted": 0,
        "closure_without_machine_evidence": 0,
        "false_export_due_to_review": 0,
        "false_export_due_to_evidence": 0,
        "classified_false_blanks": 0,
        "unclassified_false_blanks": 0,
        "safe_false_alarms": 0,
    }
    for result in results:
        item = result.get("eval")
        if not isinstance(item, dict):
            continue
        gate = result.get("review_accountability_gate")
        gate = gate if isinstance(gate, dict) else {}
        actual_export = item.get("decision_outcome") in {"true_export", "false_export"}
        evidence_weak = evidence_weakness(item, result)
        review_weak = review_weakness(item) or gate_weakness(gate)
        if truthful_pass(item):
            counts["truthful_pass"] += 1
        if actual_export and (evidence_weak or review_weak):
            counts["hand_wavy_pass"] += 1
        if item.get("review_recall") not in {None, "pass", "not_applicable"}:
            counts["review_missed_required_row"] += 1
        if actual_export and gate_weakness(gate):
            counts["weak_closure_accepted"] += 1
        if actual_export and machine_evidence_missing(item, result):
            counts["closure_without_machine_evidence"] += 1
        if item.get("false_export"):
            if evidence_weak:
                counts["false_export_due_to_evidence"] += 1
            elif review_weak:
                counts["false_export_due_to_review"] += 1
        if item.get("false_blank"):
            if classified_false_blank(item, result):
                counts["classified_false_blanks"] += 1
                counts["safe_false_alarms"] += 1
            else:
                counts["unclassified_false_blanks"] += 1
    return counts


def truthful_pass(item: dict[str, Any]) -> bool:
    return (
        item.get("patch_grade") == "pass"
        and item.get("artifact_grade") == "pass"
        and item.get("export_grade") == "pass"
        and item.get("artifact_truthfulness") == "honest"
        and item.get("evidence_sufficiency") == "sufficient"
        and not item.get("false_export")
        and not item.get("false_blank")
        and not item.get("honesty_failures")
        and item.get("review_recall") in {"pass", "not_applicable"}
        and item.get("review_precision") in {"pass", "not_applicable"}
        and item.get("moderation_outcome") in {"correct", "not_applicable"}
        and item.get("decision_outcome") in {"true_export", "true_blank", "fixup"}
    )


def evidence_weakness(item: dict[str, Any], result: dict[str, Any]) -> bool:
    test_gate = result.get("test_evidence_gate")
    return (
        item.get("artifact_truthfulness") != "honest"
        or item.get("evidence_sufficiency") != "sufficient"
        or bool(item.get("honesty_failures"))
        or (isinstance(test_gate, dict) and test_gate.get("status") != "passed")
    )


def review_weakness(item: dict[str, Any]) -> bool:
    return (
        item.get("review_recall") not in {None, "pass", "not_applicable"}
        or item.get("review_precision") not in {None, "pass", "not_applicable"}
        or item.get("moderation_outcome") not in {None, "correct", "not_applicable"}
    )


def gate_weakness(gate: dict[str, Any]) -> bool:
    keys = (
        "process_failures",
        "closure_check_failures",
        "open_rows",
        "unaccounted_rows",
        "unaccounted_major_rows",
    )
    return any(bool(gate.get(key)) for key in keys)


def machine_evidence_missing(item: dict[str, Any], result: dict[str, Any]) -> bool:
    failures = item.get("honesty_failures")
    if isinstance(failures, list) and any(
        isinstance(f, str) and f.startswith("runtime_proof_") for f in failures
    ):
        return True
    test_gate = result.get("test_evidence_gate")
    if isinstance(test_gate, dict) and test_gate.get("status") != "passed":
        return True
    commands = result.get("commands_run")
    if not isinstance(commands, list):
        return True
    return not any(passed_test_command_with_id(command) for command in commands)


def classified_false_blank(item: dict[str, Any], result: dict[str, Any]) -> bool:
    phases = result.get("phases")
    gate = result.get("review_accountability_gate") or (phases.get("review_accountability_gate") if isinstance(phases, dict) else {})
    gate = gate if isinstance(gate, dict) else {}
    candidate = result.get("candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    reasons = {item.get("review_precision"), item.get("moderation_outcome")}
    for key in ("quality_failures", "honesty_failures", "export_failures"):
        reasons.update(reason for reason in item.get(key) or () if isinstance(reason, str))
    reasons.update(reason for reason in gate.get("process_failures") or () if isinstance(reason, str))
    reasons.update((gate.get("failure_reason"), candidate.get("failure_reason")))
    return (
        (result.get("status") == "failed" or gate.get("route_decision") == "fixup")
        and bool({candidate.get("state"), candidate.get("reuse")} & {"failed_with_patch", "continuation_candidate"})
        and result_has_patch(result)
        and bool(reasons & {"contract_overreach", "review_overreach", "process_block", "tests_not_executed_successfully"})
    )


def result_has_patch(result: dict[str, Any]) -> bool:
    return bool(str(result.get("model_patch", "")).strip() or (result.get("candidate") if isinstance(result.get("candidate"), dict) else {}).get("patch_bytes"))


def passed_test_command_with_id(command: Any) -> bool:
    if not isinstance(command, dict) or not str(command.get("id", "")).strip():
        return False
    if command.get("is_test_command") is not True:
        return False
    return command.get("exit_code") == 0 or str(command.get("status", "")).lower() in {
        "ok",
        "pass",
        "passed",
        "success",
        "succeeded",
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
