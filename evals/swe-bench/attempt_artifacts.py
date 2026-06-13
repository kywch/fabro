"""Artifact sidecars for Fabro SWE-bench solve attempts.

The root SWE-bench JSONL files remain compatibility exports. These helpers
write the more general task/attempt records that issue-to-PR tooling can grow
around.
"""

from __future__ import annotations

import json
import shutil
import hashlib
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
LAYOUT = "swebench-v1-compat"
RUNS_LAYOUT = "issue-to-pr-runs-v1"
DEFAULT_ATTEMPT_ID = "001"


def build_prediction_record(result: dict[str, Any]) -> dict[str, Any]:
    """Return the single-instance equivalent of predictions.jsonl."""
    model_patch = result.get("model_patch", "")
    if result.get("status") != "completed":
        model_patch = ""
    return {
        "instance_id": result["instance_id"],
        "model_name_or_path": result["model_name_or_path"],
        "model_patch": model_patch,
    }


def build_task_record(
    instance: dict[str, Any],
    goal_path: Path,
    sandbox_provider: str,
) -> dict[str, Any]:
    """Build a domain-neutral task record from one SWE-bench instance."""
    owner, name = _split_repo(instance["repo"])
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": instance["instance_id"],
        "source": {
            "kind": "swe_bench",
            "external_id": instance["instance_id"],
            "dataset": "princeton-nlp/SWE-bench_Lite",
            "split": "test",
        },
        "repository": {
            "provider": "github",
            "owner": owner,
            "name": name,
            "full_name": instance["repo"],
            "base_ref": None,
            "base_sha": instance.get("base_commit"),
            "version": instance.get("version"),
        },
        "goal": {
            "text_path": goal_path.as_posix(),
        },
        "policy": {
            "mode": "patch_only",
            "allow_push": False,
            "allow_pr": False,
        },
        "environment": {
            "sandbox_provider": sandbox_provider,
        },
    }


def run_id_for_task(task_id: str, attempt_id: str = DEFAULT_ATTEMPT_ID) -> str:
    """Return the deterministic runs-v1 directory id for a task attempt."""
    return f"{task_id}--{attempt_id}"


