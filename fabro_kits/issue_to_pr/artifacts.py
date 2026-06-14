"""Canonical artifact bundle helpers for issue-to-PR attempts."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
RUNS_LAYOUT = "issue-to-pr-runs-v1"
DEFAULT_ATTEMPT_ID = "001"
REVIEW_ARTIFACT_NAMES = (
    "adversarial_review",
    "moderator_filter",
    "review_materialization",
    "review_accountability_gate",
)
READY_TIERS = {
    "ready_verified",
    "ready_unverified",
    "needs_fix_code",
    "needs_fix_tests",
    "metadata_only_warning",
    "process_failed",
}


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
    """Return the deterministic run directory id for a task attempt."""
    return f"{task_id}--{attempt_id}"


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
    """Write one canonical `runs/<run_id>/` bundle."""
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

    for key in (
        "verify",
        "audit",
        "test_evidence_gate",
        *REVIEW_ARTIFACT_NAMES,
    ):
        _write_named_json_artifact(output_out_dir / f"{key}.json", result, key)

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

    artifacts = {
        "run": _relative_to(run_dir / "run.json", output_dir),
        "task": _relative_to(run_dir / "task.json", output_dir),
        "patch": _relative_to(patch_path, output_dir),
        "prediction": _relative_to(prediction_path, output_dir),
        "fabro_dump": _relative_to(dump_out_dir, output_dir) if dump_out_dir.exists() else "",
    }
    for key in (
        "verify",
        "audit",
        "test_evidence_gate",
        *REVIEW_ARTIFACT_NAMES,
        "trajectory",
    ):
        path = trajectory_export_path if key == "trajectory" else output_out_dir / f"{key}.json"
        artifacts.update(_maybe_artifact(key, path, output_dir))
    return artifacts


def build_run_record(
    *,
    task_id: str,
    run_id: str,
    attempt_id: str,
    result: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    """Build the canonical run index for one attempt."""
    output_dir = run_dir / "output"
    patch_path = output_dir / "patch.diff"
    prediction_path = output_dir / "prediction.json"
    dump_path = run_dir / "fabro" / "dump"
    events_path = dump_path / "events.jsonl"
    trajectory_path = dump_path / "trajectory.jsonl"

    status = result.get("status", "error")
    phases = {
        "solve": {"status": _phase_status_for_solve(status)},
        "change": {
            "status": "completed" if result.get("model_patch", "").strip() else "failed",
            "patch_path": _relative_to(patch_path, run_dir),
        },
        "verify": _verify_phase(result, output_dir / "verify.json", run_dir),
        "audit": _audit_phase(result, output_dir / "audit.json", run_dir),
        "test_evidence_gate": _test_evidence_gate_phase(
            result,
            output_dir / "test_evidence_gate.json",
            run_dir,
        ),
        "adversarial_review": _generic_review_phase(
            result,
            "adversarial_review",
            output_dir / "adversarial_review.json",
            run_dir,
        ),
        "moderator_filter": _generic_review_phase(
            result,
            "moderator_filter",
            output_dir / "moderator_filter.json",
            run_dir,
        ),
        "review_materialization": _generic_review_phase(
            result,
            "review_materialization",
            output_dir / "review_materialization.json",
            run_dir,
        ),
        "review_accountability_gate": _review_accountability_gate_phase(
            result,
            output_dir / "review_accountability_gate.json",
            run_dir,
        ),
        "review": _review_phase(result),
        "publish": {"status": "not_run"},
        "grade": {"status": "not_run"},
    }

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
        "phases": phases,
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
            **_maybe_artifact("trajectory", output_dir / "trajectory.jsonl", run_dir),
        },
        "error": result.get("error"),
    }


def build_candidate_record(
    result: dict[str, Any],
    patch_path: Path,
    base: Path,
) -> dict[str, Any]:
    """Describe whether the retained patch is publishable or only reusable."""
    patch_text = result.get("model_patch", "")
    if not patch_text.strip():
        return {"state": "absent", "reuse": "none"}

    review = result.get("review") if isinstance(result.get("review"), dict) else {}
    gate = (
        result.get("review_accountability_gate")
        if isinstance(result.get("review_accountability_gate"), dict)
        else {}
    )
    test_gate = (
        result.get("test_evidence_gate")
        if isinstance(result.get("test_evidence_gate"), dict)
        else {}
    )
    context_updates = review.get("context_updates")
    if not isinstance(context_updates, dict):
        context_updates = {}

    if result.get("status") == "completed":
        state = "ready"
        reuse = "merge_candidate"
        warning = None
    else:
        state = "failed_with_patch"
        reuse = "continuation_candidate"
        warning = "Do not merge as-is; use this patch as continuation material."

    readiness_tier = gate.get("readiness_tier")
    if readiness_tier not in READY_TIERS:
        readiness_tier = None

    record = {
        "state": state,
        "reuse": reuse,
        "patch_path": _relative_to(patch_path, base),
        "patch_bytes": len(patch_text.encode()),
        "patch_sha256": hashlib.sha256(patch_text.encode()).hexdigest(),
        "warning": warning,
        "readiness_tier": readiness_tier,
        "failure_class": review.get("failure_class"),
        "failure_reason": (
            review.get("failure_reason")
            or gate.get("failure_reason")
            or test_gate.get("failure_reason")
            or result.get("error")
        ),
        "do_not_repeat": context_updates.get("do_not_repeat")
        or gate.get("do_not_repeat"),
        "next_agent_guidance": context_updates.get("next_agent_guidance")
        or gate.get("next_agent_guidance"),
    }
    return {key: value for key, value in record.items() if value is not None}


def load_or_init_manifest(
    output_dir: Path,
    *,
    layout: str = RUNS_LAYOUT,
    exports: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Load or initialize the root output manifest."""
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            pass

    return {
        "schema_version": SCHEMA_VERSION,
        "layout": layout,
        "exports": exports
        or {
            "swebench_predictions": "predictions.jsonl",
            "swebench_results": "results.jsonl",
            "swebench_summary": "summary.json",
        },
        "tasks": {},
        "runs": {},
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
    """Record one canonical run in the root manifest."""
    run_dir = output_dir / "runs" / run_id
    manifest["layout"] = RUNS_LAYOUT
    if include_swebench_exports:
        manifest.setdefault("exports", {}).setdefault(
            "swebench_predictions",
            "predictions.jsonl",
        )
        manifest.setdefault("exports", {}).setdefault(
            "swebench_results",
            "results.jsonl",
        )
        manifest.setdefault("exports", {}).setdefault(
            "swebench_summary",
            "summary.json",
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
    task_entry.setdefault("runs", {})[run_id] = {
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


def _verify_phase(result: dict[str, Any], path: Path, base: Path) -> dict[str, Any]:
    verify = result.get("verify")
    if not isinstance(verify, dict):
        return {"status": "not_run"}
    status = _phase_status(verify.get("status"))
    phase = {
        "status": status,
        "mode": verify.get("mode"),
        "patch_nonempty": verify.get("patch_nonempty"),
        "failure_reason": verify.get("failure_reason"),
    }
    return _with_artifact(phase, path, base)


def _audit_phase(result: dict[str, Any], path: Path, base: Path) -> dict[str, Any]:
    audit = result.get("audit")
    if not isinstance(audit, dict):
        return {"status": "not_run"}
    phase = {
        "status": "completed" if audit.get("patch_nonempty") is not None else "failed",
        "patch_nonempty": audit.get("patch_nonempty"),
        "changed_files": audit.get("changed_files"),
        "test_files_changed": audit.get("test_files_changed"),
    }
    return _with_artifact(phase, path, base)


def _test_evidence_gate_phase(
    result: dict[str, Any],
    path: Path,
    base: Path,
) -> dict[str, Any]:
    gate = result.get("test_evidence_gate")
    if not isinstance(gate, dict):
        return {"status": "not_run"}
    judgment = gate.get("judgment") if isinstance(gate.get("judgment"), dict) else {}
    phase = {
        "status": _phase_status(gate.get("status")),
        "mode": gate.get("mode"),
        "failure_reason": gate.get("failure_reason"),
        "hard_failures": judgment.get("hard_failures") or gate.get("contradictions"),
        "warnings": judgment.get("warnings") or gate.get("warnings"),
        "route_decision": judgment.get("route_decision"),
        "fixup_guidance": judgment.get("fixup_guidance"),
    }
    return _with_artifact(phase, path, base)


def _generic_review_phase(
    result: dict[str, Any],
    key: str,
    path: Path,
    base: Path,
) -> dict[str, Any]:
    artifact = result.get(key)
    if not isinstance(artifact, dict):
        return {"status": "not_run"}
    phase = {
        "status": _phase_status(artifact.get("status"), default="completed"),
        "stage": artifact.get("stage"),
        "summary": artifact.get("summary"),
        "readiness_tier": artifact.get("readiness_tier"),
        "overall_risk": artifact.get("overall_risk"),
    }
    rows = artifact.get("rows")
    dispositions = artifact.get("dispositions")
    if isinstance(rows, list):
        phase["row_count"] = len(rows)
    if isinstance(dispositions, list):
        phase["disposition_count"] = len(dispositions)
    return _with_artifact(phase, path, base)


def _review_accountability_gate_phase(
    result: dict[str, Any],
    path: Path,
    base: Path,
) -> dict[str, Any]:
    gate = result.get("review_accountability_gate")
    if not isinstance(gate, dict):
        return {"status": "not_run"}
    phase = {
        "status": _phase_status(gate.get("status") or gate.get("process_status")),
        "process_status": gate.get("process_status"),
        "route_decision": gate.get("route_decision"),
        "readiness_tier": gate.get("readiness_tier"),
        "failure_reason": gate.get("failure_reason"),
        "process_failures": gate.get("process_failures"),
        "adversarial_row_count": gate.get("adversarial_row_count"),
        "moderator_disposition_count": gate.get("moderator_disposition_count"),
        "fixup_required_rows": gate.get("fixup_required_rows"),
        "blocking_rows": gate.get("blocking_rows"),
        "malformed_artifacts": gate.get("malformed_artifacts"),
        "do_not_repeat": gate.get("do_not_repeat"),
        "next_agent_guidance": gate.get("next_agent_guidance"),
    }
    return _with_artifact(phase, path, base)


def _review_phase(result: dict[str, Any]) -> dict[str, Any]:
    review = result.get("review")
    if not isinstance(review, dict):
        return {"status": "not_run"}
    phase = {
        "status": _phase_status(review.get("status") or review.get("outcome")),
        "outcome": review.get("outcome"),
        "preferred_next_label": review.get("preferred_next_label"),
        "failure_class": review.get("failure_class"),
        "failure_reason": review.get("failure_reason"),
        "context_updates": review.get("context_updates"),
    }
    return {key: value for key, value in phase.items() if value is not None}


def _phase_status(value: Any, *, default: str = "failed") -> str:
    if value in {"passed", "succeeded"}:
        return "completed"
    if value == "skipped":
        return "skipped"
    if value in {"failed", "error", "process_failed"}:
        return "failed"
    return str(value) if value else default


def _with_artifact(phase: dict[str, Any], path: Path, base: Path) -> dict[str, Any]:
    if path.exists():
        phase["artifact_path"] = _relative_to(path, base)
    return {key: value for key, value in phase.items() if value is not None}


def _write_named_json_artifact(path: Path, result: dict[str, Any], key: str) -> None:
    artifact = result.get(key)
    if isinstance(artifact, dict):
        _write_json_atomic(path, artifact)
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


def _optional_path(value: Any) -> Path | None:
    if not value:
        return None
    return Path(str(value))


def _split_repo(repo: str) -> tuple[str | None, str]:
    parts = repo.split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, repo


def _relative_to(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp_path.replace(path)
