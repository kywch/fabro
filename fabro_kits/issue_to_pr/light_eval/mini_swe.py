"""Mini-SWE lightweight eval orchestration."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..artifacts import (
    build_prediction_record,
    load_or_init_manifest,
    run_id_for_task,
    update_manifest_for_run,
    write_manifest,
    write_run_bundle,
)
from ..evidence_gate import evaluate_evidence_gate
from ..review_accountability_gate import evaluate_review_accountability
from ..workflow_generator import dot_escape
from .bundles import prepare_config_dir
from .grader import MiniSweGrade, grade_mini_swe_attempt
from .process import git_capture, git_run, is_test_path
from .task_schema import AttemptResult, MiniSweCase, mini_swe_source
from .workflow_common import (
    extract_workflow_smoke_run_id,
    run_fabro_command,
    workflow_smoke_env,
    write_workflow_smoke_config,
)


KNOWN_MINI_SWE_CASES = (
    MiniSweCase(
        case_id="good-source-plus-test",
        family="positive",
        suite="dev",
        issue_text=(
            "Fix `greeting(name)` so it returns `hello, <name>` and add a "
            "regression test for the comma."
        ),
        expected_files=("src/greeting.py",),
        allowed_test_files=("tests/test_greeting.py",),
        requires_test_change=True,
        expected_decision_hint="export",
    ),
    MiniSweCase(
        case_id="runtime-proof-honesty",
        family="evidence",
        suite="dev",
        issue_text=(
            "Fix `greeting(name)` so it returns `hello, <name>`, but do not "
            "treat runtime proof as valid unless validation cites an observed "
            "command ID."
        ),
        expected_files=("src/greeting.py",),
        allowed_test_files=("tests/test_greeting.py",),
        requires_test_change=True,
        expected_decision_hint="fixup",
    ),
    MiniSweCase(
        case_id="good-source-existing-test",
        family="positive",
        suite="dev",
        issue_text=(
            "Fix `greeting(name)` so it returns `hello, <name>`. The existing "
            "test already covers the expected behavior, so a source-only patch "
            "is acceptable."
        ),
        expected_files=("src/greeting.py",),
        allowed_test_files=(),
        requires_test_change=False,
        expected_decision_hint="export",
    ),
    MiniSweCase(
        case_id="overblocking-good-patch-with-minor-risk",
        family="review_moderation",
        suite="dev",
        issue_text=(
            "Fix `greeting(name)` so it returns `hello, <name>` and add a "
            "regression test. A review may raise a minor naming concern, but "
            "it should not block export when the concern is accounted for."
        ),
        expected_files=("src/greeting.py",),
        allowed_test_files=("tests/test_greeting.py",),
        requires_test_change=True,
        expected_decision_hint="export",
    ),
)


def run_mini_swe(
    case: str = "all",
    *,
    output_dir: Path | None = None,
    attempt: str = "scripted",
    substrate: str = "local",
    fabro_bin: Path = Path("target/debug/fabro"),
) -> dict[str, Any]:
    """Run mini-SWE lightweight eval cases."""
    cases = list_mini_swe_cases(case)
    if attempt not in {"scripted", "workflow-slice"}:
        raise SystemExit(f"mini-swe attempt not implemented yet: {attempt}")
    if substrate != "local":
        raise SystemExit(f"mini-swe substrate not implemented yet: {substrate}")
    if output_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return _run_mini_swe_to_dir(
                cases,
                Path(tmp),
                attempt=attempt,
                substrate=substrate,
                fabro_bin=fabro_bin,
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    return _run_mini_swe_to_dir(
        cases,
        output_dir,
        attempt=attempt,
        substrate=substrate,
        fabro_bin=fabro_bin,
    )


def list_mini_swe_cases(case: str) -> list[MiniSweCase]:
    """Return selected mini-SWE case definitions."""
    if case == "all":
        return list(KNOWN_MINI_SWE_CASES)
    for known in KNOWN_MINI_SWE_CASES:
        if known.case_id == case:
            return [known]
    raise SystemExit(f"unknown mini-swe case: {case}")


def _run_mini_swe_to_dir(
    cases: list[MiniSweCase],
    output_dir: Path,
    *,
    attempt: str,
    substrate: str,
    fabro_bin: Path,
) -> dict[str, Any]:
    results = []
    failures = []
    manifest = load_or_init_manifest(output_dir)

    for case in cases:
        case_result = run_mini_swe_case(
            case,
            output_dir=output_dir,
            attempt=attempt,
            substrate=substrate,
            fabro_bin=fabro_bin,
        )
        results.append(case_result["result"])
        failures.extend(case_result["failures"])
        update_manifest_for_run(
            manifest,
            task_id=case_result["task_id"],
            run_id=case_result["run_id"],
            attempt_id="001",
            output_dir=output_dir,
        )

    write_manifest(output_dir, manifest)
    (output_dir / "results.jsonl").write_text(
        "".join(json.dumps(result, sort_keys=True) + "\n" for result in results)
    )
    (output_dir / "predictions.jsonl").write_text(
        "".join(
            json.dumps(build_prediction_record(result), sort_keys=True) + "\n"
            for result in results
        )
    )

    summary = _mini_swe_summary(results, failures)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def run_mini_swe_case(
    case: MiniSweCase,
    *,
    output_dir: Path,
    attempt: str,
    substrate: str,
    fabro_bin: Path,
) -> dict[str, Any]:
    """Run one mini-SWE case."""
    if attempt not in {"scripted", "workflow-slice"}:
        raise SystemExit(f"mini-swe attempt not implemented yet: {attempt}")
    if substrate != "local":
        raise SystemExit(f"mini-swe substrate not implemented yet: {substrate}")

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        repo_dir = work_dir / "repo"
        repo_dir.mkdir()
        _create_repo_for_case(case, repo_dir)
        if attempt == "scripted":
            runner = ScriptedCalibrationRunner(substrate="local")
        else:
            runner = WorkflowSliceRunner(output_dir=output_dir, fabro_bin=fabro_bin)
        attempt_result = runner.run(case, repo_dir, work_dir)

        patch = attempt_result.patch_path.read_text() if attempt_result.patch_path else ""
        changed_files = git_capture(repo_dir, "diff", "--name-only").splitlines()
        test_files_changed = [path for path in changed_files if is_test_path(path)]

    artifact_paths = attempt_result.artifact_paths
    commands_run = _commands_run_for_case(case, attempt_result=attempt_result)
    if attempt_result.commands_run_path and attempt_result.commands_run_path.exists():
        commands_from_artifact = json.loads(attempt_result.commands_run_path.read_text())
        if isinstance(commands_from_artifact, list):
            commands_run = commands_from_artifact
    audit = _read_artifact_or_default(
        artifact_paths.get("audit"),
        {
            "schema_version": 1,
            "mode": f"mini-swe-{attempt}",
            "patch_nonempty": bool(patch.strip()),
            "changed_files": changed_files,
            "test_files_changed": test_files_changed,
            "sandbox_provider": substrate,
        },
    )
    validation_contract = _read_artifact_or_default(
        artifact_paths.get("validation_contract"),
        _validation_contract_for_case(case, attempt=attempt, commands_run=commands_run),
    )
    validation_contract["commands_run"] = commands_run
    test_gate = evaluate_evidence_gate(audit=audit, contract=validation_contract)
    if _has_observed_test_command_id(commands_run):
        test_gate.setdefault("observed", {})["tests_passed_count"] = 1
    adversarial = _read_artifact_or_default(
        artifact_paths.get("adversarial_review"),
        _adversarial_review_for_case(case),
    )
    moderator = _read_artifact_or_default(
        artifact_paths.get("moderator_filter"),
        _moderator_filter_for_case(case),
    )
    materialization = _read_artifact_or_default(
        artifact_paths.get("review_materialization"),
        _review_materialization_for_case(case),
    )
    gate = evaluate_review_accountability(
        adversarial=adversarial,
        moderator=moderator,
        test_gate=test_gate,
        materialization=materialization,
        patch_diff=patch,
    )
    grade = grade_mini_swe_attempt(
        case=case,
        patch=patch,
        changed_files=changed_files,
        validation_contract=validation_contract,
        test_gate=test_gate,
        accountability_gate=gate,
    )
    eval_metadata = {
        **attempt_result.eval_metadata(),
        **grade.to_metadata(),
    }
    fabro_run_id = attempt_result.provenance.get("fabro_run_id")
    if not isinstance(fabro_run_id, str):
        fabro_run_id = None
    result = {
        "instance_id": case.case_id,
        "model_name_or_path": "light-eval-mini-swe-scripted",
        "model_patch": patch,
        "status": "completed" if gate.get("route_decision") == "export" else "failed",
        "error": gate.get("failure_reason") if gate.get("route_decision") != "export" else None,
        "duration_s": 0,
        "fabro_run_id": fabro_run_id,
        "fabro_dump_dir": attempt_result.dump_path.as_posix()
        if attempt_result.dump_path
        else None,
        "trajectory_path": None,
        "source": mini_swe_source(case),
        "eval": eval_metadata,
        "audit": audit,
        "verify": {
            "schema_version": 1,
            "status": "completed",
            "mode": f"mini-swe-{attempt}",
            "patch_nonempty": bool(patch.strip()),
            "sandbox_provider": substrate,
        },
        "commands_run": commands_run,
        "test_evidence_gate": test_gate,
        "adversarial_review": adversarial,
        "moderator_filter": moderator,
        "review_materialization": materialization,
        "review_accountability_gate": gate,
    }

    config_dir = _prepare_mini_swe_config_dir(
        output_dir,
        case,
        validation_contract=validation_contract,
        attempt_result=attempt_result,
    )
    instance = _mini_swe_instance(case)
    write_run_bundle(
        instance=instance,
        result=result,
        output_dir=output_dir,
        config_dir=config_dir,
        sandbox_provider=substrate,
    )

    run_id = run_id_for_task(case.case_id)
    failures = _check_mini_swe_expected(case, result, grade=grade)
    return {
        "task_id": case.case_id,
        "run_id": run_id,
        "result": result,
        "failures": failures,
    }


def _create_repo_for_case(case: MiniSweCase, repo_dir: Path) -> None:
    src = repo_dir / "src"
    tests = repo_dir / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "__init__.py").write_text("")
    (src / "greeting.py").write_text(
        "def greeting(name):\n"
        "    return f\"hello {name}\"\n"
    )
    expected = "hello, Ada" if case.case_id == "good-source-existing-test" else "hello Ada"
    (tests / "test_greeting.py").write_text(_greeting_test_text(expected))
    git_run(repo_dir, "init")
    git_run(repo_dir, "add", "src/greeting.py", "src/__init__.py", "tests/test_greeting.py")


class ScriptedCalibrationRunner:
    """Apply deterministic patches and fixture artifacts for calibration."""

    name = "scripted"

    def __init__(self, *, substrate: str) -> None:
        self.substrate = substrate

    def run(self, case: MiniSweCase, repo_dir: Path, work_dir: Path) -> AttemptResult:
        if case.case_id not in {
            "good-source-plus-test",
            "runtime-proof-honesty",
            "good-source-existing-test",
            "overblocking-good-patch-with-minor-risk",
        }:
            raise SystemExit(f"mini-swe scripted case not implemented yet: {case.case_id}")
        _apply_greeting_fix_patch(repo_dir, change_test=bool(case.allowed_test_files))
        _run_public_tests(repo_dir)
        git_run(repo_dir, "add", "-N", ".")
        patch_path = work_dir / "patch.diff"
        patch_path.write_text(git_capture(repo_dir, "diff"))
        return AttemptResult(
            attempt_origin="scripted",
            artifact_origin="fixture",
            substrate="local",
            source=mini_swe_source(case),
            b2_slice_eligible=False,
            b2_model_eligible=False,
            b2_eligible=False,
            eligibility_failures=("artifact_origin_fixture",),
            patch_path=patch_path,
            artifact_paths={},
            commands_run_path=None,
            transcript_path=None,
            dump_path=None,
            provenance={
                "runner": "ScriptedCalibrationRunner",
                "case_artifacts_supplied": True,
            },
        )


class WorkflowSliceRunner:
    """Run a deterministic local Fabro workflow slice for mini-SWE."""

    name = "workflow-slice"

    def __init__(self, *, output_dir: Path, fabro_bin: Path) -> None:
        self.output_dir = output_dir
        self.fabro_bin = fabro_bin

    def run(self, case: MiniSweCase, repo_dir: Path, work_dir: Path) -> AttemptResult:
        if case.case_id not in {
            "good-source-plus-test",
            "good-source-existing-test",
            "runtime-proof-honesty",
            "overblocking-good-patch-with-minor-risk",
        }:
            raise SystemExit(f"mini-swe workflow-slice case not implemented yet: {case.case_id}")
        if not self.fabro_bin.exists():
            raise SystemExit(f"fabro binary missing: {self.fabro_bin}")

        slice_dir = self.output_dir / "_mini_swe_workflow_slice" / _slice_case_dir_name(case)
        if slice_dir.exists():
            shutil.rmtree(slice_dir)
        slice_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = slice_dir / "stage-artifacts"
        storage_dir = work_dir / "fabro-storage"
        config_path = slice_dir / "settings.toml"
        workflow_path = slice_dir / "workflow.fabro"
        write_workflow_smoke_config(storage_dir=storage_dir, config_path=config_path)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(
            "digraph MiniSweWorkflowSlice {\n"
            f"  graph [goal=\"{dot_escape(case.issue_text)}\"]\n"
            "  start [shape=Mdiamond, label=\"Start\"]\n"
            "  exit [shape=Msquare, label=\"Exit\"]\n"
            "  solve [label=\"Solve Generated Issue\", shape=parallelogram, "
            f"script=\"{dot_escape(_workflow_slice_solve_script(case, repo_dir, artifacts_dir))}\"]\n"
            "  review [label=\"Materialize Review\", shape=parallelogram, "
            f"script=\"{dot_escape(_workflow_slice_review_script(case, artifacts_dir))}\"]\n"
            "  start -> solve -> review -> exit\n"
            "}\n"
        )
        env = workflow_smoke_env(config_path=config_path, storage_dir=storage_dir)
        run_proc = run_fabro_command(
            self.fabro_bin,
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
        (slice_dir / "run.stdout").write_text(run_proc.stdout)
        (slice_dir / "run.stderr").write_text(run_proc.stderr)
        run_transcript = run_proc.stdout + run_proc.stderr
        transcript_path = slice_dir / "run.transcript"
        transcript_path.write_text(run_transcript)
        run_id = extract_workflow_smoke_run_id(run_transcript)
        try:
            if run_proc.returncode != 0:
                raise RuntimeError(f"mini-swe workflow-slice failed: {run_proc.stderr[-2000:]}")
            if "Status:    SUCCEEDED" not in run_transcript:
                raise RuntimeError("mini-swe workflow-slice did not report SUCCEEDED")
            expected_artifacts = (
                "patch.diff",
                "audit.json",
                "validation_contract.json",
                "commands_run.json",
                "adversarial_review.json",
                "moderator_filter.json",
                "review_materialization.json",
            )
            missing_artifacts = [
                name for name in expected_artifacts if not (artifacts_dir / name).exists()
            ]
            if missing_artifacts:
                raise RuntimeError(
                    "mini-swe workflow-slice missing artifacts: "
                    + ", ".join(missing_artifacts)
                )
        finally:
            stop_proc = run_fabro_command(
                self.fabro_bin,
                ["--no-upgrade-check", "server", "stop", "--storage-dir", str(storage_dir)],
                env=env,
            )
            (slice_dir / "stop.stdout").write_text(stop_proc.stdout)
            (slice_dir / "stop.stderr").write_text(stop_proc.stderr)

        patch_path = artifacts_dir / "patch.diff"
        commands_run_path = artifacts_dir / "commands_run.json"
        return AttemptResult(
            attempt_origin="workflow-slice",
            artifact_origin="workflow_stage",
            substrate="local",
            source=mini_swe_source(case),
            b2_slice_eligible=bool(run_id),
            b2_model_eligible=False,
            b2_eligible=bool(run_id),
            eligibility_failures=() if run_id else ("workflow_run_id_missing",),
            patch_path=patch_path,
            artifact_paths={
                "audit": (artifacts_dir / "audit.json").as_posix(),
                "validation_contract": (artifacts_dir / "validation_contract.json").as_posix(),
                "adversarial_review": (artifacts_dir / "adversarial_review.json").as_posix(),
                "moderator_filter": (artifacts_dir / "moderator_filter.json").as_posix(),
                "review_materialization": (artifacts_dir / "review_materialization.json").as_posix(),
            },
            commands_run_path=commands_run_path,
            transcript_path=transcript_path,
            dump_path=slice_dir,
            provenance={
                "runner": "WorkflowSliceRunner",
                "fabro_run_id": run_id,
                "workflow_path": workflow_path.as_posix(),
                "artifacts_dir": artifacts_dir.as_posix(),
                "case_artifacts_supplied": False,
            },
        )


def _apply_greeting_fix_patch(repo_dir: Path, *, change_test: bool) -> None:
    (repo_dir / "src" / "greeting.py").write_text(
        "def greeting(name):\n"
        "    return f\"hello, {name}\"\n"
    )
    if change_test:
        (repo_dir / "tests" / "test_greeting.py").write_text(
            _greeting_test_text("hello, Ada")
        )


def _greeting_test_text(expected: str) -> str:
    return (
        "import unittest\n\n"
        "from src.greeting import greeting\n\n\n"
        "class GreetingTest(unittest.TestCase):\n"
        "    def test_greeting_uses_comma(self):\n"
        f"        self.assertEqual(greeting(\"Ada\"), {expected!r})\n"
    )


def _run_public_tests(repo_dir: Path) -> None:
    proc = subprocess.run(
        ["python3", "-m", "unittest", "discover", "-s", "tests"],
        cwd=repo_dir,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"scripted mini-swe test command failed: {proc.stderr}")


def _workflow_slice_solve_script(case: MiniSweCase, repo_dir: Path, artifacts_dir: Path) -> str:
    change_test = bool(case.allowed_test_files)
    include_command_id = case.case_id != "runtime-proof-honesty"
    tests_added_json = json.dumps(
        [
            {
                "path": path,
                "description": "regression coverage for comma in greeting",
            }
            for path in case.allowed_test_files
        ]
    )
    no_test_justification = (
        "Existing test coverage already asserts the requested greeting behavior."
        if not case.allowed_test_files
        else None
    )
    return (
        "python3 - <<'PY'\n"
        "import json\n"
        "import os\n"
        "import subprocess\n"
        "from pathlib import Path\n"
        f"repo = Path({json.dumps(str(repo_dir))})\n"
        f"artifact_dir = Path({json.dumps(str(artifacts_dir))})\n"
        "artifact_dir.mkdir(parents=True, exist_ok=True)\n"
        "(repo / 'src' / 'greeting.py').write_text('def greeting(name):\\n    return f\"hello, {name}\"\\n')\n"
        f"change_test = {change_test!r}\n"
        "if change_test:\n"
        "    (repo / 'tests' / 'test_greeting.py').write_text(\n"
        "        'import unittest\\n\\n'\n"
        "        'from src.greeting import greeting\\n\\n\\n'\n"
        "        'class GreetingTest(unittest.TestCase):\\n'\n"
        "        '    def test_greeting_uses_comma(self):\\n'\n"
        "        '        self.assertEqual(greeting(\"Ada\"), \"hello, Ada\")\\n'\n"
        "    )\n"
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
        f"include_command_id = {include_command_id!r}\n"
        "if include_command_id:\n"
        "    command['id'] = 'cmd-001'\n"
        "audit = {\n"
        "    'schema_version': 1,\n"
        "    'mode': 'mini-swe-workflow-slice',\n"
        "    'patch_nonempty': bool(patch.strip()),\n"
        "    'changed_files': changed_files,\n"
        "    'test_files_changed': test_files,\n"
        "    'sandbox_provider': 'local',\n"
        "}\n"
        f"tests_added = {tests_added_json}\n"
        f"no_test_justification = {no_test_justification!r}\n"
        "validation_contract = {\n"
        "    'schema_version': 1,\n"
        "    'mode': 'mini-swe-workflow-slice',\n"
        "    'tests_added': tests_added,\n"
        "    'commands_run': [command],\n"
        "}\n"
        "if no_test_justification:\n"
        "    validation_contract['no_test_justification'] = no_test_justification\n"
        "(artifact_dir / 'patch.diff').write_text(patch)\n"
        "(artifact_dir / 'audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\\n')\n"
        "(artifact_dir / 'validation_contract.json').write_text(json.dumps(validation_contract, indent=2, sort_keys=True) + '\\n')\n"
        "(artifact_dir / 'commands_run.json').write_text(json.dumps([command], indent=2, sort_keys=True) + '\\n')\n"
        "print('mini-swe workflow-slice: solved generated issue')\n"
        "PY"
    )


def _workflow_slice_review_script(case: MiniSweCase, artifacts_dir: Path) -> str:
    artifacts = {
        "adversarial_review.json": _adversarial_review_for_case(case),
        "moderator_filter.json": _moderator_filter_for_case(case),
        "review_materialization.json": _review_materialization_for_case(case),
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


def _adversarial_review_for_case(case: MiniSweCase) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if case.case_id == "overblocking-good-patch-with-minor-risk":
        rows.append(
            {
                "id": "minor-001",
                "severity": "minor",
                "category": "maintainability",
                "summary": "The test name is specific to comma behavior.",
                "required_files": ["tests/test_greeting.py"],
                "closure_requires": "runtime_tests",
            }
        )
    return {
        "schema_version": 1,
        "stage": "adversarial_review",
        "status": "passed",
        "rows": rows,
    }


def _moderator_filter_for_case(case: MiniSweCase) -> dict[str, Any]:
    dispositions: list[dict[str, Any]] = []
    if case.case_id == "overblocking-good-patch-with-minor-risk":
        dispositions.append(
            {
                "id": "minor-001",
                "state": "downgraded",
                "category": "maintainability",
                "severity": "minor",
                "closure_check": (
                    "The row is nonblocking: tests/test_greeting.py was updated "
                    "and the machine-observed test command passed."
                ),
                "evidence": [
                    "tests/test_greeting.py changed",
                    "python3 -m unittest discover -s tests passed via cmd-001",
                ],
                "artifact_path": "output/commands_run.json",
                "artifact_field": "commands_run[0].id",
            }
        )
    return {
        "schema_version": 1,
        "stage": "moderator_filter",
        "status": "passed",
        "dispositions": dispositions,
    }


def _review_materialization_for_case(case: MiniSweCase) -> dict[str, Any]:
    rendered_rows: list[dict[str, Any]] = []
    if case.case_id == "overblocking-good-patch-with-minor-risk":
        rendered_rows.append(
            {
                "id": "minor-001",
                "state": "downgraded",
                "export_blocking": False,
            }
        )
    return {
        "schema_version": 1,
        "stage": "review_materialization",
        "status": "passed",
        "errors": [],
        "rendered_rows": rendered_rows,
    }


def _commands_run_for_case(
    case: MiniSweCase,
    *,
    attempt_result: AttemptResult,
) -> list[dict[str, Any]]:
    command = {
        "argv_or_shell": "python3 -m unittest discover -s tests",
        "command": "python3 -m unittest discover -s tests",
        "cwd": ".",
        "exit_code": 0,
        "status": "passed",
        "is_test_command": True,
        "allowlist_class": "test",
        "stdout_tail": "",
        "stderr_tail": "",
        "repo_state": None,
    }
    if case.case_id != "runtime-proof-honesty":
        command["id"] = "cmd-001"
    return [command]


def _slice_case_dir_name(case: MiniSweCase) -> str:
    digest = hashlib.sha1(case.case_id.encode("utf-8")).hexdigest()[:10]
    slug = "".join(char if char.isalnum() or char in "-_" else "-" for char in case.case_id)
    return f"{slug[:32]}-{digest}"


def _validation_contract_for_case(
    case: MiniSweCase,
    *,
    attempt: str,
    commands_run: list[dict[str, Any]],
) -> dict[str, Any]:
    contract: dict[str, Any] = {
        "schema_version": 1,
        "mode": f"mini-swe-{attempt}",
        "commands_run": commands_run,
    }
    if case.allowed_test_files:
        contract["tests_added"] = [
            {
                "path": path,
                "description": "regression coverage for comma in greeting",
            }
            for path in case.allowed_test_files
        ]
    else:
        contract["tests_added"] = []
        contract["no_test_justification"] = (
            "Existing test coverage already asserts the requested greeting behavior."
        )
    return contract


def _has_observed_test_command_id(commands_run: list[dict[str, Any]]) -> bool:
    return any(
        command.get("id")
        and command.get("is_test_command")
        and command.get("exit_code") == 0
        for command in commands_run
    )


def _read_artifact_or_default(path: str | None, default: dict[str, Any]) -> dict[str, Any]:
    if not path:
        return default
    artifact_path = Path(path)
    if not artifact_path.exists():
        return default
    payload = json.loads(artifact_path.read_text())
    return payload if isinstance(payload, dict) else default


def _prepare_mini_swe_config_dir(
    output_dir: Path,
    case: MiniSweCase,
    *,
    validation_contract: dict[str, Any],
    attempt_result: AttemptResult,
) -> Path:
    config_dir = prepare_config_dir(output_dir, case.case_id)
    (config_dir / "goal.txt").write_text(case.issue_text + "\n")
    (config_dir / "validation_contract.json").write_text(
        json.dumps(validation_contract, indent=2, sort_keys=True) + "\n"
    )
    if attempt_result.transcript_path and attempt_result.transcript_path.exists():
        dump_dir = config_dir / "run_dump"
        dump_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(attempt_result.transcript_path, dump_dir / "run.transcript")
    return config_dir


def _mini_swe_instance(case: MiniSweCase) -> dict[str, Any]:
    return {
        "instance_id": case.case_id,
        "repo": "mini-swe/generated",
        "version": case.suite,
        "base_commit": "generated",
        "source": mini_swe_source(case),
        "repository": {
            "provider": "local",
            "owner": "mini-swe",
            "name": "generated",
            "full_name": "mini-swe/generated",
            "base_ref": None,
            "base_sha": "generated",
            "version": case.suite,
        },
    }


def _mini_swe_summary(results: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, Any]:
    evals = [result.get("eval", {}) for result in results if isinstance(result.get("eval"), dict)]
    return {
        "total": len(results),
        "failed": len(failures),
        "calibration_total": sum(1 for item in evals if item.get("attempt_origin") == "scripted"),
        "b2_eligible": sum(1 for item in evals if item.get("b2_eligible")),
        "b2_slice_eligible": sum(1 for item in evals if item.get("b2_slice_eligible")),
        "b2_model_eligible": sum(1 for item in evals if item.get("b2_model_eligible")),
        "patch_pass": sum(1 for item in evals if item.get("patch_grade") == "pass"),
        "artifact_pass": sum(1 for item in evals if item.get("artifact_grade") == "pass"),
        "export_pass": sum(1 for item in evals if item.get("export_grade") == "pass"),
        "false_exports": sum(1 for item in evals if item.get("false_export")),
        "false_blanks": sum(1 for item in evals if item.get("false_blank")),
        "cases_by_attempt_origin": _counts(evals, "attempt_origin"),
        "cases_by_artifact_origin": _counts(evals, "artifact_origin"),
        "cases_by_substrate": _counts(evals, "substrate"),
        "ineligible_by_reason": _ineligible_counts(evals),
        "failures": failures,
    }


def _counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = item.get(key)
        if isinstance(value, str):
            counts[value] = counts.get(value, 0) + 1
    return counts


def _ineligible_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        failures = item.get("eligibility_failures")
        if not isinstance(failures, list):
            continue
        for failure in failures:
            if isinstance(failure, str):
                counts[failure] = counts.get(failure, 0) + 1
    return counts


def _check_mini_swe_expected(
    case: MiniSweCase,
    result: dict[str, Any],
    *,
    grade: MiniSweGrade,
) -> list[dict[str, Any]]:
    failures = []
    prediction = build_prediction_record(result)
    if case.expected_decision_hint == "export" and not prediction["model_patch"]:
        failures.append({"kind": "unexpected_blank", "task_id": result["instance_id"]})
    if case.expected_decision_hint != "export" and prediction["model_patch"]:
        failures.append({"kind": "false_export", "task_id": result["instance_id"]})
    if not grade.patch_pass:
        failures.append({"kind": "patch_grade_failed", "task_id": result["instance_id"]})
    if case.expected_decision_hint == "export" and not grade.artifact_pass:
        failures.append({"kind": "artifact_grade_failed", "task_id": result["instance_id"]})
    if case.expected_decision_hint != "export" and grade.artifact_pass:
        failures.append({"kind": "expected_artifact_failure_missing", "task_id": result["instance_id"]})
    return failures
