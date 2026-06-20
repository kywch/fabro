"""Synthetic repo eval orchestration."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..artifacts import (
    build_eval_summary, load_or_init_manifest, run_id_for_task, update_manifest_for_run,
    write_eval_root_outputs, write_run_bundle,
)
from ..evidence_gate import evaluate_evidence_gate
from ..review_accountability_gate import evaluate_review_accountability
from .bundles import (
    prepare_synthetic_config_dir,
    synthetic_instance_for_task,
    synthetic_source_for_task,
)
from .expectations import check_expected, synthetic_expected_for_task
from .local_repo import run_claimed_test_mismatch_local_repo
from .paths import DEFAULT_SYNTHETIC_DOCKER_IMAGE
from .process import as_str_list, is_test_path
from .repo_cases import create_claimed_test_mismatch_repo
from .sandboxed_repo import run_claimed_test_mismatch_docker_repo


def run_synthetic(
    task: str = "all",
    *,
    output_dir: Path | None = None,
    sandbox: str = "local",
    docker_image: str = DEFAULT_SYNTHETIC_DOCKER_IMAGE,
) -> dict[str, Any]:
    if sandbox not in {"local", "docker"}:
        raise SystemExit(f"unknown synthetic sandbox: {sandbox}")
    task_ids = list_synthetic_tasks(task)
    if output_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return _run_synthetic_to_dir(
                task_ids,
                Path(tmp),
                sandbox=sandbox,
                docker_image=docker_image,
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    return _run_synthetic_to_dir(task_ids, output_dir, sandbox=sandbox, docker_image=docker_image)

def list_synthetic_tasks(task: str) -> list[str]:
    known = ["claimed-test-mismatch"]
    if task == "all":
        return known
    if task not in known:
        raise SystemExit(f"unknown synthetic task: {task}")
    return [task]

def _run_synthetic_to_dir(
    task_ids: list[str],
    output_dir: Path,
    *,
    sandbox: str,
    docker_image: str,
) -> dict[str, Any]:
    results = []
    failures = []
    manifest = load_or_init_manifest(output_dir)

    for task_id in task_ids:
        case_result = run_synthetic_task(
            task_id,
            output_dir=output_dir,
            sandbox=sandbox,
            docker_image=docker_image,
        )
        results.append(case_result["result"])
        update_manifest_for_run(
            manifest,
            task_id=case_result["task_id"],
            run_id=case_result["run_id"],
            attempt_id="001",
            output_dir=output_dir,
        )
        failures.extend(case_result["failures"])

    summary = build_eval_summary(len(results), failures)
    write_eval_root_outputs(output_dir, results=results, summary=summary, manifest=manifest)
    return summary

def run_synthetic_task(
    task_id: str,
    *,
    output_dir: Path,
    sandbox: str = "local",
    docker_image: str = DEFAULT_SYNTHETIC_DOCKER_IMAGE,
) -> dict[str, Any]:
    if task_id != "claimed-test-mismatch":
        raise SystemExit(f"unknown synthetic task: {task_id}")

    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = Path(tmp) / "repo"
        repo_dir.mkdir()
        create_claimed_test_mismatch_repo(repo_dir)
        if sandbox == "local":
            sandbox_result = run_claimed_test_mismatch_local_repo(repo_dir)
        elif sandbox == "docker":
            sandbox_result = run_claimed_test_mismatch_docker_repo(
                repo_dir,
                docker_image=docker_image,
            )
        else:
            raise SystemExit(f"unknown synthetic sandbox: {sandbox}")

    patch = str(sandbox_result["patch"])
    changed_files = as_str_list(sandbox_result.get("changed_files"))
    sandbox_provider = str(sandbox_result["sandbox_provider"])
    mode = str(sandbox_result["mode"])
    source_kind = str(sandbox_result["source_kind"])

    audit = {
        "schema_version": 1,
        "mode": mode,
        "patch_nonempty": bool(patch.strip()),
        "changed_files": changed_files,
        "test_files_changed": [path for path in changed_files if is_test_path(path)],
        "sandbox_provider": sandbox_provider,
    }
    validation_contract = {
        "schema_version": 1,
        "mode": mode,
        "tests_added": [{"path": "tests/test_greeting.py", "description": "claimed coverage"}],
        "commands_run": [
            {
                "command": "python3 -m unittest tests/test_greeting.py",
                "status": "passed",
            }
        ],
    }
    test_gate = evaluate_evidence_gate(audit=audit, contract=validation_contract)
    adversarial = {
        "schema_version": 1,
        "stage": "adversarial_review",
        "status": "passed",
        "rows": [],
    }
    moderator = {
        "schema_version": 1,
        "stage": "moderator_filter",
        "status": "passed",
        "dispositions": [],
    }
    materialization = {
        "schema_version": 1,
        "stage": "review_materialization",
        "status": "passed",
        "errors": [],
    }
    gate = evaluate_review_accountability(
        adversarial=adversarial,
        moderator=moderator,
        test_gate=test_gate,
        materialization=materialization,
        patch_diff=patch,
    )
    result_status = "completed" if gate["route_decision"] == "export" else "failed"
    result = {
        "instance_id": task_id,
        "model_name_or_path": "light-eval-synthetic",
        "model_patch": patch,
        "status": result_status,
        "error": gate.get("failure_reason") if result_status != "completed" else None,
        "duration_s": 0,
        "fabro_run_id": None,
        "fabro_dump_dir": None,
        "trajectory_path": None,
        "source": synthetic_source_for_task(task_id, source_kind=source_kind),
        "audit": audit,
        "verify": {
            "schema_version": 1,
            "status": "completed",
            "mode": mode,
            "patch_nonempty": bool(patch.strip()),
            "sandbox_provider": sandbox_provider,
        },
        "test_evidence_gate": test_gate,
        "adversarial_review": adversarial,
        "moderator_filter": moderator,
        "review_materialization": materialization,
        "review_accountability_gate": gate,
    }

    config_dir = prepare_synthetic_config_dir(
        output_dir,
        task_id,
        validation_contract=validation_contract,
    )
    write_run_bundle(
        instance=synthetic_instance_for_task(task_id, source_kind=source_kind),
        result=result,
        output_dir=output_dir,
        config_dir=config_dir,
        sandbox_provider=sandbox_provider,
    )
    run_id = run_id_for_task(task_id)
    failures = check_expected(
        synthetic_expected_for_task(task_id),
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
