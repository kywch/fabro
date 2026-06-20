"""Artifact materialization helpers for mini-SWE model attempts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..run_attempt import find_json_stage_record, find_test_evidence_gate_record


def materialize_model_artifacts(
    *,
    dump_path: Path,
    artifacts_dir: Path,
    workspace_dir: Path | None = None,
) -> dict[str, str]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, str] = {}
    candidates = {
        "audit": find_dump_json_file(dump_path, "diff-audit.json")
        or find_workspace_issue_json_file(workspace_dir, "diff-audit.json")
        or find_json_stage_record(dump_path, "audit"),
        "validation_contract": find_dump_json_file(dump_path, "validation.json")
        or find_workspace_issue_json_file(workspace_dir, "validation.json"),
        "test_evidence_gate": find_dump_json_file(dump_path, "test-evidence-gate.json")
        or find_workspace_issue_json_file(workspace_dir, "test-evidence-gate.json")
        or find_test_evidence_gate_record(dump_path),
        "adversarial_review": find_dump_json_file(dump_path, "adversarial-review.json")
        or find_workspace_issue_json_file(workspace_dir, "adversarial-review.json")
        or find_json_stage_record(dump_path, "adversarial_review"),
        "moderator_filter": find_dump_json_file(dump_path, "moderator-filter.json")
        or find_workspace_issue_json_file(workspace_dir, "moderator-filter.json")
        or find_json_stage_record(dump_path, "moderator_filter"),
        "review_materialization": find_dump_json_file(
            dump_path,
            "review-materialization.json",
        )
        or find_workspace_issue_json_file(workspace_dir, "review-materialization.json")
        or find_json_stage_record(dump_path, "materialize_review_artifacts"),
    }
    for name, payload in candidates.items():
        if not isinstance(payload, dict):
            continue
        path = artifacts_dir / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        artifacts[name] = path.as_posix()
    return artifacts


def find_workspace_issue_json_file(
    workspace_dir: Path | None,
    name: str,
) -> dict[str, Any] | None:
    if workspace_dir is None:
        return None
    path = workspace_dir / ".fabro" / "issue-to-pr" / name
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def find_dump_json_file(dump_path: Path, name: str) -> dict[str, Any] | None:
    for path in sorted(dump_path.rglob(name)):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def read_artifact_or_default(path: str | None, default: dict[str, Any]) -> dict[str, Any]:
    if not path:
        return default
    artifact_path = Path(path)
    if not artifact_path.exists():
        return default
    payload = json.loads(artifact_path.read_text())
    return payload if isinstance(payload, dict) else default
