from __future__ import annotations

import json
from pathlib import Path

from ..task_schema import MiniSweCase
from .cases import case_behavior, public_test_text_for_case, task_contract_for_case, tests_added_for_case
from .evidence import (
    adversarial_review_for_case,
    moderator_filter_for_case,
    review_materialization_for_case,
)


WORKFLOW_SLICE_REQUIRED_ARTIFACTS = (
    "patch.diff",
    "audit.json",
    "validation_contract.json",
    "commands_run.json",
    "adversarial_review.json",
    "moderator_filter.json",
    "review_materialization.json",
)


def missing_workflow_slice_artifacts(artifacts_dir: Path) -> list[str]:
    return [
        name for name in WORKFLOW_SLICE_REQUIRED_ARTIFACTS if not (artifacts_dir / name).exists()
    ]


def write_workflow_slice_trajectory(
    *,
    trajectory_path: Path,
    case: MiniSweCase,
    fabro_run_id: str | None,
    workflow_path: Path,
    artifacts_dir: Path,
) -> None:
    events = [
        {
            "schema_version": 1,
            "event": "mini_swe_workflow_slice",
            "case_id": case.case_id,
            "fabro_run_id": fabro_run_id,
            "workflow_path": workflow_path.as_posix(),
        },
        {
            "schema_version": 1,
            "event": "stage_artifacts_materialized",
            "case_id": case.case_id,
            "artifacts_dir": artifacts_dir.as_posix(),
            "artifacts": list(WORKFLOW_SLICE_REQUIRED_ARTIFACTS),
        },
    ]
    trajectory_path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events)
    )


def workflow_slice_solve_script(case: MiniSweCase, repo_dir: Path, artifacts_dir: Path) -> str:
    behavior = case_behavior(case)
    test_text = public_test_text_for_case(case) if behavior.change_test else None
    tests_added_json = json.dumps(tests_added_for_case(case))
    task_contract_json = json.dumps(task_contract_for_case(case))
    return (
        "python3 - <<'PY'\n"
        "import json\n"
        "import os\n"
        "import subprocess\n"
        "from pathlib import Path\n"
        f"repo = Path({json.dumps(str(repo_dir))})\n"
        f"artifact_dir = Path({json.dumps(str(artifacts_dir))})\n"
        "artifact_dir.mkdir(parents=True, exist_ok=True)\n"
        f"change_source = {behavior.change_source!r}\n"
        "if change_source:\n"
        "    (repo / 'src' / 'greeting.py').write_text('def greeting(name):\\n    return f\"hello, {name}\"\\n')\n"
        f"test_text = {test_text!r}\n"
        "if test_text is not None:\n"
        "    (repo / 'tests' / 'test_greeting.py').write_text(test_text)\n"
        "env = dict(os.environ)\n"
        "env['PYTHONDONTWRITEBYTECODE'] = '1'\n"
        "proc = subprocess.run(\n"
        "    ['python3', '-m', 'unittest', 'discover', '-s', 'tests'],\n"
        "    cwd=repo,\n"
        "    env=env,\n"
        "    stdout=subprocess.PIPE,\n"
        "    stderr=subprocess.PIPE,\n"
        "    text=True,\n"
        "    check=False,\n"
        ")\n"
        "subprocess.run(['git', 'add', '-N', '.'], cwd=repo, check=True)\n"
        "patch = subprocess.check_output(['git', 'diff'], cwd=repo, text=True)\n"
        "changed_files = subprocess.check_output(['git', 'diff', '--name-only'], cwd=repo, text=True).splitlines()\n"
        "test_files = [path for path in changed_files if path.startswith('tests/') or '/tests/' in path or Path(path).name.startswith('test_')]\n"
        "command = {\n"
        "    'argv_or_shell': 'python3 -m unittest discover -s tests',\n"
        "    'command': 'python3 -m unittest discover -s tests',\n"
        "    'cwd': '.',\n"
        "    'exit_code': proc.returncode,\n"
        "    'status': 'passed' if proc.returncode == 0 else 'failed',\n"
        "    'is_test_command': True,\n"
        "    'allowlist_class': 'test',\n"
        "    'stdout_tail': proc.stdout[-2000:],\n"
        "    'stderr_tail': proc.stderr[-2000:],\n"
        "    'repo_state': None,\n"
        "}\n"
        f"command_id = {behavior.public_test_command_id!r}\n"
        "if command_id is not None:\n"
        "    command['id'] = command_id\n"
        "audit = {\n"
        "    'schema_version': 1,\n"
        "    'mode': 'mini-swe-workflow-slice',\n"
        "    'patch_nonempty': bool(patch.strip()),\n"
        "    'changed_files': changed_files,\n"
        "    'test_files_changed': test_files,\n"
        "    'sandbox_provider': 'local',\n"
        "}\n"
        f"tests_added = {tests_added_json}\n"
        f"task_contract = json.loads({json.dumps(task_contract_json)})\n"
        f"no_test_justification = {behavior.no_test_justification!r}\n"
        "validation_contract = {\n"
        "    'schema_version': 1,\n"
        "    'mode': 'mini-swe-workflow-slice',\n"
        "    'tests_added': tests_added,\n"
        "    'commands_run': [command],\n"
        "}\n"
        "validation_contract.update(task_contract)\n"
        "if no_test_justification:\n"
        "    validation_contract['no_test_justification'] = no_test_justification\n"
        "(artifact_dir / 'patch.diff').write_text(patch)\n"
        "(artifact_dir / 'audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\\n')\n"
        "(artifact_dir / 'validation_contract.json').write_text(json.dumps(validation_contract, indent=2, sort_keys=True) + '\\n')\n"
        "(artifact_dir / 'commands_run.json').write_text(json.dumps([command], indent=2, sort_keys=True) + '\\n')\n"
        "print('mini-swe workflow-slice: solved generated issue')\n"
        "PY"
    )


def workflow_slice_review_script(case: MiniSweCase, artifacts_dir: Path) -> str:
    artifacts = {
        "adversarial_review.json": adversarial_review_for_case(case),
        "moderator_filter.json": moderator_filter_for_case(case),
        "review_materialization.json": review_materialization_for_case(case),
    }
    return (
        "python3 - <<'PY'\n"
        "import json\n"
        "from pathlib import Path\n"
        f"artifact_dir = Path({json.dumps(str(artifacts_dir))})\n"
        "artifact_dir.mkdir(parents=True, exist_ok=True)\n"
        f"artifacts = json.loads({json.dumps(json.dumps(artifacts, sort_keys=True))})\n"
        "for name, payload in artifacts.items():\n"
        "    (artifact_dir / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + '\\n')\n"
        "print('mini-swe workflow-slice: materialized review artifacts')\n"
        "PY"
    )