def build_attempt_record(
    *,
    instance_id: str,
    attempt_id: str,
    result: dict[str, Any],
    config_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build the canonical per-attempt index sidecar."""
    patch_path = config_dir / "patch.diff"
    prediction_path = config_dir / "prediction.json"
    verify_path = config_dir / "verify.json"
    audit_path = config_dir / "audit.json"
    exported_trajectory_path = config_dir / "trajectory.jsonl"
    dump_path = _optional_path(result.get("fabro_dump_dir"))
    run_dir = _optional_path(result.get("fabro_run_dir"))
    events_path = _optional_path(result.get("events_path"))
    trajectory_path = _optional_path(result.get("trajectory_path"))

    status = result.get("status", "error")
    solve_status = _phase_status_for_solve(status)
    change_status = "completed" if result.get("model_patch", "").strip() else "failed"

    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": instance_id,
        "attempt_id": attempt_id,
        "selected": True,
        "status": status,
        "duration_s": result.get("duration_s", 0),
        "phases": {
            "solve": {"status": solve_status},
            "change": {
                "status": change_status,
                "patch_path": _relative_to(patch_path, config_dir),
            },
            "verify": _verify_phase(result, verify_path, config_dir),
            "audit": _audit_phase(result, audit_path, config_dir),
            "review": _review_phase(result),
            "publish": {"status": "not_run"},
            "grade": {"status": "not_run"},
        },
        "candidate": build_candidate_record(result, patch_path, config_dir),
        "fabro": {
            "run_id": result.get("fabro_run_id"),
            "run_dir": _relative_to(run_dir, output_dir) if run_dir else None,
            "dump_path": _relative_to(dump_path, config_dir) if dump_path else None,
            "events_path": _relative_to(events_path, config_dir) if events_path else None,
            "trajectory_path": (
                _relative_to(trajectory_path, config_dir) if trajectory_path else None
            ),
        },
        "exports": {
            "swebench_prediction": _relative_to(prediction_path, config_dir),
            **_maybe_export("trajectory", exported_trajectory_path, config_dir),
        },
        "error": result.get("error"),
    }


def write_attempt_sidecars(
    *,
    instance: dict[str, Any],
    result: dict[str, Any],
    output_dir: Path,
    config_dir: Path,
    sandbox_provider: str,
    attempt_id: str = DEFAULT_ATTEMPT_ID,
) -> dict[str, str]:
    """Write task, attempt, prediction, and patch sidecars for one attempt."""
    goal_path = config_dir / "goal.txt"
    patch_path = config_dir / "patch.diff"
    prediction_path = config_dir / "prediction.json"
    verify_path = config_dir / "verify.json"
    audit_path = config_dir / "audit.json"
    trajectory_export_path = config_dir / "trajectory.jsonl"
    task_path = config_dir / "task.json"
    attempt_path = config_dir / "attempt.json"

    patch_path.write_text(result.get("model_patch", ""))
    _write_verify_artifact(verify_path, result)
    _write_audit_artifact(audit_path, result)
    _copy_optional(result.get("trajectory_path"), trajectory_export_path)
    _write_json_atomic(
        task_path,
        build_task_record(instance, Path(goal_path.name), sandbox_provider),
    )
    _write_json_atomic(prediction_path, build_prediction_record(result))
    _write_json_atomic(
        attempt_path,
        build_attempt_record(
            instance_id=instance["instance_id"],
            attempt_id=attempt_id,
            result=result,
            config_dir=config_dir,
            output_dir=output_dir,
        ),
    )

    return {
        "task": _relative_to(task_path, output_dir),
        "attempt": _relative_to(attempt_path, output_dir),
        "patch": _relative_to(patch_path, output_dir),
        "prediction": _relative_to(prediction_path, output_dir),
        **_maybe_artifact("verify", verify_path, output_dir),
        **_maybe_artifact("audit", audit_path, output_dir),
        **_maybe_artifact("trajectory", trajectory_export_path, output_dir),
    }


def build_run_record(
    *,
    task_id: str,
    run_id: str,
    attempt_id: str,
    result: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    """Build the canonical runs-v1 run index."""
    patch_path = run_dir / "output" / "patch.diff"
    prediction_path = run_dir / "output" / "prediction.json"
    verify_path = run_dir / "output" / "verify.json"
    audit_path = run_dir / "output" / "audit.json"
    exported_trajectory_path = run_dir / "output" / "trajectory.jsonl"
    dump_path = run_dir / "fabro" / "dump"
    events_path = dump_path / "events.jsonl"
    trajectory_path = dump_path / "trajectory.jsonl"

    status = result.get("status", "error")
    solve_status = _phase_status_for_solve(status)
    change_status = "completed" if result.get("model_patch", "").strip() else "failed"

    return {
        "schema_version": SCHEMA_VERSION,
        "layout": RUNS_LAYOUT,
        "run_id": run_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "selected": True,
        "status": status,
        "duration_s": result.get("duration_s", 0),
        "source": {
            "kind": "swe_bench",
            "external_id": task_id,
        },
        "phases": {
            "solve": {"status": solve_status},
            "change": {
                "status": change_status,
                "patch_path": _relative_to(patch_path, run_dir),
            },
            "verify": _verify_phase(result, verify_path, run_dir),
            "audit": _audit_phase(result, audit_path, run_dir),
            "review": _review_phase(result),
            "publish": {"status": "not_run"},
            "grade": {"status": "not_run"},
        },
        "candidate": build_candidate_record(result, patch_path, run_dir),
        "fabro": {
            "run_id": result.get("fabro_run_id"),
            "dump_path": _relative_to(dump_path, run_dir) if dump_path.exists() else None,
            "events_path": _relative_to(events_path, run_dir)
            if events_path.exists()
            else None,
            "trajectory_path": _relative_to(trajectory_path, run_dir)
            if trajectory_path.exists()
            else None,
        },
        "exports": {
            "swebench_prediction": _relative_to(prediction_path, run_dir),
            **_maybe_export("trajectory", exported_trajectory_path, run_dir),
        },
        "error": result.get("error"),
    }


def write_run_bundle(
    *,
    instance: dict[str, Any],
    result: dict[str, Any],
    output_dir: Path,
    config_dir: Path,
    sandbox_provider: str,
    run_id: str | None = None,
    attempt_id: str = DEFAULT_ATTEMPT_ID,
) -> dict[str, str]:
    """Write a runs-v1 bundle for one task attempt."""
    task_id = instance["instance_id"]
    run_id = run_id or run_id_for_task(task_id, attempt_id)
    run_dir = output_dir / "runs" / run_id
    input_dir = run_dir / "input"
    config_out_dir = run_dir / "config"
    dump_out_dir = run_dir / "fabro" / "dump"
    output_out_dir = run_dir / "output"

    input_dir.mkdir(parents=True, exist_ok=True)
    config_out_dir.mkdir(parents=True, exist_ok=True)
    output_out_dir.mkdir(parents=True, exist_ok=True)

    goal_src = config_dir / "goal.txt"
    goal_path = input_dir / "goal.md"
    goal_path.write_text(goal_src.read_text() if goal_src.exists() else "")

    for name in ("workflow.fabro", "workflow.toml"):
        src = config_dir / name
        if src.exists():
            shutil.copy2(src, config_out_dir / name)

    dump_src = config_dir / "run_dump"
    if dump_src.exists():
        if dump_out_dir.exists():
            shutil.rmtree(dump_out_dir)
        shutil.copytree(dump_src, dump_out_dir)

    patch_path = output_out_dir / "patch.diff"
    patch_path.write_text(result.get("model_patch", ""))
    prediction_path = output_out_dir / "prediction.json"
    _write_json_atomic(prediction_path, build_prediction_record(result))
    verify_path = output_out_dir / "verify.json"
    _write_verify_artifact(verify_path, result)
    audit_path = output_out_dir / "audit.json"
    _write_audit_artifact(audit_path, result)
    trajectory_export_path = output_out_dir / "trajectory.jsonl"
    _copy_optional(result.get("trajectory_path"), trajectory_export_path)

    task_record = build_task_record(instance, Path("input/goal.md"), sandbox_provider)
    _write_json_atomic(run_dir / "task.json", task_record)
    _write_json_atomic(input_dir / "envelope.json", task_record)

    run_record = build_run_record(
        task_id=task_id,
        run_id=run_id,
        attempt_id=attempt_id,
        result=result,
        run_dir=run_dir,
    )
    _write_json_atomic(run_dir / "run.json", run_record)
    _write_json_atomic(output_out_dir / "envelope.json", run_record)

    return {
        "run": _relative_to(run_dir / "run.json", output_dir),
        "task": _relative_to(run_dir / "task.json", output_dir),
        "patch": _relative_to(patch_path, output_dir),
        "prediction": _relative_to(prediction_path, output_dir),
        **_maybe_artifact("verify", verify_path, output_dir),
        **_maybe_artifact("audit", audit_path, output_dir),
        **_maybe_artifact("trajectory", trajectory_export_path, output_dir),
        "fabro_dump": _relative_to(dump_out_dir, output_dir) if dump_out_dir.exists() else "",
    }


def load_or_init_manifest(
    output_dir: Path,
    *,
    layout: str = LAYOUT,
    exports: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Load the root manifest, or initialize a compatibility manifest."""
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            pass

    if exports is None and layout == LAYOUT:
        exports = {
            "swebench_predictions": "predictions.jsonl",
            "swebench_results": "results.jsonl",
            "swebench_summary": "summary.json",
        }
    elif exports is None:
        exports = {}

    return {
        "schema_version": SCHEMA_VERSION,
        "layout": layout,
        "exports": exports,
        "tasks": {},
    }


def update_manifest_for_attempt(
    manifest: dict[str, Any],
    *,
    task_id: str,
    attempt_id: str,
    output_dir: Path,
    config_dir: Path,
) -> None:
    """Record one task attempt in the root manifest."""
    task_path = config_dir / "task.json"
    attempt_path = config_dir / "attempt.json"
    task_entry = manifest.setdefault("tasks", {}).setdefault(
        task_id,
        {
            "selected_attempt": attempt_id,
            "task_path": _relative_to(task_path, output_dir),
            "attempts": {},
        },
    )
    task_entry["selected_attempt"] = attempt_id
    task_entry["task_path"] = _relative_to(task_path, output_dir)
    task_entry.setdefault("attempts", {})[attempt_id] = {
        "attempt_path": _relative_to(attempt_path, output_dir),
    }


def update_manifest_for_run(
    manifest: dict[str, Any],
    *,
    task_id: str,
    run_id: str,
    attempt_id: str,
    output_dir: Path,
    include_swebench_exports: bool = True,
) -> None:
    """Record one runs-v1 run in the root manifest."""
    run_dir = output_dir / "runs" / run_id
    manifest["layout"] = _merged_layout(manifest.get("layout"))
    if include_swebench_exports:
        manifest.setdefault("exports", {}).setdefault(
            "swebench_predictions_export",
            "exports/swebench/predictions.jsonl",
        )
        manifest.setdefault("exports", {}).setdefault(
            "swebench_results_export",
            "exports/swebench/results.jsonl",
        )
        manifest.setdefault("exports", {}).setdefault(
            "swebench_summary_export",
            "exports/swebench/summary.json",
        )
    manifest.setdefault("runs", {})[run_id] = {
        "run_path": _relative_to(run_dir / "run.json", output_dir),
        "task_path": _relative_to(run_dir / "task.json", output_dir),
        "task_id": task_id,
        "attempt_id": attempt_id,
        "selected": True,
    }
    task_entry = manifest.setdefault("tasks", {}).setdefault(task_id, {})
    task_entry["selected_run"] = run_id
    runs = task_entry.setdefault("runs", {})
    if isinstance(runs, list):
        if run_id not in runs:
            runs.append(run_id)
    else:
        runs[run_id] = {
            "run_path": _relative_to(run_dir / "run.json", output_dir),
            "task_path": _relative_to(run_dir / "task.json", output_dir),
        }


def write_manifest(output_dir: Path, manifest: dict[str, Any]) -> None:
    """Write the root manifest atomically."""
    _write_json_atomic(output_dir / "manifest.json", manifest)


def _phase_status_for_solve(status: str) -> str:
    if status in {"completed", "no_patch"}:
        return "completed"
    if status == "verify_failed":
        return "failed"
    if status in {"failed", "timeout", "error"}:
        return "failed"
    return status or "failed"


def _verify_phase(result: dict[str, Any], verify_path: Path, base: Path) -> dict[str, Any]:
    verify = result.get("verify")
    if not isinstance(verify, dict):
        return {"status": "not_run"}

    raw_status = verify.get("status")
    if raw_status == "passed":
        status = "completed"
    elif raw_status == "skipped":
        status = "skipped"
    elif raw_status in {"failed", "error"}:
        status = "failed"
    else:
        status = raw_status or "failed"

    phase = {
        "status": status,
        "mode": verify.get("mode"),
        "patch_nonempty": verify.get("patch_nonempty"),
        "failure_reason": verify.get("failure_reason"),
    }
    if verify_path.exists():
        phase["artifact_path"] = _relative_to(verify_path, base)
    return {key: value for key, value in phase.items() if value is not None}


def _audit_phase(result: dict[str, Any], audit_path: Path, base: Path) -> dict[str, Any]:
    audit = result.get("audit")
    if not isinstance(audit, dict):
        return {"status": "not_run"}

    phase = {
        "status": "completed" if audit.get("patch_nonempty") is not None else "failed",
        "patch_nonempty": audit.get("patch_nonempty"),
        "changed_files": audit.get("changed_files"),
        "test_files_changed": audit.get("test_files_changed"),
    }
    if audit_path.exists():
        phase["artifact_path"] = _relative_to(audit_path, base)
    return {key: value for key, value in phase.items() if value is not None}


def _review_phase(result: dict[str, Any]) -> dict[str, Any]:
    review = result.get("review")
    if not isinstance(review, dict):
        return {"status": "not_run"}

    raw_status = review.get("status")
    outcome = review.get("outcome")
    if raw_status in {"succeeded", "passed"} or (
        raw_status is None and outcome in {"succeeded", "passed"}
    ):
        status = "completed"
    elif raw_status in {"failed", "error"} or (
        raw_status is None and outcome in {"failed", "error"}
    ):
        status = "failed"
    else:
        status = raw_status or "failed"

    phase = {
        "status": status,
        "outcome": outcome,
        "preferred_next_label": review.get("preferred_next_label"),
        "failure_class": review.get("failure_class"),
        "failure_reason": review.get("failure_reason"),
        "context_updates": review.get("context_updates"),
    }
    return {key: value for key, value in phase.items() if value is not None}


def build_candidate_record(
    result: dict[str, Any],
    patch_path: Path,
    base: Path,
) -> dict[str, Any]:
    has_patch = bool(result.get("model_patch", "").strip())
    patch_text = result.get("model_patch", "")
    status = result.get("status", "error")
    review = result.get("review") if isinstance(result.get("review"), dict) else {}
    context_updates = review.get("context_updates") if isinstance(review, dict) else None
    if not isinstance(context_updates, dict):
        context_updates = {}

    if not has_patch:
        return {
            "state": "absent",
            "reuse": "none",
        }

    if status == "completed":
        state = "ready"
        reuse = "merge_candidate"
        warning = None
    else:
        state = "failed_with_patch"
        reuse = "continuation_candidate"
        warning = "Do not merge as-is; use this patch as a starting point with the review lesson."

    record = {
        "state": state,
        "reuse": reuse,
        "patch_path": _relative_to(patch_path, base),
        "patch_bytes": len(patch_text),
        "patch_sha256": hashlib.sha256(patch_text.encode()).hexdigest(),
        "warning": warning,
        "failure_class": review.get("failure_class") if isinstance(review, dict) else None,
        "failure_reason": (
            review.get("failure_reason") if isinstance(review, dict) else None
        )
        or result.get("error"),
        "do_not_repeat": context_updates.get("do_not_repeat"),
        "next_agent_guidance": context_updates.get("next_agent_guidance"),
    }
    return {key: value for key, value in record.items() if value is not None}


def _write_verify_artifact(path: Path, result: dict[str, Any]) -> None:
    verify = result.get("verify")
    if isinstance(verify, dict):
        _write_json_atomic(path, verify)
    else:
        path.unlink(missing_ok=True)


def _write_audit_artifact(path: Path, result: dict[str, Any]) -> None:
    audit = result.get("audit")
    if isinstance(audit, dict):
        _write_json_atomic(path, audit)
    else:
        path.unlink(missing_ok=True)


def _copy_optional(src_value: Any, dst: Path) -> None:
    src = _optional_path(src_value)
    if src and src.exists():
        shutil.copy2(src, dst)
    else:
        dst.unlink(missing_ok=True)


def _maybe_artifact(key: str, path: Path, base: Path) -> dict[str, str]:
    if path.exists():
        return {key: _relative_to(path, base)}
    return {}


def _maybe_export(key: str, path: Path, base: Path) -> dict[str, str]:
    return _maybe_artifact(key, path, base)


def _optional_path(value: Any) -> Path | None:
    if not value:
        return None
    return Path(str(value))


def _split_repo(repo: str) -> tuple[str | None, str]:
    parts = repo.split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, repo


def _merged_layout(layout: Any) -> str:
    if layout in {RUNS_LAYOUT, f"{LAYOUT}+{RUNS_LAYOUT}"}:
        return str(layout)
    if layout == LAYOUT:
        return f"{LAYOUT}+{RUNS_LAYOUT}"
    return RUNS_LAYOUT


def _relative_to(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp_path.replace(path)
