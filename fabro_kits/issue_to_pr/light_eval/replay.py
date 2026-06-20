"""Saved artifact replay eval."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..artifacts import (
    build_eval_summary, load_or_init_manifest, run_id_for_task, update_manifest_for_run,
    write_eval_root_outputs, write_run_bundle,
)
from ..review_accountability_gate import evaluate_review_accountability, load_json_object
from .bundles import instance_for_task, prepare_config_dir
from .expectations import check_expected, check_root_expected
from .paths import list_fixtures
from .process import read_json, read_optional_text


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

    write_eval_root_outputs(output_dir, results=results, manifest=manifest)
    failures.extend(check_root_expected(fixture_dirs, output_dir=output_dir))
    summary = build_eval_summary(len(results), failures)
    write_eval_root_outputs(output_dir, summary=summary)
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
