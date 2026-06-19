"""Fabro workflow execution smoke eval."""

from __future__ import annotations

import json
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..workflow_generator import dot_escape
from .workflow_common import (
    extract_workflow_smoke_run_id,
    run_fabro_command,
    workflow_smoke_env,
    write_workflow_smoke_config,
)


def run_workflow_smoke(
    *,
    output_dir: Path | None = None,
    fabro_bin: Path = Path("target/debug/fabro"),
) -> dict[str, Any]:
    if output_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return _run_workflow_smoke_to_dir(Path(tmp), fabro_bin=fabro_bin)
    output_dir.mkdir(parents=True, exist_ok=True)
    return _run_workflow_smoke_to_dir(output_dir.resolve(), fabro_bin=fabro_bin)

def _run_workflow_smoke_to_dir(output_dir: Path, *, fabro_bin: Path) -> dict[str, Any]:
    smoke_dir = output_dir / "workflow-smoke"
    if smoke_dir.exists():
        shutil.rmtree(smoke_dir)
    smoke_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    if not fabro_bin.exists():
        failures.append(
            {
                "kind": "fabro_binary_missing",
                "path": str(fabro_bin),
                "reason": "Build fabro-cli first or pass --fabro-bin.",
            }
        )
        return write_workflow_smoke_summary(output_dir, smoke_dir, failures=failures)

    workflow_path = smoke_dir / "workflow.fabro"
    storage_dir = smoke_dir / "storage"
    config_path = smoke_dir / "settings.toml"
    write_workflow_smoke_files(
        workflow_path=workflow_path,
        storage_dir=storage_dir,
        config_path=config_path,
    )
    env = workflow_smoke_env(config_path=config_path, storage_dir=storage_dir)
    run_proc = run_fabro_command(
        fabro_bin,
        [
            "--no-upgrade-check",
            "run",
            "--auto-approve",
            "--environment",
            "local",
            str(workflow_path),
        ],
        env=env,
    )
    (smoke_dir / "run.stdout").write_text(run_proc.stdout)
    (smoke_dir / "run.stderr").write_text(run_proc.stderr)
    run_transcript = run_proc.stdout + run_proc.stderr
    (smoke_dir / "run.transcript").write_text(run_transcript)
    run_id = extract_workflow_smoke_run_id(run_transcript)
    try:
        if run_proc.returncode != 0:
            failures.append(
                {
                    "kind": "workflow_run_failed",
                    "exit_code": run_proc.returncode,
                    "stderr": run_proc.stderr[-2000:],
                }
            )
        elif not run_id:
            failures.append({"kind": "workflow_run_id_missing"})
        elif "Status:    SUCCEEDED" not in run_transcript:
            failures.append(
                {
                    "kind": "workflow_run_not_succeeded",
                    "run_id": run_id,
                    "transcript": run_transcript[-2000:],
                }
            )
    finally:
        stop_proc = run_fabro_command(
            fabro_bin,
            ["--no-upgrade-check", "server", "stop", "--storage-dir", str(storage_dir)],
            env=env,
        )
        (smoke_dir / "stop.stdout").write_text(stop_proc.stdout)
        (smoke_dir / "stop.stderr").write_text(stop_proc.stderr)

    if run_id:
        (smoke_dir / "run_id.txt").write_text(run_id + "\n")
    probe_path = smoke_dir / "workflow-output.txt"
    if not probe_path.exists() or probe_path.read_text() != "workflow-ok":
        failures.append(
            {
                "kind": "workflow_script_side_effect_missing",
                "path": str(probe_path),
            }
        )
    result = {
        "schema_version": 1,
        "mode": "synthetic-workflow-smoke",
        "status": "passed" if not failures else "failed",
        "run_id": run_id or None,
        "run_transcript": run_transcript,
        "storage_dir": str(storage_dir),
        "workflow_path": str(workflow_path),
    }
    (smoke_dir / "workflow_smoke.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return write_workflow_smoke_summary(output_dir, smoke_dir, failures=failures)

def write_workflow_smoke_summary(
    output_dir: Path,
    smoke_dir: Path,
    *,
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = {
        "total": 1,
        "failed": len(failures),
        "false_exports": 0,
        "failures": failures,
        "workflow_smoke": str((smoke_dir / "workflow_smoke.json").relative_to(output_dir))
        if (smoke_dir / "workflow_smoke.json").exists()
        else "",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary

def write_workflow_smoke_files(
    *,
    workflow_path: Path,
    storage_dir: Path,
    config_path: Path,
) -> None:
    write_workflow_smoke_config(storage_dir=storage_dir, config_path=config_path)
    probe_path = workflow_path.parent / "workflow-output.txt"
    workflow_path.write_text(
        "digraph TinyIssueToPrSmoke {\n"
        "  graph [goal=\"synthetic issue-to-PR workflow smoke\"]\n"
        "  start [shape=Mdiamond, label=\"Start\"]\n"
        "  exit [shape=Msquare, label=\"Exit\"]\n"
        f"  write [shape=parallelogram, label=\"Write Artifact\", script=\"{dot_escape(workflow_probe_script(probe_path))}\"]\n"
        "  start -> write -> exit\n"
        "}\n"
    )

def workflow_probe_script(probe_path: Path) -> str:
    quoted_path = shlex.quote(str(probe_path))
    return f"printf workflow-ok > {quoted_path} && cat {quoted_path}"
