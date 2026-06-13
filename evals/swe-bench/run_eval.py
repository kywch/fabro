#!/usr/bin/env python3
"""SWE-bench evaluation orchestrator for Fabro.

Loads SWE-bench Lite instances, generates per-instance workflow configs,
runs Fabro agent in Daytona or Docker sandboxes, and collects patches.

Usage:
    cd evals/swe-bench
    python run_eval.py --output-dir results/haiku-baseline 2>&1 | tee results/haiku-baseline/console.log
"""

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from datasets import load_dataset
from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS

from attempt_artifacts import (
    DEFAULT_ATTEMPT_ID,
    build_prediction_record,
    load_or_init_manifest,
    run_id_for_task,
    update_manifest_for_attempt,
    update_manifest_for_run,
    write_attempt_sidecars,
    write_run_bundle,
    write_manifest,
)
from gen_dockerfile import generate_dockerfile, repo_version_key

EVAL_DIR = Path(__file__).parent.resolve()
ISSUE_TO_PR_DIR = EVAL_DIR.parent / "issue-to-pr"
sys.path.insert(0, str(ISSUE_TO_PR_DIR))

from workflow_generator import (  # noqa: E402
    SIMPLE_PROFILE,
    STRUCTURED_PROFILE,
    VERIFY_DIFF_CHECK,
    VERIFY_NONE,
    default_verify_mode,
    generate_issue_to_pr_workflow,
)

# ---------------------------------------------------------------------------
# Logging — dual output: file (DEBUG) + terminal (INFO)
# ---------------------------------------------------------------------------

log = logging.getLogger("swe-eval")


def setup_logging(output_dir: Path):
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S"
    )

    # File handler — everything
    fh = logging.FileHandler(output_dir / "eval.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    log.addHandler(fh)

    # Console handler — INFO+
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(ch)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def dot_escape(s: str) -> str:
    """Escape a string for use inside DOT double-quoted attribute values."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def load_completed_ids(output_dir: Path) -> set[str]:
    """Load instance IDs that already produced a terminal usable result."""
    completed = set()
    for jsonl_file in [output_dir / "results.jsonl"]:
        if jsonl_file.exists():
            with open(jsonl_file) as f:
                for line in f:
                    if line.strip():
                        try:
                            result = json.loads(line)
                            if result.get("status") in {"completed", "no_patch"}:
                                completed.add(result["instance_id"])
                        except (json.JSONDecodeError, KeyError):
                            pass
    return completed


def load_instances(instance_ids: list[str] | None = None) -> list[dict]:
    """Load SWE-bench Lite instances from HuggingFace."""
    dataset = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    instances = [dict(row) for row in dataset]
    if instance_ids:
        id_set = set(instance_ids)
        instances = [i for i in instances if i["instance_id"] in id_set]
        found = {i["instance_id"] for i in instances}
        missing = id_set - found
        if missing:
            log.warning(f"Instance IDs not found: {missing}")
    return instances


def get_spec(instance: dict) -> dict:
    """Get the swebench spec for an instance's (repo, version) pair."""
    repo = instance["repo"]
    version = instance["version"]
    return MAP_REPO_VERSION_TO_SPECS.get(repo, {}).get(version, {})


def build_goal(instance: dict) -> str:
    """Build the goal text from problem statement and hints."""
    parts = [instance["problem_statement"]]
    hints = instance.get("hints_text", "")
    if hints and hints.strip():
        parts.append(f"\n\n## Additional Context\n\n{hints}")
    return "\n".join(parts)


def build_setup_script(instance: dict) -> str:
    """Build the setup script that runs before the agent.

    Clones the repo, checks out the base commit, runs pre_install commands,
    and installs the package. Runs inside the Daytona sandbox.
    """
    spec = get_spec(instance)
    repo = instance["repo"]
    base_commit = instance["base_commit"]
    install_cmd = spec.get("install", "pip install -e .")

    parts = [
        f"git clone https://github.com/{repo}.git .",
        f"git checkout {base_commit}",
    ]

    pre_install = spec.get("pre_install", [])
    if isinstance(pre_install, str):
        pre_install = [pre_install]
    parts.extend(pre_install)

    parts.append(install_cmd)
    return " && ".join(parts)


def toml_literal_string(text: str) -> str:
    """Wrap text in TOML multi-line literal string (no escape processing)."""
    return f"'''\n{text}'''"


def generate_workflow_fabro(
    instance: dict,
    workflow_profile: str = SIMPLE_PROFILE,
    verify_mode: str | None = None,
) -> str:
    """Generate a per-instance .fabro DOT graph with properly escaped values."""
    setup_script = build_setup_script(instance)
    return generate_issue_to_pr_workflow(
        graph_name="SWEBench",
        setup_script=setup_script,
        workflow_profile=workflow_profile,
        verify_mode=verify_mode,
        solve_prompt="Fix this GitHub issue in the repository. Make the minimal code change needed.",
    )


def ensure_local_docker_image(instance: dict, output_dir: Path) -> str:
    """Build or reuse the local Docker image for an instance environment."""
    repo = instance["repo"]
    version = instance["version"]
    image = repo_version_key(repo, version)

    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
    )
    if inspect.returncode == 0:
        log.debug(f"[{instance['instance_id']}] Reusing Docker image {image}")
        return image

    dockerfile_dir = output_dir / "dockerfiles"
    dockerfile_dir.mkdir(parents=True, exist_ok=True)
    dockerfile_path = dockerfile_dir / f"{image}.Dockerfile"
    dockerfile_path.write_text(generate_dockerfile(repo, version))

    log.info(f"[{instance['instance_id']}] Building Docker image {image}")
    subprocess.run(
        ["docker", "build", "-t", image, "-f", str(dockerfile_path), "."],
        cwd=EVAL_DIR,
        check=True,
    )
    return image


