"""Shared Fabro run extraction helpers for issue-to-PR adapters."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


def parse_run_ref(stdout: str, stderr: str) -> tuple[str | None, Path | None]:
    """Parse either a server run ID or local run directory from Fabro output."""
    run_id = None
    run_dir = None
    for line in (stdout + "\n" + stderr).splitlines():
        stripped = line.strip()
        if not stripped.startswith("Run:"):
            continue
        value = stripped.split("Run:", 1)[1].strip()
        if "/" in value:
            run_dir = Path(value.replace("~", str(Path.home())))
        elif value:
            run_id = value
    return run_id, run_dir


def dump_run(
    fabro_bin: str,
    run_id: str,
    config_dir: Path,
    timeout: int = 120,
) -> Path | None:
    """Dump a server-backed run to local files."""
    dump_dir = config_dir / "run_dump"
    if dump_dir.exists():
        shutil.rmtree(dump_dir)
    proc = subprocess.run(
        [fabro_bin, "dump", "--output", str(dump_dir), run_id],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return dump_dir if proc.returncode == 0 else None


def find_patch(run_dir: Path) -> str | None:
    return find_stage_output(run_dir, "extract_patch") or find_stage_output(
        run_dir,
        "snapshot_patch",
    )


def find_verify_record(run_dir: Path) -> dict[str, Any] | None:
    output = find_stage_output(run_dir, "verify")
    if not output:
        return None
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "status" in value:
            return value
    return None


def find_audit_record(run_dir: Path) -> dict[str, Any] | None:
    output = find_stage_output(run_dir, "audit")
    return _last_json_object(output) if output else None


def find_review_record(run_dir: Path) -> dict[str, Any] | None:
    status = find_stage_status(run_dir, "review")
    if not status:
        return None
    routing = _last_json_object(find_stage_output(run_dir, "review") or "")
    if isinstance(routing, dict):
        merged = {**routing, **status}
        if status.get("failure_reason") is None and routing.get("failure_reason"):
            merged["failure_reason"] = routing["failure_reason"]
        return {key: value for key, value in merged.items() if value is not None}
    return {key: value for key, value in status.items() if value is not None}


def find_test_evidence_gate_record(run_dir: Path) -> dict[str, Any] | None:
    output = find_stage_output(run_dir, "test_evidence_gate")
    value = _last_json_object(output) if output else None
    return value if isinstance(value, dict) and "status" in value else None


def find_json_stage_record(run_dir: Path, node_id: str) -> dict[str, Any] | None:
    output = find_stage_output(run_dir, node_id)
    value = _last_json_object(output) if output else None
    return value if isinstance(value, dict) else None


def find_stage_output(run_dir: Path, node_id: str) -> str | None:
    for stage_dir in _stage_dirs(run_dir):
        if not _stage_dir_matches(stage_dir, node_id):
            continue
        for name in ("stdout.log", "output.log", "response.md"):
            path = stage_dir / name
            if path.exists():
                return path.read_text()
    return None


def find_stage_status(run_dir: Path, node_id: str) -> dict[str, Any] | None:
    for stage_dir in _stage_dirs(run_dir):
        if not _stage_dir_matches(stage_dir, node_id):
            continue
        path = stage_dir / "status.json"
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _stage_dirs(run_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for root_name in ("nodes", "stages"):
        root = run_dir / root_name
        if root.exists():
            candidates.extend(path for path in root.iterdir() if path.is_dir())
    return sorted(candidates, key=_stage_sort_key, reverse=True)


def _last_json_object(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    decoder = json.JSONDecoder()
    for index in reversed([idx for idx, char in enumerate(text) if char == "{"]):
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        tail = text[index + end :].strip()
        if isinstance(value, dict) and (not tail or tail.startswith("```")):
            return value
    return None


def _stage_sort_key(path: Path) -> tuple[int, int, str]:
    match = re.search(r"(\d+)-.*@(\d+)$", path.name)
    if match:
        return int(match.group(2)), int(match.group(1)), path.name
    match = re.search(r"(\d+)", path.name)
    return (0, int(match.group(1)), path.name) if match else (0, 0, path.name)


def _stage_dir_matches(path: Path, node_id: str) -> bool:
    name = path.name
    if name == node_id or name.startswith(f"{node_id}@"):
        return True
    return bool(re.match(rf"^\d+-{re.escape(node_id)}@", name))


TRAJECTORY_EVENTS = {
    "agent.input",
    "agent.message",
    "agent.tool.started",
    "agent.tool.completed",
    "agent.error",
    "agent.warning",
    "agent.loop_detected",
    "agent.turn_limit_reached",
    "agent.steering_injected",
    "agent.compaction.started",
    "agent.compaction.completed",
    "agent.processing_end",
}


def trajectory_entry(event: dict[str, Any]) -> dict[str, Any] | None:
    event_name = event.get("event")
    if event_name not in TRAJECTORY_EVENTS:
        return None
    props = event.get("properties") or {}
    entry = {
        "seq": event.get("seq"),
        "ts": event.get("ts"),
        "run_id": event.get("run_id"),
        "event": event_name,
        "stage_id": event.get("stage_id"),
        "node_id": event.get("node_id"),
        "node_label": event.get("node_label"),
        "session_id": event.get("session_id"),
        "visit": props.get("visit"),
    }
    if event_name == "agent.input":
        entry.update({"role": "user", "text": props.get("text", "")})
    elif event_name == "agent.message":
        entry.update({
            "role": "assistant",
            "text": props.get("text", ""),
            "model": props.get("model"),
            "billing": props.get("billing"),
            "tool_call_count": props.get("tool_call_count"),
        })
    elif event_name == "agent.tool.started":
        entry.update({
            "role": "tool_call",
            "tool_name": props.get("tool_name"),
            "tool_call_id": props.get("tool_call_id") or event.get("tool_call_id"),
            "arguments": props.get("arguments"),
        })
    elif event_name == "agent.tool.completed":
        entry.update({
            "role": "tool_result",
            "tool_name": props.get("tool_name"),
            "tool_call_id": props.get("tool_call_id") or event.get("tool_call_id"),
            "output": props.get("output"),
            "is_error": props.get("is_error"),
        })
    else:
        entry["properties"] = props
    return {key: value for key, value in entry.items() if value is not None}


def write_trajectory_from_events(dump_dir: Path) -> Path | None:
    events_path = dump_dir / "events.jsonl"
    if not events_path.exists():
        return None
    trajectory_path = dump_dir / "trajectory.jsonl"
    count = 0
    with events_path.open() as events, trajectory_path.open("w") as trajectory:
        for line in events:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry = trajectory_entry(event)
            if entry is not None:
                trajectory.write(json.dumps(entry) + "\n")
                count += 1
    if count == 0:
        trajectory_path.unlink(missing_ok=True)
        return None
    return trajectory_path
