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

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from datasets import load_dataset
from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS

from fabro_kits.issue_to_pr.artifacts import (
    DEFAULT_ATTEMPT_ID,
    build_candidate_record,
    build_prediction_record,
    load_or_init_manifest,
    run_id_for_task,
    update_manifest_for_run,
    write_run_bundle,
    write_manifest,
)
from fabro_kits.issue_to_pr.run_attempt import (
    dump_run,
    find_audit_record,
    find_json_stage_record,
    find_patch,
    find_review_record,
    find_test_evidence_gate_record,
    find_verify_record,
    parse_run_ref,
    write_trajectory_from_events,
)
from fabro_kits.issue_to_pr.workflow_generator import (
    SIMPLE_PROFILE,
    STRUCTURED_GATED_PROFILE,
    STRUCTURED_MODERATED_PROFILE,
    STRUCTURED_PROFILE,
    VERIFY_DIFF_CHECK,
    VERIFY_NONE,
    default_verify_mode,
    escape_goal_for_template,
    generate_issue_to_pr_workflow,
    validate_generated_workflow,
)
from gen_dockerfile import generate_dockerfile, repo_version_key

EVAL_DIR = Path(__file__).parent.resolve()
STRUCTURED_WORKFLOW_PROFILES = {
    STRUCTURED_PROFILE,
    STRUCTURED_GATED_PROFILE,
    STRUCTURED_MODERATED_PROFILE,
}
GATED_WORKFLOW_PROFILES = {STRUCTURED_GATED_PROFILE, STRUCTURED_MODERATED_PROFILE}

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


def load_completed_results(output_dir: Path) -> list[dict]:
    """Load prior results that should be preserved when resuming."""
    results = []
    results_file = output_dir / "results.jsonl"
    if results_file.exists():
        with open(results_file) as f:
            for line in f:
                if line.strip():
                    try:
                        result = json.loads(line)
                        if result.get("instance_id") and result.get("status") in {"completed", "no_patch"}:
                            results.append(result)
                    except json.JSONDecodeError:
                        pass
    return results


