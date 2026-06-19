"""Bundle and instance helpers for lightweight eval outputs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def prepare_config_dir(output_dir: Path, task_id: str) -> Path:
    config_dir = output_dir / "_configs" / task_id
    dump_dir = config_dir / "run_dump"
    dump_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "goal.txt").write_text(f"Replay fixture {task_id}\n")
    (config_dir / "workflow.fabro").write_text("digraph Replay {}\n")
    (config_dir / "workflow.toml").write_text("[workflow]\n")
    (dump_dir / "events.jsonl").write_text("")
    return config_dir

def prepare_synthetic_config_dir(
    output_dir: Path,
    task_id: str,
    *,
    validation_contract: dict[str, Any],
) -> Path:
    config_dir = prepare_config_dir(output_dir, task_id)
    (config_dir / "goal.txt").write_text(
        "Synthetic local-repo task: preserve source-only patch but fail closed when "
        "validation claims tests not present in the regenerated git diff.\n"
    )
    (config_dir / "validation_contract.json").write_text(
        json.dumps(validation_contract, indent=2, sort_keys=True) + "\n"
    )
    return config_dir

def prepare_issue_workflow_smoke_config_dir(
    output_dir: Path,
    task_id: str,
    *,
    workflow_path: Path,
    validation_contract: dict[str, Any],
    run_transcript: str,
) -> Path:
    config_dir = prepare_config_dir(output_dir, task_id)
    shutil.copy2(workflow_path, config_dir / "workflow.fabro")
    (config_dir / "goal.txt").write_text(
        "Issue-to-PR workflow smoke: run scripted Fabro stages that materialize "
        "patch, audit, validation, and review artifacts, then fail closed when "
        "validation claims tests not present in the workflow-produced diff.\n"
    )
    (config_dir / "validation_contract.json").write_text(
        json.dumps(validation_contract, indent=2, sort_keys=True) + "\n"
    )
    dump_dir = config_dir / "run_dump"
    dump_dir.mkdir(parents=True, exist_ok=True)
    (dump_dir / "run.transcript").write_text(run_transcript)
    return config_dir

def instance_for_task(task_id: str) -> dict[str, Any]:
    return {
        "instance_id": task_id,
        "repo": "light-eval/replay",
        "version": "fixture",
        "base_commit": "fixture",
    }

def synthetic_instance_for_task(
    task_id: str,
    *,
    source_kind: str = "synthetic_local_repo",
) -> dict[str, Any]:
    return {
        "instance_id": task_id,
        "repo": "synthetic/local-repo",
        "version": "local",
        "base_commit": "synthetic",
        "source": synthetic_source_for_task(task_id, source_kind=source_kind),
        "repository": {
            "provider": "local",
            "owner": "synthetic",
            "name": "local-repo",
            "full_name": "synthetic/local-repo",
            "base_ref": None,
            "base_sha": "synthetic",
            "version": "local",
        },
    }

def synthetic_source_for_task(
    task_id: str,
    *,
    source_kind: str = "synthetic_local_repo",
) -> dict[str, Any]:
    return {
        "kind": source_kind,
        "external_id": task_id,
        "dataset": dataset_for_source_kind(source_kind),
        "split": "scripted",
    }

def dataset_for_source_kind(source_kind: str) -> str:
    if source_kind == "synthetic_sandboxed_repo":
        return "fabro-kits/issue-to-pr-tier2b"
    if source_kind == "synthetic_workflow_artifact_smoke":
        return "fabro-kits/issue-to-pr-workflow-smoke"
    return "fabro-kits/issue-to-pr-tier2a"
