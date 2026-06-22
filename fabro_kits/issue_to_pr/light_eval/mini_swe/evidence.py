from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from ..task_schema import AttemptResult, MiniSweCase
from .cases import case_behavior, task_contract_for_case, tests_added_for_case

def adversarial_review_for_case(case: MiniSweCase) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    checked_risks: list[dict[str, Any]] = []
    if case.case_id == "overblocking-good-patch-with-minor-risk":
        rows.append(
            {
                "id": "minor-001",
                "severity": "minor",
                "category": "maintainability",
                "summary": "The test name is specific to comma behavior.",
                "required_files": ["tests/test_greeting.py"],
                "closure_requires": "runtime_tests",
            }
        )
    elif case_behavior(case).change_source:
        checked_risks.append(
            {
                "risk": "source behavior change may miss the requested greeting contract",
                "evidence": ["src/greeting.py"],
                "counterexample_check": (
                    "src/greeting.py changes the greeting return value and "
                    "public tests exercise the requested behavior"
                ),
            }
        )
    return {
        "schema_version": 1,
        "stage": "adversarial_review",
        "status": "passed",
        "checked_risks": checked_risks,
        "rows": rows,
    }

def moderator_filter_for_case(case: MiniSweCase) -> dict[str, Any]:
    dispositions: list[dict[str, Any]] = []
    if case.case_id == "overblocking-good-patch-with-minor-risk":
        dispositions.append(
            {
                "id": "minor-001",
                "state": "downgraded",
                "category": "maintainability",
                "severity": "minor",
                "closure_check": (
                    "The row is nonblocking: tests/test_greeting.py was updated "
                    "and the machine-observed test command passed."
                ),
                "evidence": [
                    "tests/test_greeting.py changed",
                    "python3 -m unittest discover -s tests passed via cmd-001",
                ],
                "artifact_path": "output/commands_run.json",
                "artifact_field": "commands_run[0].id",
            }
        )
    return {
        "schema_version": 1,
        "stage": "moderator_filter",
        "status": "passed",
        "dispositions": dispositions,
    }

def review_materialization_for_case(case: MiniSweCase) -> dict[str, Any]:
    rendered_rows: list[dict[str, Any]] = []
    if case.case_id == "overblocking-good-patch-with-minor-risk":
        rendered_rows.append(
            {
                "id": "minor-001",
                "state": "downgraded",
                "export_blocking": False,
            }
        )
    return {
        "schema_version": 1,
        "stage": "review_materialization",
        "status": "passed",
        "errors": [],
        "rendered_rows": rendered_rows,
    }

def commands_run_for_case(
    case: MiniSweCase,
    *,
    attempt_result: AttemptResult,
) -> list[dict[str, Any]]:
    command = {
        "argv_or_shell": "python3 -m unittest discover -s tests",
        "command": "python3 -m unittest discover -s tests",
        "cwd": ".",
        "exit_code": 0,
        "status": "passed",
        "is_test_command": True,
        "allowlist_class": "test",
        "stdout_tail": "",
        "stderr_tail": "",
        "repo_state": None,
    }
    command_id = case_behavior(case).public_test_command_id
    if command_id is not None:
        command["id"] = command_id
    return [command]

def commands_run_from_artifacts(
    case: MiniSweCase,
    *,
    attempt_result: AttemptResult,
    validation_contract: dict[str, Any],
) -> list[Any]:
    if attempt_result.attempt_origin == "scripted":
        return commands_run_for_case(case, attempt_result=attempt_result)
    if attempt_result.commands_run_path and attempt_result.commands_run_path.exists():
        try:
            payload = json.loads(attempt_result.commands_run_path.read_text())
        except json.JSONDecodeError:
            return []
        if isinstance(payload, list):
            return normalize_command_ids(payload)
    commands = validation_contract.get("commands_run")
    if isinstance(commands, list):
        return normalize_command_ids(commands)
    return []

def normalize_command_ids(commands: list[Any]) -> list[Any]:
    for command in commands:
        if isinstance(command, dict) and not str(command.get("id", "")).strip():
            stable_id = str(command.get("stable_id", "")).strip()
            if stable_id:
                command["id"] = stable_id
    return commands

def commands_run_from_validation_contract_path(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    artifact_path = Path(path)
    if not artifact_path.exists():
        return []
    try:
        payload = json.loads(artifact_path.read_text())
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    commands = payload.get("commands_run")
    if not isinstance(commands, list):
        return []
    return normalize_command_ids([item for item in commands if isinstance(item, dict)])

def has_commands_run_list(value: Any) -> bool:
    return isinstance(value, list) and any(isinstance(item, dict) for item in value)

def commands_run_artifact_exists(attempt_result: AttemptResult) -> bool:
    return bool(
        attempt_result.commands_run_path and attempt_result.commands_run_path.exists()
    )

def eval_metadata_with_runtime_proof(
    attempt_result: AttemptResult,
    *,
    commands_run: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = attempt_result.eval_metadata()
    proof = dict(metadata.get("eligibility_proof") or {})
    proof["runtime_commands_present"] = bool(commands_run)
    proof["repo_facts_recomputed"] = True
    proof["artifact_claims_compared"] = True
    metadata["eligibility_proof"] = proof
    if attempt_result.attempt_origin != "scripted" and not commands_run:
        failures = list(metadata.get("eligibility_failures") or [])
        if attempt_result.attempt_origin == "model":
            failure = "model_workflow_missing_commands_run"
        else:
            failure = f"{attempt_result.attempt_origin.replace('-', '_')}_missing_commands_run"
        if failure not in failures:
            failures.append(failure)
        metadata["eligibility_failures"] = failures
        metadata["b2_slice_eligible"] = False
        metadata["b2_model_eligible"] = False
        metadata["b2_eligible"] = False
    return metadata

def validation_contract_for_case(
    case: MiniSweCase,
    *,
    attempt: str,
    commands_run: list[dict[str, Any]],
) -> dict[str, Any]:
    behavior = case_behavior(case)
    contract: dict[str, Any] = {
        "schema_version": 1,
        "mode": f"mini-swe-{attempt}",
        "commands_run": commands_run,
        "tests_added": tests_added_for_case(case),
        **task_contract_for_case(case),
    }
    if behavior.no_test_justification:
        contract["no_test_justification"] = behavior.no_test_justification
    return contract

def has_observed_test_command_id(commands_run: list[dict[str, Any]]) -> bool:
    return any(
        command.get("id")
        and command.get("is_test_command")
        and command.get("exit_code") == 0
        for command in commands_run
    )

def effective_expected_decision_hint(
    case: MiniSweCase,
    *,
    attempt_result: AttemptResult,
    commands_run: list[dict[str, Any]],
    test_gate: dict[str, Any],
) -> str:
    if (
        case.case_id == "runtime-proof-honesty"
        and attempt_result.attempt_origin == "model"
        and has_observed_test_command_id(commands_run)
        and test_gate_has_verified_test_command(test_gate)
    ):
        return "export"
    return case.expected_decision_hint

def test_gate_has_verified_test_command(test_gate: dict[str, Any]) -> bool:
    observed = test_gate.get("observed") if isinstance(test_gate, dict) else None
    if not isinstance(observed, dict):
        return False
    verified = observed.get("verified_commands")
    if not isinstance(verified, list) or not verified:
        return False
    tests_passed = observed.get("tests_passed_count")
    return type(tests_passed) is int and tests_passed > 0