def generate_workflow_toml(
    instance: dict,
    run_dir: Path,
    sandbox_provider: str,
    output_dir: Path,
) -> str:
    """Generate a workflow.toml config for a single instance."""
    repo = instance["repo"]
    version = instance["version"]
    snapshot_name = repo_version_key(repo, version)
    dockerfile = generate_dockerfile(repo, version)
    fabro_path = run_dir / "workflow.fabro"

    lines = [
        '_version = 1',
        '',
        '[workflow]',
        f'graph = "{fabro_path}"',
        '',
        '[run.pull_request]',
        'enabled = false',
        '',
        '[run.clone]',
        'enabled = false',
        '',
        '[run.run_branch]',
        'enabled = false',
        '',
        '[run.meta_branch]',
        'enabled = false',
        '',
        '[run.environment]',
        f'id = "swebench-{sandbox_provider}"',
        '',
        f'[environments.swebench-{sandbox_provider}]',
        f'provider = "{sandbox_provider}"',
        '',
        f'[environments.swebench-{sandbox_provider}.env]',
        'PATH = "/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"',
        '',
        f'[environments.swebench-{sandbox_provider}.resources]',
        'cpu = 2',
        'memory = "4GB"',
        'disk = "10GB"',
    ]

    if sandbox_provider == "daytona":
        lines.extend([
            '',
            f'[environments.swebench-{sandbox_provider}.image]',
            f'dockerfile = {{ contents = {toml_literal_string(dockerfile)} }}',
        ])
    elif sandbox_provider == "docker":
        image = ensure_local_docker_image(instance, output_dir)
        lines.extend([
            '',
            f'[environments.swebench-{sandbox_provider}.image]',
            f'docker = "{image}"',
        ])
    else:
        raise ValueError(f"unsupported sandbox provider: {sandbox_provider}")

    return "\n".join(lines)


def find_stage_output(run_dir: Path, node_id: str) -> str | None:
    """Find the latest stdout-like output for a node in a Fabro run dir."""
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
    """Find the latest extract_patch stdout log in a local or dumped run dir."""
    return find_stage_output(run_dir, "extract_patch")


def find_verify_record(run_dir: Path) -> dict | None:
    """Parse the latest verify JSON object printed by the verify stage."""
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


