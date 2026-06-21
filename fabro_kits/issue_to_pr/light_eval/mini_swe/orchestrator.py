from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from ...artifacts import (
    load_or_init_manifest, run_id_for_task, update_manifest_for_run,
    write_eval_root_outputs, write_run_bundle,
)
from ...evidence_gate import evaluate_evidence_gate
from ...review_accountability_gate import evaluate_review_accountability
from ..grader import grade_mini_swe_attempt
from .artifacts import (
    read_artifact_or_default,
)
from .cases import list_mini_swe_cases, validate_mini_swe_options
from .evidence import (
    adversarial_review_for_case,
    commands_run_artifact_exists,
    commands_run_from_artifacts,
    effective_expected_decision_hint,
    eval_metadata_with_runtime_proof,
    has_commands_run_list,
    has_observed_test_command_id,
    moderator_filter_for_case,
    review_materialization_for_case,
    validation_contract_for_case,
)
from ..paths import DEFAULT_SYNTHETIC_DOCKER_IMAGE
from ..process import git_capture, is_test_path
from ..task_schema import AttemptResult, MiniSweCase, mini_swe_source
from .repo import create_repo_for_case, run_hidden_oracle
from .reporting import (
    check_mini_swe_expected,
    mini_swe_instance,
    mini_swe_summary,
    prepare_mini_swe_config_dir,
)
from .runners import (
    MiniSweProcessBlock,
    ModelWorkflowRunner,
    ScriptedCalibrationRunner,
    WorkflowSliceRunner,
)