def write_root_exports(
    output_dir: Path,
    results: list[dict],
    *,
    model: str,
    provider: str,
    workflow_profile: str,
    verify_mode: str,
    total_duration_s: float,
) -> dict:
    (output_dir / "predictions.jsonl").write_text(
        "".join(json.dumps(build_prediction_record(r)) + "\n" for r in results)
    )
    (output_dir / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results))

    counters = {key: 0 for key in ("completed", "no_patch", "verify_failed", "failed", "timeout", "error")}
    failed_with_patch = 0
    continuation_candidates = 0
    for result in results:
        counters[result["status"]] = counters.get(result["status"], 0) + 1
        candidate = result.get("candidate") if isinstance(result.get("candidate"), dict) else {}
        if candidate.get("reuse") == "continuation_candidate":
            continuation_candidates += 1
        if candidate.get("state") == "failed_with_patch":
            failed_with_patch += 1
    summary = {
        "model": model,
        "provider": provider,
        "workflow_profile": workflow_profile,
        "verify_mode": verify_mode,
        "total": len(results),
        **counters,
        "failed_with_patch": failed_with_patch,
        "continuation_candidates": continuation_candidates,
        "total_duration_s": total_duration_s,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def review_artifacts_reconciled(result: dict) -> bool:
    gate = result.get("review_accountability_gate")
    return (
        not isinstance(gate, dict)
        or not (gate.get("adversarial_row_count") or gate.get("moderator_disposition_count"))
        or (
            isinstance(result.get("adversarial_review"), dict)
            and isinstance(result.get("moderator_filter"), dict)
        )
    )


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
        "audit": None,
        "test_evidence_gate": None,
        "adversarial_review": None,
        "moderator_filter": None,
        "review_materialization": None,
        "review_accountability_gate": None,
        "artifacts": {},
    }

    start_time = time.time()

    try:
        goal_text = build_goal(instance)
        goal_file = config_dir / "goal.txt"
        goal_file.write_text(escape_goal_for_template(goal_text))

        fabro_content = generate_workflow_fabro(
            instance,
            workflow_profile=workflow_profile,
            verify_mode=verify_mode,
        )
        validate_generated_workflow(
            fabro_content,
            workflow_profile=workflow_profile,
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
        if workflow_profile in STRUCTURED_WORKFLOW_PROFILES:
            verify = find_verify_record(fabro_run_dir) if fabro_run_dir else None
            if not verify and dumped:
                verify = find_verify_record(dumped)
            result["verify"] = verify
            audit = find_audit_record(fabro_run_dir) if fabro_run_dir else None
            if not audit and dumped:
                audit = find_audit_record(dumped)
            result["audit"] = audit
            if workflow_profile in GATED_WORKFLOW_PROFILES:
                test_evidence_gate = (
                    find_test_evidence_gate_record(fabro_run_dir)
                    if fabro_run_dir
                    else None
                )
                if not test_evidence_gate and dumped:
                    test_evidence_gate = find_test_evidence_gate_record(dumped)
                result["test_evidence_gate"] = test_evidence_gate
            if workflow_profile == STRUCTURED_MODERATED_PROFILE:
                adversarial_review = (
                    find_json_stage_record(fabro_run_dir, "adversarial_review")
                    if fabro_run_dir
                    else None
                )
                if not adversarial_review and dumped:
                    adversarial_review = find_json_stage_record(
                        dumped,
                        "adversarial_review",
                    )
                result["adversarial_review"] = adversarial_review

                moderator_filter = (
                    find_json_stage_record(fabro_run_dir, "moderator_filter")
                    if fabro_run_dir
                    else None
                )
                if not moderator_filter and dumped:
                    moderator_filter = find_json_stage_record(
                        dumped,
                        "moderator_filter",
                    )
                result["moderator_filter"] = moderator_filter

                review_materialization = (
                    find_json_stage_record(
                        fabro_run_dir,
                        "materialize_review_artifacts",
                    )
                    if fabro_run_dir
                    else None
                )
                if not review_materialization and dumped:
                    review_materialization = find_json_stage_record(
                        dumped,
                        "materialize_review_artifacts",
                    )
                result["review_materialization"] = review_materialization
                if isinstance(review_materialization, dict):
                    materialized = review_materialization.get("artifacts")
                    if isinstance(materialized, dict):
                        if not isinstance(result.get("adversarial_review"), dict):
                            result["adversarial_review"] = materialized.get("adversarial_review")
                        if not isinstance(result.get("moderator_filter"), dict):
                            result["moderator_filter"] = materialized.get("moderator_filter")

                review_accountability_gate = (
                    find_json_stage_record(
                        fabro_run_dir,
                        "review_accountability_gate",
                    )
                    if fabro_run_dir
                    else None
                )
                if not review_accountability_gate and dumped:
                    review_accountability_gate = find_json_stage_record(
                        dumped,
                        "review_accountability_gate",
                    )
                result["review_accountability_gate"] = review_accountability_gate
            review = find_review_record(fabro_run_dir) if fabro_run_dir else None
            if not review and dumped:
                review = find_review_record(dumped)
            result["review"] = review

        # Extract patch from the fabro run dir
        patch = find_patch(fabro_run_dir) if fabro_run_dir else None
        if not patch and dumped:
            patch = find_patch(dumped)
        if patch and patch.strip():
            result["model_patch"] = patch
            if (
                workflow_profile in STRUCTURED_WORKFLOW_PROFILES
                and verify
                and verify.get("status") not in {"passed", "skipped"}
            ):
                result["status"] = "verify_failed"
                result["error"] = verify.get("failure_reason") or "Verify failed"
            elif (
                workflow_profile in GATED_WORKFLOW_PROFILES
                and result.get("test_evidence_gate")
                and result["test_evidence_gate"].get("status") != "passed"
            ):
                result["status"] = "failed"
                result["error"] = (
                    result["test_evidence_gate"].get("failure_reason")
                    or "Test evidence gate failed"
                )
            elif (
                workflow_profile == STRUCTURED_MODERATED_PROFILE
                and result.get("review_accountability_gate")
                and result["review_accountability_gate"].get("status") != "passed"
            ):
                result["status"] = "failed"
                result["error"] = (
                    result["review_accountability_gate"].get("failure_reason")
                    or "Review accountability gate blocked export"
                )
            elif proc.returncode == 0:
                result["status"] = "completed"
            if (
                workflow_profile == STRUCTURED_MODERATED_PROFILE
                and result["status"] == "completed"
                and not review_artifacts_reconciled(result)
            ):
                result["status"] = "failed"
                result["error"] = "Review artifacts missing from durable export"
        elif (
            workflow_profile in STRUCTURED_WORKFLOW_PROFILES
            and verify
            and verify.get("status") != "passed"
        ):
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
        run_id = run_id_for_task(instance_id, DEFAULT_ATTEMPT_ID)
        result["artifacts"] = write_run_bundle(
            instance=instance,
            result=result,
            output_dir=output_dir,
            config_dir=config_dir,
            sandbox_provider=sandbox_provider,
            run_id=run_id,
            attempt_id=DEFAULT_ATTEMPT_ID,
        )
        result["candidate"] = build_candidate_record(
            result,
            output_dir / "runs" / run_id / "output" / "patch.diff",
            output_dir / "runs" / run_id,
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
        "--workflow-profile",
        choices=[
            SIMPLE_PROFILE,
            STRUCTURED_PROFILE,
            STRUCTURED_GATED_PROFILE,
            STRUCTURED_MODERATED_PROFILE,
        ],
        default=SIMPLE_PROFILE,
        help=(
            "Workflow profile to run. simple preserves setup->solve->extract_patch; "
            "structured runs research->implement->verify with one fixup loop; "
            "structured-gated adds a deterministic test-evidence gate before review; "
            "structured-moderated adds adversarial/moderated review and a readiness audit."
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
    log.info(f"  Workflow:    {args.workflow_profile}")
    log.info(f"  Verify:      {args.verify_mode}")
    log.info(f"  Output:      {args.output_dir}")
    log.info("")

    # --- Load instances ---------------------------------------------------
    log.info("Loading SWE-bench Lite instances...")
    instances = load_instances(args.instance_ids)
    log.info(f"  {len(instances)} instances loaded")

    # --- Resume: skip already-completed instances -------------------------
    results = load_completed_results(args.output_dir)
    completed_ids = {result["instance_id"] for result in results}
    if completed_ids:
        instances = [i for i in instances if i["instance_id"] not in completed_ids]
        log.info(f"  {len(completed_ids)} already completed, {len(instances)} remaining")
    log.info("")

    # --- Run instances ----------------------------------------------------
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
                args.fabro_bin, args.workflow_profile,
                args.verify_mode,
            ): inst
            for inst in instances
        }

        for future in as_completed(futures):
            result = future.result()
            iid = result["instance_id"]
            status = result["status"]
            dur = result["duration_s"]
            has_patch = bool(result["model_patch"].strip())

            with lock:
                results.append(result)
                counters[status] = counters.get(status, 0) + 1
                done_count += 1
                n = done_count

                update_manifest_for_run(
                    manifest,
                    task_id=iid,
                    run_id=run_id_for_task(iid, DEFAULT_ATTEMPT_ID),
                    attempt_id=DEFAULT_ATTEMPT_ID,
                    output_dir=args.output_dir,
                )
                write_manifest(args.output_dir, manifest)

            patch_info = f"patch={len(result['model_patch'])}b" if has_patch else "no patch"
            err_info = f"  err={result['error'][:80]}" if result["error"] else ""
            elapsed = round(time.time() - wall_start)
            log.info(
                f"[{n:3d}/{total}]  {status:<10s}  {dur:6.0f}s  "
                f"{patch_info:<14s}  {iid}{err_info}"
            )

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

    wall_duration = round(time.time() - wall_start, 1)

    summary = write_root_exports(
        args.output_dir,
        results,
        model=args.model,
        provider=args.provider,
        workflow_profile=args.workflow_profile,
        verify_mode=args.verify_mode,
        total_duration_s=wall_duration,
    )
    predictions_file = args.output_dir / "predictions.jsonl"
    results_file = args.output_dir / "results.jsonl"
    summary_file = args.output_dir / "summary.json"

    skipped = len(completed_ids)
    log.info("")
    log.info("=" * 64)
    log.info("FINAL RESULTS")
    log.info("=" * 64)
    if skipped:
        log.info(f"  Skipped:     {skipped} (already completed)")
        log.info(f"  This run:    {total}")
    log.info(f"  Total:       {summary['total']}")
    log.info(f"  Completed:   {summary.get('completed', 0)}")
    log.info(f"  No patch:    {summary.get('no_patch', 0)}")
    log.info(f"  Verify fail: {summary.get('verify_failed', 0)}")
    log.info(f"  Failed:      {summary.get('failed', 0)}")
    log.info(f"  Failed+patch:{summary['failed_with_patch']:4d}")
    log.info(f"  Continue:    {summary['continuation_candidates']:4d}")
    log.info(f"  Timeout:     {summary.get('timeout', 0)}")
    log.info(f"  Error:       {summary.get('error', 0)}")
    log.info(f"  Wall time:   {wall_duration}s")
    log.info(f"  Predictions: {predictions_file}")
    log.info(f"  Results:     {results_file}")
    log.info(f"  Summary:     {summary_file}")
    log.info(f"  Full log:    {args.output_dir / 'eval.log'}")
    log.info("=" * 64)


if __name__ == "__main__":
    main()