def _stage_sort_key(path: Path) -> tuple[int, int, str]:
    match = re.search(r"(\d+)-.*@(\d+)$", path.name)
    if match:
        return int(match.group(2)), int(match.group(1)), path.name
    match = re.search(r"(\d+)", path.name)
    if match:
        return 0, int(match.group(1)), path.name
    return 0, 0, path.name


def parse_run_ref(stdout: str, stderr: str) -> tuple[str | None, Path | None]:
    """Parse either a server run ID or a local run dir from fabro output."""
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


def dump_run(fabro_bin: str, run_id: str, config_dir: Path, timeout: int) -> Path | None:
    """Dump a server-backed run to local files and return the dump path."""
    dump_dir = config_dir / "run_dump"
    if dump_dir.exists():
        shutil.rmtree(dump_dir)
    proc = subprocess.run(
        [fabro_bin, "dump", "--output", str(dump_dir), run_id],
        timeout=timeout,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        log.debug(f"[{run_id}] fabro dump failed: {proc.stderr[-500:]}")
        return None
    return dump_dir


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
    """Convert one durable event into the eval trajectory shape."""
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
        "parallel_group_id": event.get("parallel_group_id"),
        "parallel_branch_id": event.get("parallel_branch_id"),
        "visit": props.get("visit"),
    }

    if event_name == "agent.input":
        entry.update({
            "role": "user",
            "text": props.get("text", ""),
        })
    elif event_name == "agent.message":
        entry.update({
            "role": "assistant",
            "text": props.get("text", ""),
            "model": props.get("model"),
            "billing": props.get("billing"),
            "tool_call_count": props.get("tool_call_count"),
            "message": props.get("message"),
            "context_window": props.get("context_window"),
        })
    elif event_name == "agent.tool.started":
        entry.update({
            "role": "tool_call",
            "tool_name": props.get("tool_name"),
            "tool_call_id": props.get("tool_call_id") or event.get("tool_call_id"),
            "arguments": props.get("arguments"),
            "tool_call": props.get("tool_call"),
            "turn_id": props.get("turn_id"),
            "parent_message_id": props.get("parent_message_id"),
        })
    elif event_name == "agent.tool.completed":
        entry.update({
            "role": "tool_result",
            "tool_name": props.get("tool_name"),
            "tool_call_id": props.get("tool_call_id") or event.get("tool_call_id"),
            "output": props.get("output"),
            "is_error": props.get("is_error"),
            "tool_result": props.get("tool_result"),
            "turn_id": props.get("turn_id"),
        })
    else:
        entry["properties"] = props

    return {key: value for key, value in entry.items() if value is not None}


def write_trajectory_from_events(dump_dir: Path) -> Path | None:
    """Write a best-effort agent trajectory JSONL file from a Fabro dump."""
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


# ---------------------------------------------------------------------------
# Per-instance runner
# ---------------------------------------------------------------------------