def run_mini_swe(
    case: str = "all",
    *,
    suite: str = "all",
    output_dir: Path | None = None,
    attempt: str = "scripted",
    substrate: str = "local",
    fabro_bin: Path = Path("target/debug/fabro"),
    docker_image: str = DEFAULT_SYNTHETIC_DOCKER_IMAGE,
    model: str | None = None,
    provider: str | None = None,
    credential_bridge: str = "off",
    auth_storage_dir: Path | None = None,
    credential_preflight: bool = False,
    seed: int | None = None,
    fail_fast: bool = False,
) -> dict[str, Any]:
    cases = list_mini_swe_cases(case, suite=suite)
    validate_mini_swe_options(
        attempt=attempt,
        substrate=substrate,
        credential_bridge=credential_bridge,
        credential_preflight=credential_preflight,
        auth_storage_dir=auth_storage_dir,
    )
    if output_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return _run_mini_swe_to_dir(
                cases,
                Path(tmp),
                attempt=attempt,
                substrate=substrate,
                fabro_bin=fabro_bin,
                docker_image=docker_image,
                model=model,
                provider=provider,
                credential_bridge=credential_bridge,
                auth_storage_dir=auth_storage_dir,
                credential_preflight=credential_preflight,
                seed=seed,
                fail_fast=fail_fast,
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    return _run_mini_swe_to_dir(
        cases,
        output_dir,
        attempt=attempt,
        substrate=substrate,
        fabro_bin=fabro_bin,
        docker_image=docker_image,
        model=model,
        provider=provider,
        credential_bridge=credential_bridge,
        auth_storage_dir=auth_storage_dir,
        credential_preflight=credential_preflight,
        seed=seed,
        fail_fast=fail_fast,
    )

def _run_mini_swe_to_dir(
    cases: list[MiniSweCase],
    output_dir: Path,
    *,
    attempt: str,
    substrate: str,
    fabro_bin: Path,
    docker_image: str,
    model: str | None,
    provider: str | None,
    credential_bridge: str,
    auth_storage_dir: Path | None,
    credential_preflight: bool,
    seed: int | None,
    fail_fast: bool,
) -> dict[str, Any]:
    started_at = time.monotonic()
    results = []
    failures = []
    manifest = load_or_init_manifest(output_dir)

    for case in cases:
        try:
            case_result = run_mini_swe_case(
                case,
                output_dir=output_dir,
                attempt=attempt,
                substrate=substrate,
                fabro_bin=fabro_bin,
                docker_image=docker_image,
                model=model,
                provider=provider,
                credential_bridge=credential_bridge,
                auth_storage_dir=auth_storage_dir,
                credential_preflight=credential_preflight,
            )
        except MiniSweProcessBlock as exc:
            failures.append(exc.to_failure(task_id=case.case_id, attempt=attempt))
            if fail_fast:
                break
            continue
        results.append(case_result["result"])
        failures.extend(case_result["failures"])
        update_manifest_for_run(
            manifest,
            task_id=case_result["task_id"],
            run_id=case_result["run_id"],
            attempt_id="001",
            output_dir=output_dir,
        )
        if fail_fast and case_result["failures"]:
            break

    summary = mini_swe_summary(results, failures)
    summary["total_duration_s"] = round(time.monotonic() - started_at, 1)
    if seed is not None:
        summary["seed"] = seed
    write_eval_root_outputs(output_dir, results=results, summary=summary, manifest=manifest)
    return summary


def run_mini_swe_case(
    case: MiniSweCase,
    *,
    output_dir: Path,
    attempt: str,
    substrate: str,
    fabro_bin: Path,
    docker_image: str = DEFAULT_SYNTHETIC_DOCKER_IMAGE,
    model: str | None = None,
    provider: str | None = None,
    credential_bridge: str = "off",
    auth_storage_dir: Path | None = None,
    credential_preflight: bool = False,
) -> dict[str, Any]:
    started_at = time.monotonic()
    validate_mini_swe_options(
        attempt=attempt,
        substrate=substrate,
        credential_bridge=credential_bridge,
        credential_preflight=credential_preflight,
        auth_storage_dir=auth_storage_dir,
    )

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        repo_dir = work_dir / "repo"
        repo_dir.mkdir()
        create_repo_for_case(case, repo_dir)
        if attempt == "scripted":
            runner = ScriptedCalibrationRunner(substrate=substrate, docker_image=docker_image)
        elif attempt == "workflow-slice":
            runner = WorkflowSliceRunner(output_dir=output_dir, fabro_bin=fabro_bin)
        else:
            runner = ModelWorkflowRunner(
                output_dir=output_dir,
                fabro_bin=fabro_bin,
                model=model,
                provider=provider,
                credential_bridge=credential_bridge,
                auth_storage_dir=auth_storage_dir,
                credential_preflight=credential_preflight,
            )
        attempt_result = runner.run(case, repo_dir, work_dir)

        patch = attempt_result.patch_path.read_text() if attempt_result.patch_path else ""
        changed_files = git_capture(repo_dir, "diff", "--name-only").splitlines()
        test_files_changed = [path for path in changed_files if is_test_path(path)]
        hidden_oracle = run_hidden_oracle(case, repo_dir)

    artifact_paths = attempt_result.artifact_paths
    audit = read_artifact_or_default(
        artifact_paths.get("audit"),
        {
            "schema_version": 1,
            "mode": f"mini-swe-{attempt}",
            "patch_nonempty": bool(patch.strip()),
            "changed_files": changed_files,
            "test_files_changed": test_files_changed,
            "sandbox_provider": substrate,
        },
    )
    audit_repair = {"patch_nonempty": bool(patch.strip()), "changed_files": changed_files, "test_files_changed": test_files_changed}
    if any(audit.get(key) != value for key, value in audit_repair.items()):
        audit["_repo_facts_repaired"] = True
    audit.update(audit_repair)
    fallback_contract = validation_contract_for_case(case, attempt=attempt, commands_run=[])
    validation_contract = {
        **fallback_contract,
        **read_artifact_or_default(
            artifact_paths.get("validation_contract"),
            fallback_contract,
        ),
    }
    commands_run = commands_run_from_artifacts(
        case,
        attempt_result=attempt_result,
        validation_contract=validation_contract,
    )
    if (
        attempt_result.attempt_origin == "scripted"
        or commands_run_artifact_exists(attempt_result)
        or not has_commands_run_list(validation_contract.get("commands_run"))
    ):
        validation_contract["commands_run"] = commands_run
    eval_metadata = eval_metadata_with_runtime_proof(
        attempt_result,
        commands_run=commands_run,
    )
    test_gate = read_artifact_or_default(
        artifact_paths.get("test_evidence_gate"),
        evaluate_evidence_gate(audit=audit, contract=validation_contract),
    )
    if not artifact_paths.get("test_evidence_gate") and has_observed_test_command_id(commands_run):
        test_gate.setdefault("observed", {})["tests_passed_count"] = 1
    adversarial = read_artifact_or_default(
        artifact_paths.get("adversarial_review"),
        adversarial_review_for_case(case),
    )
    moderator = read_artifact_or_default(
        artifact_paths.get("moderator_filter"),
        moderator_filter_for_case(case),
    )
    materialization = read_artifact_or_default(
        artifact_paths.get("review_materialization"),
        review_materialization_for_case(case),
    )
    gate = evaluate_review_accountability(
        adversarial=adversarial,
        moderator=moderator,
        test_gate=test_gate,
        materialization=materialization,
        patch_diff=patch,
    )
    expected_decision_hint = effective_expected_decision_hint(
        case,
        attempt_result=attempt_result,
        commands_run=commands_run,
        test_gate=test_gate,
    )
    grade = grade_mini_swe_attempt(
        case=case,
        patch=patch,
        changed_files=changed_files,
        test_files_changed=test_files_changed,
        audit=audit,
        validation_contract=validation_contract,
        hidden_oracle_passed=hidden_oracle["passed"],
        test_gate=test_gate,
        accountability_gate=gate,
        expected_decision_hint=expected_decision_hint,
    )
    eval_metadata = {**eval_metadata, **grade.to_metadata()}
    eval_metadata["effective_expected_decision_hint"] = expected_decision_hint
    fabro_run_id = attempt_result.provenance.get("fabro_run_id")
    if not isinstance(fabro_run_id, str):
        fabro_run_id = None
    result = {
        "instance_id": case.case_id,
        "model_name_or_path": f"light-eval-mini-swe-{attempt}",
        "model_patch": patch,
        "status": "completed" if gate.get("route_decision") == "export" else "failed",
        "error": gate.get("failure_reason") if gate.get("route_decision") != "export" else None,
        "duration_s": round(time.monotonic() - started_at, 1),
        "fabro_run_id": fabro_run_id,
        "fabro_dump_dir": attempt_result.dump_path.as_posix()
        if attempt_result.dump_path
        else None,
        "trajectory_path": attempt_result.trajectory_path.as_posix()
        if attempt_result.trajectory_path
        else None,
        "source": mini_swe_source(case),
        "eval": eval_metadata,
        "audit": audit,
        "verify": {
            "schema_version": 1,
            "status": "completed",
            "mode": f"mini-swe-{attempt}",
            "patch_nonempty": bool(patch.strip()),
            "sandbox_provider": substrate,
            "hidden_oracle": hidden_oracle,
        },
        "commands_run": commands_run,
        "test_evidence_gate": test_gate,
        "adversarial_review": adversarial,
        "moderator_filter": moderator,
        "review_materialization": materialization,
        "review_accountability_gate": gate,
    }

    config_dir = prepare_mini_swe_config_dir(
        output_dir,
        case,
        validation_contract=validation_contract,
        attempt_result=attempt_result,
    )
    instance = mini_swe_instance(case)
    write_run_bundle(
        instance=instance,
        result=result,
        output_dir=output_dir,
        config_dir=config_dir,
        sandbox_provider=substrate,
    )

    run_id = run_id_for_task(case.case_id)
    failures = check_mini_swe_expected(
        case,
        result,
        grade=grade,
        expected_decision_hint=expected_decision_hint,
    )
    return {
        "task_id": case.case_id,
        "run_id": run_id,
        "result": result,
        "failures": failures,
    }
