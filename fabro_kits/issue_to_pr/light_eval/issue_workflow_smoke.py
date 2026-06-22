"""Issue-to-PR workflow artifact smoke eval."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..artifacts import (
    build_eval_summary, load_or_init_manifest, run_id_for_task, update_manifest_for_run,
    write_eval_root_outputs, write_run_bundle,
)
from ..evidence_gate import evaluate_evidence_gate
from ..review_accountability_gate import evaluate_review_accountability
from ..workflow_generator import dot_escape
from .bundles import prepare_issue_workflow_smoke_config_dir, synthetic_instance_for_task, synthetic_source_for_task
from .expectations import check_expected, issue_workflow_smoke_expected
from .process import read_json
from .workflow_common import (
    extract_workflow_smoke_run_id,
    run_fabro_command,
    workflow_smoke_env,
    write_workflow_smoke_config,
)


def run_issue_workflow_smoke(
    *,
    output_dir: Path | None = None,
    fabro_bin: Path = Path("target/debug/fabro"),
) -> dict[str, Any]:
    if output_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return _run_issue_workflow_smoke_to_dir(Path(tmp), fabro_bin=fabro_bin)
    output_dir.mkdir(parents=True, exist_ok=True)
    return _run_issue_workflow_smoke_to_dir(output_dir.resolve(), fabro_bin=fabro_bin)

def _run_issue_workflow_smoke_to_dir(output_dir: Path, *, fabro_bin: Path) -> dict[str, Any]:
    task_id = "issue-workflow-smoke"
    smoke_dir = output_dir / task_id
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
        return write_issue_workflow_smoke_summary(output_dir, smoke_dir, failures=failures)

    workflow_path = smoke_dir / "workflow.fabro"
    storage_dir = smoke_dir / "storage"
    config_path = smoke_dir / "settings.toml"
    artifacts_dir = smoke_dir / "stage-artifacts"
    write_issue_workflow_smoke_files(
        workflow_path=workflow_path,
        storage_dir=storage_dir,
        config_path=config_path,
        artifacts_dir=artifacts_dir,
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
    if failures:
        return write_issue_workflow_smoke_summary(output_dir, smoke_dir, failures=failures)

    try:
        case_result = build_issue_workflow_smoke_case(
            output_dir=output_dir,
            smoke_dir=smoke_dir,
            workflow_path=workflow_path,
            artifacts_dir=artifacts_dir,
            run_id=run_id,
            run_transcript=run_transcript,
        )
    except (OSError, json.JSONDecodeError, KeyError, RuntimeError) as exc:
        failures.append({"kind": "issue_workflow_artifacts_malformed", "reason": str(exc)})
        return write_issue_workflow_smoke_summary(output_dir, smoke_dir, failures=failures)

    failures.extend(case_result["failures"])
    result = case_result["result"]
    manifest = load_or_init_manifest(output_dir)
    update_manifest_for_run(
        manifest,
        task_id=case_result["task_id"],
        run_id=case_result["bundle_run_id"],
        attempt_id="001",
        output_dir=output_dir,
    )
    write_eval_root_outputs(output_dir, results=[result], manifest=manifest)
    return write_issue_workflow_smoke_summary(output_dir, smoke_dir, failures=failures)

def build_issue_workflow_smoke_case(
    *,
    output_dir: Path,
    smoke_dir: Path,
    workflow_path: Path,
    artifacts_dir: Path,
    run_id: str,
    run_transcript: str,
) -> dict[str, Any]:
    task_id = "issue-workflow-smoke"
    patch = (artifacts_dir / "patch.diff").read_text()
    audit = read_json(artifacts_dir / "audit.json")
    validation_contract = read_json(artifacts_dir / "validation_contract.json")
    adversarial = read_json(artifacts_dir / "adversarial_review.json")
    moderator = read_json(artifacts_dir / "moderator_filter.json")
    materialization = read_json(artifacts_dir / "review_materialization.json")
    test_gate = evaluate_evidence_gate(audit=audit, contract=validation_contract)
    gate = evaluate_review_accountability(
        adversarial=adversarial,
        moderator=moderator,
        test_gate=test_gate,
        materialization=materialization,
        patch_diff=patch,
    )
    result_status = "completed" if gate["route_decision"] == "export" else "failed"
    source_kind = "synthetic_workflow_artifact_smoke"
    result = {
        "instance_id": task_id,
        "model_name_or_path": "light-eval-issue-workflow-smoke",
        "model_patch": patch,
        "status": result_status,
        "error": gate.get("failure_reason") if result_status != "completed" else None,
        "duration_s": 0,
        "fabro_run_id": run_id,
        "fabro_dump_dir": str(smoke_dir),
        "trajectory_path": None,
        "source": synthetic_source_for_task(task_id, source_kind=source_kind),
        "audit": audit,
        "verify": {
            "schema_version": 1,
            "status": "completed",
            "mode": "synthetic-workflow-artifact-smoke",
            "fabro_run_id": run_id,
        },
        "test_evidence_gate": test_gate,
        "adversarial_review": adversarial,
        "moderator_filter": moderator,
        "review_materialization": materialization,
        "review_accountability_gate": gate,
    }
    config_dir = prepare_issue_workflow_smoke_config_dir(
        output_dir,
        task_id,
        workflow_path=workflow_path,
        validation_contract=validation_contract,
        run_transcript=run_transcript,
    )
    write_run_bundle(
        instance=synthetic_instance_for_task(task_id, source_kind=source_kind),
        result=result,
        output_dir=output_dir,
        config_dir=config_dir,
        sandbox_provider="local",
    )
    bundle_run_id = run_id_for_task(task_id)
    failures = check_expected(
        issue_workflow_smoke_expected(),
        output_dir=output_dir,
        run_id=bundle_run_id,
        result=result,
        gate=gate,
        patch=patch,
    )
    smoke_record = {
        "schema_version": 1,
        "mode": "synthetic-workflow-artifact-smoke",
        "status": "passed" if not failures else "failed",
        "fabro_run_id": run_id,
        "bundle_run_id": bundle_run_id,
        "workflow_path": str(workflow_path),
        "artifacts_dir": str(artifacts_dir),
        "run_transcript": run_transcript,
    }
    (smoke_dir / "issue_workflow_smoke.json").write_text(
        json.dumps(smoke_record, indent=2, sort_keys=True) + "\n"
    )
    return {
        "task_id": task_id,
        "bundle_run_id": bundle_run_id,
        "result": result,
        "failures": failures,
    }

def write_issue_workflow_smoke_summary(
    output_dir: Path,
    smoke_dir: Path,
    *,
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = build_eval_summary(
        1,
        failures,
        issue_workflow_smoke=str(
            (smoke_dir / "issue_workflow_smoke.json").relative_to(output_dir)
        )
        if (smoke_dir / "issue_workflow_smoke.json").exists()
        else "",
    )
    write_eval_root_outputs(output_dir, summary=summary)
    return summary

def write_issue_workflow_smoke_files(
    *,
    workflow_path: Path,
    storage_dir: Path,
    config_path: Path,
    artifacts_dir: Path,
) -> None:
    write_workflow_smoke_config(storage_dir=storage_dir, config_path=config_path)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(
        "digraph IssueToPrArtifactSmoke {\n"
        "  graph [goal=\"synthetic issue-to-PR artifact workflow smoke\"]\n"
        "  start [shape=Mdiamond, label=\"Start\"]\n"
        "  exit [shape=Msquare, label=\"Exit\"]\n"
        "  derive_diff_facts [label=\"Derive Diff Facts\", shape=parallelogram, "
        f"script=\"{dot_escape(issue_workflow_diff_script(artifacts_dir))}\"]\n"
        "  materialize_review [label=\"Materialize Review\", shape=parallelogram, "
        f"script=\"{dot_escape(issue_workflow_review_script(artifacts_dir))}\"]\n"
        "  start -> derive_diff_facts -> materialize_review -> exit\n"
        "}\n"
    )

def issue_workflow_diff_script(artifacts_dir: Path) -> str:
    return (
        "python3 - <<'PY'\n"
        "import json\n"
        "from pathlib import Path\n"
        f"artifact_dir = Path({json.dumps(str(artifacts_dir))})\n"
        "artifact_dir.mkdir(parents=True, exist_ok=True)\n"
        "patch = '''diff --git a/src/greeting.py b/src/greeting.py\n"
        "--- a/src/greeting.py\n"
        "+++ b/src/greeting.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def greeting(name):\n"
        "-    return f\"hello {name}\"\n"
        "+    return f\"hello, {name}\"\n"
        "'''\n"
        "audit = {\n"
        "    'schema_version': 1,\n"
        "    'mode': 'synthetic-workflow-artifact-smoke',\n"
        "    'patch_nonempty': True,\n"
        "    'changed_files': ['src/greeting.py'],\n"
        "    'test_files_changed': [],\n"
        "    'sandbox_provider': 'local',\n"
        "}\n"
        "validation_contract = {\n"
        "    'schema_version': 1,\n"
        "    'mode': 'synthetic-workflow-artifact-smoke',\n"
        "    'tests_added': [\n"
        "        {'path': 'tests/test_greeting.py', 'description': 'claimed coverage'}\n"
        "    ],\n"
        "    'commands_run': [\n"
        "        {'command': 'python3 -m unittest tests/test_greeting.py', 'status': 'passed'}\n"
        "    ],\n"
        "}\n"
        "(artifact_dir / 'patch.diff').write_text(patch)\n"
        "(artifact_dir / 'audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\\n')\n"
        "(artifact_dir / 'validation_contract.json').write_text(json.dumps(validation_contract, indent=2, sort_keys=True) + '\\n')\n"
        "print('issue-workflow-smoke: wrote diff facts')\n"
        "PY"
    )

def issue_workflow_review_script(artifacts_dir: Path) -> str:
    return (
        "python3 - <<'PY'\n"
        "import json\n"
        "from pathlib import Path\n"
        f"artifact_dir = Path({json.dumps(str(artifacts_dir))})\n"
        "artifact_dir.mkdir(parents=True, exist_ok=True)\n"
        "artifacts = {\n"
        "    'adversarial_review.json': {\n"
        "        'schema_version': 1,\n"
        "        'stage': 'adversarial_review',\n"
        "        'status': 'passed',\n"
        "        'checked_risks': [\n"
        "            {\n"
        "                'risk': 'source behavior changed without matching test-file evidence',\n"
        "                'evidence': ['src/greeting.py'],\n"
        "                'counterexample_check': 'src/greeting.py is the only changed source file in the synthetic patch',\n"
        "            }\n"
        "        ],\n"
        "        'rows': [],\n"
        "    },\n"
        "    'moderator_filter.json': {\n"
        "        'schema_version': 1,\n"
        "        'stage': 'moderator_filter',\n"
        "        'status': 'passed',\n"
        "        'dispositions': [],\n"
        "    },\n"
        "    'review_materialization.json': {\n"
        "        'schema_version': 1,\n"
        "        'stage': 'review_materialization',\n"
        "        'status': 'passed',\n"
        "        'errors': [],\n"
        "    },\n"
        "}\n"
        "for name, payload in artifacts.items():\n"
        "    (artifact_dir / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + '\\n')\n"
        "print('issue-workflow-smoke: wrote review artifacts')\n"
        "PY"
    )