def run_instance(
    instance: dict,
    model: str,
    provider: str,
    output_dir: Path,
    timeout: int,
    sandbox_provider: str,
    fabro_bin: str,
    output_layout: str,
    workflow_profile: str,
    verify_mode: str,
) -> dict:
    """Run Fabro agent on a single SWE-bench instance."""
    instance_id = instance["instance_id"]
    config_dir = output_dir / "configs" / instance_id

    config_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "instance_id": instance_id,
        "model_name_or_path": model,
        "model_patch": "",
        "status": "error",
        "error": None,
        "duration_s": 0,
        "fabro_run_dir": None,
        "fabro_dump_dir": None,
        "events_path": None,
        "trajectory_path": None,
        "verify": None,
        "artifacts": {},
    }

    start_time = time.time()

    try:
        goal_text = build_goal(instance)
        goal_file = config_dir / "goal.txt"
        goal_file.write_text(goal_text)

        fabro_content = generate_workflow_fabro(
            instance,
            workflow_profile=workflow_profile,
            verify_mode=verify_mode,
        )
        (config_dir / "workflow.fabro").write_text(fabro_content)
        toml_content = generate_workflow_toml(
            instance, config_dir, sandbox_provider, output_dir,
        )
        toml_file = config_dir / "workflow.toml"
        toml_file.write_text(toml_content)

        cmd = [
            fabro_bin, "run", str(toml_file),
            "--auto-approve",
            "--model", model,
            "--provider", provider,
            "--goal-file", str(goal_file),
            "--label", f"swe-bench={instance_id}",
        ]

        log.debug(f"[{instance_id}] Starting fabro run")
        proc = subprocess.run(
            cmd,
            cwd="/tmp",
            timeout=timeout,
            capture_output=True,
            text=True,
        )

        run_id, fabro_run_dir = parse_run_ref(proc.stdout, proc.stderr)
        result["fabro_run_dir"] = str(fabro_run_dir) if fabro_run_dir else None
        result["fabro_run_id"] = run_id
        dumped = dump_run(fabro_bin, run_id, config_dir, timeout=120) if run_id else None
        if dumped:
            result["fabro_dump_dir"] = str(dumped)
            events_path = dumped / "events.jsonl"
            if events_path.exists():
                result["events_path"] = str(events_path)
            trajectory_path = write_trajectory_from_events(dumped)
            if trajectory_path:
                result["trajectory_path"] = str(trajectory_path)

        if proc.returncode != 0:
            result["error"] = f"fabro exited with code {proc.returncode}"
            result["status"] = "failed"
            (config_dir / "fabro_stderr.log").write_text(proc.stderr)
            (config_dir / "fabro_stdout.log").write_text(proc.stdout)
            log.debug(f"[{instance_id}] fabro stderr: {proc.stderr[-300:]}")
        else:
            result["status"] = "completed"

        verify = None
        if workflow_profile == STRUCTURED_PROFILE:
            verify = find_verify_record(fabro_run_dir) if fabro_run_dir else None
            if not verify and dumped:
                verify = find_verify_record(dumped)
            result["verify"] = verify

        # Extract patch from the fabro run dir
        patch = find_patch(fabro_run_dir) if fabro_run_dir else None
        if not patch and dumped:
            patch = find_patch(dumped)
        if patch and patch.strip():
            result["model_patch"] = patch
            if (
                workflow_profile == STRUCTURED_PROFILE
                and verify
                and verify.get("status") not in {"passed", "skipped"}
            ):
                result["status"] = "verify_failed"
                result["error"] = verify.get("failure_reason") or "Verify failed"
            else:
                result["status"] = "completed"
        elif workflow_profile == STRUCTURED_PROFILE and verify and verify.get("status") != "passed":
            result["status"] = "verify_failed"
            result["error"] = verify.get("failure_reason") or "Verify failed"
        elif result["status"] == "completed":
            result["status"] = "no_patch"
            result["error"] = "No patch produced"

    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["error"] = f"Timed out after {timeout}s"
        _cleanup_sandbox(instance_id, sandbox_provider, fabro_bin)
    except Exception as e:
        result["error"] = str(e)
        log.debug(f"[{instance_id}] Exception: {e}")

    result["duration_s"] = round(time.time() - start_time, 1)
    try:
        result["artifacts"] = write_attempt_sidecars(
            instance=instance,
            result=result,
            output_dir=output_dir,
            config_dir=config_dir,
            sandbox_provider=sandbox_provider,
            attempt_id=DEFAULT_ATTEMPT_ID,
        )
        if _writes_runs_layout(output_layout):
            run_id = run_id_for_task(instance_id, DEFAULT_ATTEMPT_ID)
            result["artifacts"]["run_bundle"] = write_run_bundle(
                instance=instance,
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider=sandbox_provider,
                run_id=run_id,
                attempt_id=DEFAULT_ATTEMPT_ID,
            )
    except Exception as e:
        result["artifact_error"] = str(e)
        log.debug(f"[{instance_id}] Artifact sidecar write failed: {e}")
    return result


