#!/usr/bin/env python3
"""Run one issue-to-PR style solve attempt with Fabro.

This is intentionally file-driven: callers provide a normalized task JSON, and
the script writes one `runs/<run_id>/` artifact bundle. Dataset loading,
benchmark grading, scoreboards, and batching stay in adapters such as
`evals/swe-bench/run_eval.py`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path


from fabro_kits.issue_to_pr.artifacts import (
    DEFAULT_ATTEMPT_ID,
    RUNS_LAYOUT,
    build_candidate_record,
    load_or_init_manifest,
    run_id_for_task,
    update_manifest_for_run,
    write_manifest,
    write_run_bundle,
)
from fabro_kits.issue_to_pr.workflow_generator import (
    SIMPLE_PROFILE,
    STRUCTURED_PROFILE,
    VERIFY_DIFF_CHECK,
    VERIFY_NONE,
    default_verify_mode,
    escape_goal_for_template,
    generate_issue_to_pr_workflow,
    validate_generated_workflow,
)


def shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def task_goal(task: dict) -> str:
    goal = task.get("goal") or {}
    if goal.get("text"):
        return goal["text"]
    if goal.get("text_path"):
        path = Path(goal["text_path"]).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.read_text()
    raise ValueError("task goal must include goal.text or goal.text_path")


def repo_full_name(task: dict) -> str:
    repo = task.get("repository") or {}
    if repo.get("full_name"):
        return repo["full_name"]
    if repo.get("owner") and repo.get("name"):
        return f"{repo['owner']}/{repo['name']}"
    if repo.get("name") and "/" in repo["name"]:
        return repo["name"]
    raise ValueError("repository must include full_name or owner/name")


def setup_script(task: dict) -> str:
    if (task.get("setup") or {}).get("script"):
        return task["setup"]["script"]
    env = task.get("environment") or {}
    if env.get("setup_script"):
        return env["setup_script"]

    repo = task.get("repository") or {}
    full_name = repo_full_name(task)
    clone_url = repo.get("clone_url") or f"https://github.com/{full_name}.git"
    commands = [f"git clone {shell_quote(clone_url)} ."]
    if repo.get("base_sha"):
        commands.append(f"git checkout {shell_quote(repo['base_sha'])}")
    elif repo.get("base_ref"):
        commands.append(f"git checkout {shell_quote(repo['base_ref'])}")
    for command in env.get("setup_commands", []):
        commands.append(command)
    return " && ".join(commands)


def generate_workflow_fabro(
    task: dict,
    workflow_profile: str = SIMPLE_PROFILE,
    verify_mode: str | None = None,
) -> str:
    setup = setup_script(task)
    return generate_issue_to_pr_workflow(
        graph_name="IssueToPr",
        setup_script=setup,
        workflow_profile=workflow_profile,
        verify_mode=verify_mode,
    )


def generate_workflow_toml(task: dict, workflow_path: Path, sandbox_provider: str) -> str:
    env = task.get("environment") or {}
    image = env.get("image")
    if sandbox_provider != "docker":
        raise ValueError("run_attempt.py currently supports --sandbox-provider docker")
    if not image:
        raise ValueError("docker tasks must provide environment.image")

    return "\n".join([
        "_version = 1",
        "",
        "[workflow]",
        f'graph = "{workflow_path}"',
        "",
        "[run.pull_request]",
        "enabled = false",
        "",
        "[run.clone]",
        "enabled = false",
        "",
        "[run.run_branch]",
        "enabled = false",
        "",
        "[run.meta_branch]",
        "enabled = false",
        "",
        "[run.environment]",
        'id = "issue-to-pr-docker"',
        "",
        "[environments.issue-to-pr-docker]",
        'provider = "docker"',
        "",
        "[environments.issue-to-pr-docker.env]",
        'PATH = "/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"',
        "",
        "[environments.issue-to-pr-docker.resources]",
        "cpu = 2",
        'memory = "4GB"',
        'disk = "10GB"',
        "",
        "[environments.issue-to-pr-docker.image]",
        f'docker = "{image}"',
    ])


def parse_run_ref(stdout: str, stderr: str) -> tuple[str | None, Path | None]:
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


def dump_run(fabro_bin: str, run_id: str, config_dir: Path) -> Path | None:
    dump_dir = config_dir / "run_dump"
    if dump_dir.exists():
        shutil.rmtree(dump_dir)
    proc = subprocess.run(
        [fabro_bin, "dump", "--output", str(dump_dir), run_id],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return None
    return dump_dir


def find_stage_output(run_dir: Path, node_id: str) -> str | None:
    candidates = []
    for root_name in ("nodes", "stages"):
        root = run_dir / root_name
        if root.exists():
            candidates.extend(path for path in root.iterdir() if path.is_dir())
    for stage_dir in sorted(candidates, key=_stage_sort_key, reverse=True):
        if node_id not in stage_dir.name:
            continue
        for name in ("stdout.log", "output.log", "response.md"):
            path = stage_dir / name
            if path.exists():
                return path.read_text()
    return None


def find_patch(run_dir: Path) -> str | None:
    return find_stage_output(run_dir, "extract_patch") or find_stage_output(
        run_dir,
        "snapshot_patch",
    )


def find_stage_status(run_dir: Path, node_id: str) -> dict | None:
    candidates = []
    for root_name in ("nodes", "stages"):
        root = run_dir / root_name
        if root.exists():
            candidates.extend(path for path in root.iterdir() if path.is_dir())
    for stage_dir in sorted(candidates, key=_stage_sort_key, reverse=True):
        if node_id not in stage_dir.name:
            continue
        path = stage_dir / "status.json"
        if not path.exists():
            continue
        try:
            status = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(status, dict):
            return status
    return None


def find_verify_record(run_dir: Path) -> dict | None:
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


def find_audit_record(run_dir: Path) -> dict | None:
    output = find_stage_output(run_dir, "audit")
    if not output:
        return None
    return _last_json_object(output)


def find_review_record(run_dir: Path) -> dict | None:
    status = find_stage_status(run_dir, "review")
    if not status:
        return None
    response = find_stage_output(run_dir, "review")
    routing = _last_json_object(response) if response else None
    if isinstance(routing, dict):
        merged = {**routing, **status}
        if status.get("failure_reason") is None and routing.get("failure_reason"):
            merged["failure_reason"] = routing["failure_reason"]
        return {key: value for key, value in merged.items() if value is not None}
    return {key: value for key, value in status.items() if value is not None}


def _last_json_object(text: str) -> dict | None:
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
    return None


def _stage_sort_key(path: Path) -> tuple[int, int, str]:
    import re

    match = re.search(r"(\d+)-.*@(\d+)$", path.name)
    if match:
        return int(match.group(2)), int(match.group(1)), path.name
    match = re.search(r"(\d+)", path.name)
    if match:
        return 0, int(match.group(1)), path.name
    return 0, 0, path.name


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


def trajectory_entry(event: dict) -> dict | None:
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
            if entry is None:
                continue
            trajectory.write(json.dumps(entry) + "\n")
            count += 1
    if count == 0:
        trajectory_path.unlink(missing_ok=True)
        return None
    return trajectory_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one issue-to-PR attempt")
    parser.add_argument("--task-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--attempt-id", default=DEFAULT_ATTEMPT_ID)
    parser.add_argument("--fabro-bin", default="fabro")
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--sandbox-provider", choices=["docker"], default="docker")
    parser.add_argument("--mode", choices=["patch-only", "issue-to-pr"], default="patch-only")
    parser.add_argument(
        "--workflow-profile",
        choices=[SIMPLE_PROFILE, STRUCTURED_PROFILE],
        default=SIMPLE_PROFILE,
    )
    parser.add_argument(
        "--verify-mode",
        choices=[VERIFY_NONE, VERIFY_DIFF_CHECK],
        default=None,
    )
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    args.verify_mode = default_verify_mode(args.workflow_profile, args.verify_mode)

    fabro_path = Path(args.fabro_bin).expanduser()
    if fabro_path.is_absolute() or len(fabro_path.parts) > 1:
        args.fabro_bin = str(fabro_path.resolve())
    else:
        args.fabro_bin = shutil.which(args.fabro_bin) or args.fabro_bin

    task = json.loads(args.task_json.read_text())
    task_id = task["task_id"]
    run_id = args.run_id or run_id_for_task(task_id, args.attempt_id)
    output_dir = args.output_dir.resolve()
    config_dir = output_dir / ".work" / run_id
    config_dir.mkdir(parents=True, exist_ok=True)

    goal_file = config_dir / "goal.txt"
    goal_file.write_text(escape_goal_for_template(task_goal(task)))
    workflow_path = config_dir / "workflow.fabro"
    workflow_content = generate_workflow_fabro(
        task,
        workflow_profile=args.workflow_profile,
        verify_mode=args.verify_mode,
    )
    validate_generated_workflow(
        workflow_content,
        workflow_profile=args.workflow_profile,
    )
    workflow_path.write_text(workflow_content)
    toml_path = config_dir / "workflow.toml"
    toml_path.write_text(generate_workflow_toml(task, workflow_path, args.sandbox_provider))

    start_time = time.time()
    cmd = [
        args.fabro_bin,
        "run",
        str(toml_path),
        "--auto-approve",
        "--model",
        args.model,
        "--provider",
        args.provider,
        "--goal-file",
        str(goal_file),
        "--label",
        f"issue-to-pr={run_id}",
    ]
    proc = subprocess.run(
        cmd,
        cwd="/tmp",
        capture_output=True,
        text=True,
        timeout=args.timeout,
    )
    (config_dir / "fabro_stdout.log").write_text(proc.stdout)
    (config_dir / "fabro_stderr.log").write_text(proc.stderr)

    fabro_run_id, fabro_run_dir = parse_run_ref(proc.stdout, proc.stderr)
    dumped = dump_run(args.fabro_bin, fabro_run_id, config_dir) if fabro_run_id else None
    trajectory_path = write_trajectory_from_events(dumped) if dumped else None
    verify = None
    if args.workflow_profile == STRUCTURED_PROFILE:
        verify = find_verify_record(fabro_run_dir) if fabro_run_dir else None
        if not verify and dumped:
            verify = find_verify_record(dumped)
    audit = None
    if args.workflow_profile == STRUCTURED_PROFILE:
        audit = find_audit_record(fabro_run_dir) if fabro_run_dir else None
        if not audit and dumped:
            audit = find_audit_record(dumped)
    review = None
    if args.workflow_profile == STRUCTURED_PROFILE:
        review = find_review_record(fabro_run_dir) if fabro_run_dir else None
        if not review and dumped:
            review = find_review_record(dumped)
    patch = find_patch(fabro_run_dir) if fabro_run_dir else None
    if not patch and dumped:
        patch = find_patch(dumped)

    status = "completed" if proc.returncode == 0 else "failed"
    error = None if proc.returncode == 0 else f"fabro exited with code {proc.returncode}"
    if (
        args.workflow_profile == STRUCTURED_PROFILE
        and verify
        and verify.get("status") not in {"passed", "skipped"}
    ):
        status = "verify_failed"
        error = verify.get("failure_reason") or "Verify failed"
    elif status == "completed" and not (patch or "").strip():
        status = "no_patch"
        error = "No patch produced"

    result = {
        "instance_id": task_id,
        "model_name_or_path": args.model,
        "model_patch": patch or "",
        "status": status,
        "error": error,
        "duration_s": round(time.time() - start_time, 1),
        "fabro_run_id": fabro_run_id,
        "fabro_run_dir": str(fabro_run_dir) if fabro_run_dir else None,
        "fabro_dump_dir": str(dumped) if dumped else None,
        "events_path": str(dumped / "events.jsonl") if dumped else None,
        "trajectory_path": str(trajectory_path) if trajectory_path else None,
        "verify": verify,
        "audit": audit,
        "review": review,
    }

    instance = {
        "instance_id": task_id,
        "repo": repo_full_name(task),
        "version": (task.get("repository") or {}).get("version"),
        "base_commit": (task.get("repository") or {}).get("base_sha"),
    }
    artifacts = write_run_bundle(
        instance=instance,
        result=result,
        output_dir=output_dir,
        config_dir=config_dir,
        sandbox_provider=args.sandbox_provider,
        run_id=run_id,
        attempt_id=args.attempt_id,
    )
    result["candidate"] = build_candidate_record(
        result,
        output_dir / "runs" / run_id / "output" / "patch.diff",
        output_dir / "runs" / run_id,
    )
    manifest = load_or_init_manifest(output_dir, layout=RUNS_LAYOUT, exports={})
    update_manifest_for_run(
        manifest,
        task_id=task_id,
        run_id=run_id,
        attempt_id=args.attempt_id,
        output_dir=output_dir,
        include_swebench_exports=False,
    )
    write_manifest(output_dir, manifest)

    print(json.dumps({"run_id": run_id, "status": status, "artifacts": artifacts}, indent=2))
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