def _cleanup_sandbox(label_value: str, sandbox_provider: str, fabro_bin: str):
    """Best-effort delete of orphaned Daytona sandbox after timeout.

    Finds the sandbox via `fabro ps --label --json` to get the run ID,
    then deletes any Daytona sandbox whose name contains that run ID.
    """
    if sandbox_provider != "daytona":
        return
    try:
        ps = subprocess.run(
            [fabro_bin, "ps", "--label", f"swe-bench={label_value}", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        runs = json.loads(ps.stdout) if ps.stdout.strip() else []
        for run in runs:
            run_id = run.get("run_id", "")
            if not run_id:
                continue
            sandbox_name = f"fabro-{run_id}"
            subprocess.run(
                ["daytona", "sandbox", "delete", sandbox_name],
                capture_output=True, timeout=15,
            )
            log.debug(f"[{label_value}] Deleted sandbox {sandbox_name}")
    except Exception as e:
        log.debug(f"[{label_value}] Sandbox cleanup failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

DAYTONA_CPU_LIMIT = 500  # org-level max from Daytona tier


def preflight_daytona(max_workers: int, sandbox_cpu: int):
    """Check that we have enough Daytona CPU headroom before starting."""
    needed = max_workers * sandbox_cpu
    buffer = 1.2  # 20% headroom

    # Count CPUs in use by existing sandboxes
    used_cpus = 0
    try:
        result = subprocess.run(
            ["daytona", "sandbox", "list"],
            capture_output=True, text=True, timeout=10,
        )
        import re
        # Count sandbox entries (each has a UUID)
        sandbox_count = len(re.findall(
            r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
            result.stdout,
        ))
        # Daytona list doesn't show CPU per sandbox; assume worst case (same size as ours)
        used_cpus = sandbox_count * sandbox_cpu
    except Exception:
        pass  # can't reach daytona — proceed with 0 used

    available = DAYTONA_CPU_LIMIT - used_cpus
    required = int(needed * buffer)

    if required > available:
        print(f"Preflight FAILED: need {required} CPUs "
              f"({max_workers} workers x {sandbox_cpu} CPU x {buffer} buffer) "
              f"but only {available} available "
              f"({DAYTONA_CPU_LIMIT} limit - {used_cpus} in use)")
        print(f"  Reduce --max-workers to {int(available / buffer / sandbox_cpu)} or fewer")
        sys.exit(1)

    print(f"Preflight OK: {required} CPUs needed, {available} available "
          f"({used_cpus} in use, {DAYTONA_CPU_LIMIT} limit)")


def preflight_docker():
    """Check that the local Docker daemon is reachable."""
    subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    print("Preflight OK: Docker daemon reachable")


def _writes_runs_layout(output_layout: str) -> bool:
    return output_layout in {"runs-v1", "both"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Run SWE-bench evaluation with Fabro"
    )
    parser.add_argument(
        "--model", default="claude-haiku-4-5", help="LLM model to use",
    )
    parser.add_argument(
        "--provider", default="anthropic", help="LLM provider",
    )
    parser.add_argument(
        "--max-workers", type=int, default=75,
        help="Max concurrent sandboxes (default 75)",
    )
    parser.add_argument(
        "--sandbox-provider",
        choices=["daytona", "docker"],
        default="daytona",
        help="Sandbox provider to use (default: daytona)",
    )
    parser.add_argument(
        "--fabro-bin",
        default="fabro",
        help="Fabro CLI binary to execute (default: fabro)",
    )
    parser.add_argument(
        "--instance-ids", nargs="+", help="Run only these instance IDs",
    )
    parser.add_argument(
        "--timeout", type=int, default=1200,
        help="Timeout per instance in seconds",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=EVAL_DIR / "results" / "default",
        help="Output directory for results",
    )
    parser.add_argument(
        "--output-layout",
        choices=["swebench-compat", "runs-v1", "both"],
        default="swebench-compat",
        help=(
            "Artifact layout to write. swebench-compat preserves the current "
            "configs/<instance_id> tree; runs-v1 additionally writes "
            "runs/<task_id>--001; both writes both layouts."
        ),
    )
    parser.add_argument(
        "--workflow-profile",
        choices=[SIMPLE_PROFILE, STRUCTURED_PROFILE],
        default=SIMPLE_PROFILE,
        help=(
            "Workflow profile to run. simple preserves setup->solve->extract_patch; "
            "structured runs research->implement->verify with one fixup loop."
        ),
    )
    parser.add_argument(
        "--verify-mode",
        choices=[VERIFY_NONE, VERIFY_DIFF_CHECK],
        default=None,
        help=(
            "Verification mode for the generated workflow. Defaults to none for "
            "simple and diff-check for structured."
        ),
    )
    args = parser.parse_args()
    args.verify_mode = default_verify_mode(args.workflow_profile, args.verify_mode)

    fabro_path = Path(args.fabro_bin).expanduser()
    if fabro_path.is_absolute() or len(fabro_path.parts) > 1:
        args.fabro_bin = str(fabro_path.resolve())
    else:
        args.fabro_bin = shutil.which(args.fabro_bin) or args.fabro_bin

    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(args.output_dir)

    # --- Preflight --------------------------------------------------------
    if args.sandbox_provider == "daytona":
        preflight_daytona(args.max_workers, sandbox_cpu=4)
    else:
        preflight_docker()

    log.info("=" * 64)
    log.info("SWE-bench Evaluation")
    log.info("=" * 64)
    log.info(f"  Model:       {args.model}")
    log.info(f"  Provider:    {args.provider}")
    log.info(f"  Workers:     {args.max_workers}")
    log.info(f"  Sandbox:     {args.sandbox_provider}")
    log.info(f"  Fabro bin:   {args.fabro_bin}")
    log.info(f"  Timeout:     {args.timeout}s")
    log.info(f"  Layout:      {args.output_layout}")
    log.info(f"  Workflow:    {args.workflow_profile}")
    log.info(f"  Verify:      {args.verify_mode}")
    log.info(f"  Output:      {args.output_dir}")
    log.info("")

    # --- Load instances ---------------------------------------------------
    log.info("Loading SWE-bench Lite instances...")
    instances = load_instances(args.instance_ids)
    log.info(f"  {len(instances)} instances loaded")

    # --- Resume: skip already-completed instances -------------------------
    completed_ids = load_completed_ids(args.output_dir)
    if completed_ids:
        instances = [i for i in instances if i["instance_id"] not in completed_ids]
        log.info(f"  {len(completed_ids)} already completed, {len(instances)} remaining")
    log.info("")

    # --- Run instances ----------------------------------------------------
    predictions_file = args.output_dir / "predictions.jsonl"
    results_file = args.output_dir / "results.jsonl"
    exports_dir = args.output_dir / "exports" / "swebench"
    export_predictions_file = exports_dir / "predictions.jsonl"
    export_results_file = exports_dir / "results.jsonl"
    if _writes_runs_layout(args.output_layout):
        exports_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_or_init_manifest(args.output_dir)

    # Counters (thread-safe via lock)
    lock = threading.Lock()
    counters = {
        "completed": 0,
        "no_patch": 0,
        "verify_failed": 0,
        "failed": 0,
        "timeout": 0,
        "error": 0,
    }
    done_count = 0
    total = len(instances)
    wall_start = time.time()

    log.info(f"Running {total} instances (max {args.max_workers} concurrent)...")
    log.info("-" * 64)

    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(
                run_instance, inst, args.model, args.provider,
                args.output_dir, args.timeout, args.sandbox_provider,
                args.fabro_bin, args.output_layout, args.workflow_profile,
                args.verify_mode,
            ): inst
            for inst in instances
        }

        with open(predictions_file, "a") as pf, open(results_file, "a") as rf:
            export_pf = (
                open(export_predictions_file, "a")
                if _writes_runs_layout(args.output_layout)
                else None
            )
            export_rf = (
                open(export_results_file, "a")
                if _writes_runs_layout(args.output_layout)
                else None
            )
            try:
                for future in as_completed(futures):
                    result = future.result()
                    iid = result["instance_id"]
                    status = result["status"]
                    dur = result["duration_s"]
                    has_patch = bool(result["model_patch"].strip())

                    with lock:
                        counters[status] = counters.get(status, 0) + 1
                        done_count += 1
                        n = done_count

                        # Write prediction
                        prediction_record = build_prediction_record(result)
                        prediction_line = json.dumps(prediction_record) + "\n"
                        pf.write(prediction_line)
                        pf.flush()
                        if export_pf:
                            export_pf.write(prediction_line)
                            export_pf.flush()

                        # Write detailed result
                        result_line = json.dumps(result) + "\n"
                        rf.write(result_line)
                        rf.flush()
                        if export_rf:
                            export_rf.write(result_line)
                            export_rf.flush()

                        update_manifest_for_attempt(
                            manifest,
                            task_id=iid,
                            attempt_id=DEFAULT_ATTEMPT_ID,
                            output_dir=args.output_dir,
                            config_dir=args.output_dir / "configs" / iid,
                        )
                        if _writes_runs_layout(args.output_layout):
                            update_manifest_for_run(
                                manifest,
                                task_id=iid,
                                run_id=run_id_for_task(iid, DEFAULT_ATTEMPT_ID),
                                attempt_id=DEFAULT_ATTEMPT_ID,
                                output_dir=args.output_dir,
                            )
                        write_manifest(args.output_dir, manifest)

                    # Log every result
                    patch_info = (
                        f"patch={len(result['model_patch'])}b"
                        if has_patch
                        else "no patch"
                    )
                    err_info = f"  err={result['error'][:80]}" if result["error"] else ""
                    elapsed = round(time.time() - wall_start)
                    log.info(
                        f"[{n:3d}/{total}]  {status:<10s}  {dur:6.0f}s  "
                        f"{patch_info:<14s}  {iid}{err_info}"
                    )

                    # Print running totals every 10 completions
                    if n % 10 == 0 or n == total:
                        log.info(
                            f"  --- progress: {n}/{total}  "
                            f"completed={counters.get('completed',0)}  "
                            f"no_patch={counters.get('no_patch',0)}  "
                            f"verify_failed={counters.get('verify_failed',0)}  "
                            f"failed={counters.get('failed',0)}  "
                            f"timeout={counters.get('timeout',0)}  "
                            f"error={counters.get('error',0)}  "
                            f"elapsed={elapsed}s ---"
                        )
            finally:
                if export_pf:
                    export_pf.close()
                if export_rf:
                    export_rf.close()

    wall_duration = round(time.time() - wall_start, 1)

    # --- Final summary (recompute from full results file) -----------------
    all_counters = {
        "completed": 0,
        "no_patch": 0,
        "verify_failed": 0,
        "failed": 0,
        "timeout": 0,
        "error": 0,
    }
    all_total = 0
    with open(results_file) as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                all_counters[r["status"]] = all_counters.get(r["status"], 0) + 1
                all_total += 1

    summary = {
        "model": args.model,
        "provider": args.provider,
        "workflow_profile": args.workflow_profile,
        "verify_mode": args.verify_mode,
        "total": all_total,
        **all_counters,
        "total_duration_s": wall_duration,
    }
    summary_file = args.output_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, indent=2))
    if _writes_runs_layout(args.output_layout):
        exports_dir.mkdir(parents=True, exist_ok=True)
        (exports_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    skipped = len(completed_ids)
    log.info("")
    log.info("=" * 64)
    log.info("FINAL RESULTS")
    log.info("=" * 64)
    if skipped:
        log.info(f"  Skipped:     {skipped} (already completed)")
        log.info(f"  This run:    {total}")
    log.info(f"  Total:       {all_total}")
    log.info(f"  Completed:   {all_counters.get('completed', 0)}")
    log.info(f"  No patch:    {all_counters.get('no_patch', 0)}")
    log.info(f"  Verify fail: {all_counters.get('verify_failed', 0)}")
    log.info(f"  Failed:      {all_counters.get('failed', 0)}")
    log.info(f"  Timeout:     {all_counters.get('timeout', 0)}")
    log.info(f"  Error:       {all_counters.get('error', 0)}")
    log.info(f"  Wall time:   {wall_duration}s")
    log.info(f"  Predictions: {predictions_file}")
    log.info(f"  Results:     {results_file}")
    log.info(f"  Summary:     {summary_file}")
    log.info(f"  Full log:    {args.output_dir / 'eval.log'}")
    log.info("=" * 64)


if __name__ == "__main__":
    main()
