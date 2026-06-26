from __future__ import annotations
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ...run_attempt import (
    dump_run,
    fetch_run_diff,
    find_patch,
    write_events_jsonl,
    write_trajectory_from_events,
)
from ...workflow_generator import (
    STRUCTURED_MODERATED_PROFILE,
    VERIFY_DIFF_CHECK,
    generate_issue_to_pr_workflow,
    validate_generated_workflow,
)
from ..paths import DEFAULT_SYNTHETIC_DOCKER_IMAGE
from ..process import git_capture, git_run
from ..task_schema import AttemptResult, MiniSweCase, mini_swe_source
from ..workflow_common import (
    extract_workflow_smoke_run_id,
    run_fabro_command,
    workflow_smoke_env,
    write_workflow_smoke_config,
)
from .artifacts import materialize_model_artifacts
from .cases import ensure_mini_swe_case_supported, task_contract_for_case
from .credentials import bridge_model_credentials, credential_preflight_report
from .evidence import (
    commands_run_from_validation_contract_path,
)
from .repo import (
    append_working_dir_config,
    apply_case_patch,
    apply_case_patch_in_docker,
    apply_patch_to_repo,
    model_setup_script,
    run_public_tests,
)

class ScriptedCalibrationRunner:
    name = "scripted"
    def __init__(
        self,
        *,
        substrate: str,
        docker_image: str = DEFAULT_SYNTHETIC_DOCKER_IMAGE,
    ) -> None:
        self.substrate = substrate
        self.docker_image = docker_image
    def run(self, case: MiniSweCase, repo_dir: Path, work_dir: Path) -> AttemptResult:
        ensure_mini_swe_case_supported(case, attempt=self.name)
        if self.substrate == "docker":
            apply_case_patch_in_docker(
                case,
                repo_dir,
                docker_image=self.docker_image,
            )
        else:
            apply_case_patch(case, repo_dir)
            run_public_tests(repo_dir)
        git_run(repo_dir, "add", "-N", ".")
        patch_path = work_dir / "patch.diff"
        patch_path.write_text(git_capture(repo_dir, "diff"))
        return AttemptResult(
            attempt_origin="scripted",
            artifact_origin="fixture",
            substrate=self.substrate,
            source=mini_swe_source(case),
            b2_slice_eligible=False,
            b2_model_eligible=False,
            b2_eligible=False,
            eligibility_failures=("artifact_origin_fixture",),
            evaluation_role="calibration_provenance",
            eligibility_proof={
                "repo_facts_recomputed": True,
                "artifact_claims_compared": True,
                "commands_run_source": "scripted_case_fallback",
            },
            patch_path=patch_path,
            artifact_paths={},
            commands_run_path=None,
            trajectory_path=None,
            transcript_path=None,
            dump_path=None,
            provenance={
                "runner": "ScriptedCalibrationRunner",
                "case_artifacts_supplied": True,
                "docker_image": self.docker_image if self.substrate == "docker" else None,
            },
        )

@dataclass(frozen=True)
class _ModelRunContext:
    fabro_bin: Path
    run_dir: Path
    artifacts_dir: Path
    command_cwd: Path
    model_workspace: Path
    storage_dir: Path
    config_path: Path
    workflow_path: Path

@dataclass(frozen=True)
class _ModelWorkflowArtifacts:
    fabro_run_id: str
    workflow_reported_succeeded: bool
    dump_path: Path
    patch_path: Path
    artifact_paths: dict[str, str]
    trajectory_path: Path | None
    transcript_path: Path
    eligibility_failures: tuple[str, ...]
    run_returncode: int

class ModelWorkflowRunner:
    name = "model"
    def __init__(
        self,
        *,
        output_dir: Path,
        fabro_bin: Path,
        model: str | None,
        provider: str | None,
        workflow_timeout_seconds: int = 600,
        credential_bridge: str = "off",
        auth_storage_dir: Path | None = None,
        credential_preflight: bool = False,
    ) -> None:
        self.output_dir = output_dir
        self.fabro_bin = fabro_bin
        self.model = model
        self.provider = provider
        self.workflow_timeout_seconds = workflow_timeout_seconds
        self.credential_bridge = credential_bridge
        self.auth_storage_dir = auth_storage_dir
        self.credential_preflight = credential_preflight
    def run(self, case: MiniSweCase, repo_dir: Path, work_dir: Path) -> AttemptResult:
        context = self._prepare_run_context(case, work_dir)
        credential_bridge_report = self._bridge_credentials(context)
        self._write_workflow(case, repo_dir, context)
        env = self._workflow_env(context, credential_bridge_report)
        preflight_report = self._run_credential_preflight(context, env)
        artifacts = self._run_and_collect_artifacts(case, repo_dir, context, env)
        b2_eligible = not artifacts.eligibility_failures
        return AttemptResult(
            attempt_origin="model",
            artifact_origin="model_workflow",
            substrate="local",
            source=mini_swe_source(case),
            b2_slice_eligible=False,
            b2_model_eligible=b2_eligible,
            b2_eligible=b2_eligible,
            eligibility_failures=artifacts.eligibility_failures,
            evaluation_role="b2_candidate",
            eligibility_proof={
                "repo_facts_recomputed": True,
                "artifact_claims_compared": True,
                "workflow_exit_code": artifacts.run_returncode,
                "workflow_reported_succeeded": artifacts.workflow_reported_succeeded,
                "commands_run_source": "validation_contract_artifact"
                if b2_eligible
                else "missing_or_incomplete",
            },
            patch_path=artifacts.patch_path,
            artifact_paths=artifacts.artifact_paths,
            commands_run_path=None,
            trajectory_path=artifacts.trajectory_path,
            transcript_path=artifacts.transcript_path,
            dump_path=artifacts.dump_path,
            provenance={
                "runner": "ModelWorkflowRunner",
                "fabro_run_id": artifacts.fabro_run_id,
                "workflow_path": context.workflow_path.as_posix(),
                "model": self.model,
                "provider": self.provider,
                "credential_bridge": credential_bridge_report,
                "credential_preflight": preflight_report,
                "case_artifacts_supplied": False,
            },
        )
    def _prepare_run_context(self, case: MiniSweCase, work_dir: Path) -> _ModelRunContext:
        if not self.fabro_bin.exists():
            raise SystemExit(f"fabro binary missing: {self.fabro_bin}")
        fabro_bin = self.fabro_bin.resolve()
        run_dir = (self.output_dir / "_mini_swe_model" / slice_case_dir_name(case)).resolve()
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = run_dir / "stage-artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        command_cwd = run_dir / "command-cwd"
        command_cwd.mkdir()
        model_workspace = run_dir / "workspace"
        model_workspace.mkdir()
        storage_dir = work_dir / "fabro-model-storage"
        config_path = run_dir / "settings.toml"
        workflow_path = run_dir / "workflow.fabro"
        write_workflow_smoke_config(storage_dir=storage_dir, config_path=config_path)
        append_working_dir_config(config_path, model_workspace)
        return _ModelRunContext(
            fabro_bin=fabro_bin,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            command_cwd=command_cwd,
            model_workspace=model_workspace,
            storage_dir=storage_dir,
            config_path=config_path,
            workflow_path=workflow_path,
        )
    def _bridge_credentials(self, context: _ModelRunContext) -> dict[str, Any]:
        report = bridge_model_credentials(
            bridge=self.credential_bridge,
            source_storage_dir=self.auth_storage_dir,
            target_storage_dir=context.storage_dir,
        )
        self._write_json(context.run_dir / "credential_bridge.json", report)
        if self.credential_bridge == "openai-codex" and report.get("status") != "copied":
            raise MiniSweProcessBlock(
                reason="credential_bridge_failed",
                message="mini-swe Codex credential bridge failed before model execution.",
                detail=f"credential bridge status: {report.get('status')}",
            )
        return report
    def _write_workflow(
        self,
        case: MiniSweCase,
        repo_dir: Path,
        context: _ModelRunContext,
    ) -> None:
        workflow = generate_issue_to_pr_workflow(
            graph_name="MiniSweModelWorkflow",
            setup_script=model_setup_script(repo_dir, task_contract_for_case(case)),
            workflow_profile=STRUCTURED_MODERATED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
            solve_prompt=case.issue_text,
            simple_fixup_prompt=case.case_id == "good-test-only",
        )
        validate_generated_workflow(
            workflow,
            workflow_profile=STRUCTURED_MODERATED_PROFILE,
        )
        context.workflow_path.write_text(workflow)
    def _workflow_env(
        self,
        context: _ModelRunContext,
        credential_bridge_report: dict[str, Any],
    ) -> dict[str, str]:
        env = workflow_smoke_env(
            config_path=context.config_path,
            storage_dir=context.storage_dir,
        )
        if self.credential_bridge == "openai-codex" and "OPENAI_API_KEY" in env:
            env = dict(env)
            env.pop("OPENAI_API_KEY", None)
            credential_bridge_report["scrubbed_env_secret_names"] = ["OPENAI_API_KEY"]
            self._write_json(context.run_dir / "credential_bridge.json", credential_bridge_report)
        return env
    def _run_credential_preflight(
        self,
        context: _ModelRunContext,
        env: dict[str, str],
    ) -> dict[str, Any]:
        report = credential_preflight_report(
            fabro_bin=context.fabro_bin,
            env=env,
            enabled=self.credential_preflight,
            provider=self.provider or ("openai" if self.credential_bridge == "openai-codex" else None),
            model=self.model,
        )
        self._write_json(context.run_dir / "credential_preflight.json", report)
        if self.credential_preflight and report.get("status") == "failed":
            _stop_fabro_server(context.fabro_bin, context.storage_dir, context.run_dir, env)
            raise MiniSweProcessBlock(
                reason="credential_preflight_failed",
                message="mini-swe model credential preflight failed; fix provider auth before running model attempts.",
                detail=f"fabro model test exited with status {report.get('returncode')}",
            )
        return report
    def _run_and_collect_artifacts(
        self,
        case: MiniSweCase,
        repo_dir: Path,
        context: _ModelRunContext,
        env: dict[str, str],
    ) -> _ModelWorkflowArtifacts:
        run_proc = self._run_workflow(case, context, env)
        transcript_path, run_transcript = _write_run_output(context.run_dir, run_proc)
        fabro_run_id = extract_workflow_smoke_run_id(run_transcript)
        try:
            self._raise_if_workflow_ungradable(run_proc, run_transcript, fabro_run_id)
            workflow_reported_succeeded = "Status:    SUCCEEDED" in run_transcript
            dump_path = dump_run(
                str(context.fabro_bin),
                fabro_run_id,
                context.run_dir,
                env=env,
            )
            if dump_path is None:
                raise MiniSweProcessBlock(
                    reason="model_workflow_dump_failed",
                    message="mini-swe model workflow dump failed",
                    detail=run_transcript[-4000:],
                )
            events_path = write_events_jsonl(
                str(context.fabro_bin),
                fabro_run_id,
                dump_path,
                env=env,
            )
            trajectory_path = (
                write_trajectory_from_events(events_path)
                if events_path is not None
                else None
            )
            patch_path, patch = self._write_model_patch(repo_dir, context, dump_path, fabro_run_id, env)
            artifact_paths = materialize_model_artifacts(
                dump_path=dump_path,
                artifacts_dir=context.artifacts_dir,
                workspace_dir=context.model_workspace,
            )
            eligibility_failures = self._eligibility_failures(
                returncode=run_proc.returncode,
                workflow_reported_succeeded=workflow_reported_succeeded,
                patch=patch,
                artifact_paths=artifact_paths,
                trajectory_path=trajectory_path,
            )
        finally:
            _stop_fabro_server(context.fabro_bin, context.storage_dir, context.run_dir, env)
        return _ModelWorkflowArtifacts(
            fabro_run_id=fabro_run_id,
            workflow_reported_succeeded=workflow_reported_succeeded,
            dump_path=dump_path,
            patch_path=patch_path,
            artifact_paths=artifact_paths,
            trajectory_path=trajectory_path,
            transcript_path=transcript_path,
            eligibility_failures=eligibility_failures,
            run_returncode=run_proc.returncode,
        )
    def _run_workflow(
        self,
        case: MiniSweCase,
        context: _ModelRunContext,
        env: dict[str, str],
    ) -> Any:
        args = [
            "--no-upgrade-check",
            "run",
            "--auto-approve",
            "--environment",
            "local",
            "--goal",
            case.issue_text,
        ]
        if self.provider:
            args.extend(["--provider", self.provider])
        if self.model:
            args.extend(["--model", self.model])
        args.append(str(context.workflow_path))
        run_proc = run_fabro_command(
            context.fabro_bin,
            args,
            env=env,
            timeout=self.workflow_timeout_seconds,
            cwd=context.command_cwd,
        )
        return run_proc
    def _raise_if_workflow_ungradable(
        self,
        run_proc: Any,
        run_transcript: str,
        fabro_run_id: str | None,
    ) -> None:
        if run_proc.returncode != 0 and "No LLM providers configured" in run_proc.stderr:
            raise MiniSweProcessBlock(
                reason="provider_not_configured",
                message=(
                    "mini-swe model attempt needs a configured LLM provider. "
                    "Set ANTHROPIC_API_KEY or OPENAI_API_KEY, pass --provider/--model "
                    "for an already configured provider, or use --attempt scripted "
                    "for deterministic calibration coverage."
                ),
                detail=run_proc.stderr[-4000:],
            )
        if fabro_run_id:
            return
        reason = (
            "model_workflow_failed"
            if run_proc.returncode != 0
            else "model_workflow_run_id_missing"
        )
        raise MiniSweProcessBlock(
            reason=reason,
            message=(
                "mini-swe model workflow failed before grading artifacts"
                if run_proc.returncode != 0
                else "mini-swe model workflow did not report a run id"
            ),
            detail=run_transcript[-4000:],
        )
    def _write_model_patch(
        self,
        repo_dir: Path,
        context: _ModelRunContext,
        dump_path: Path,
        fabro_run_id: str,
        env: dict[str, str],
    ) -> tuple[Path, str]:
        patch = fetch_run_diff(str(context.fabro_bin), fabro_run_id, env=env) or ""
        if not patch.strip() and dump_path:
            patch = find_patch(dump_path) or patch
        patch_path = context.artifacts_dir / "patch.diff"
        patch_path.write_text(patch)
        if patch.strip():
            apply_patch_to_repo(repo_dir, patch)
        return patch_path, patch
    def _eligibility_failures(
        self,
        *,
        returncode: int,
        workflow_reported_succeeded: bool,
        patch: str,
        artifact_paths: dict[str, str],
        trajectory_path: Path | None,
    ) -> tuple[str, ...]:
        required = {
            "audit",
            "validation_contract",
            "test_evidence_gate",
            "adversarial_review",
            "moderator_filter",
            "review_materialization",
        }
        missing = sorted(required - set(artifact_paths))
        validation_commands = commands_run_from_validation_contract_path(
            artifact_paths.get("validation_contract")
        )
        workflow_failures = (
            ["model_workflow_failed"] * (returncode != 0)
            + ["model_workflow_not_succeeded"] * (not workflow_reported_succeeded)
        )
        return tuple(
            workflow_failures
            + ["model_workflow_missing_patch"] * (not patch.strip())
            + [f"model_workflow_missing_{name}" for name in missing]
            + ["model_workflow_missing_commands_run"] * (not validation_commands)
            + ["model_workflow_missing_trajectory"] * (trajectory_path is None)
        )
    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

def _write_run_output(output_dir: Path, run_proc: Any) -> tuple[Path, str]:
    (output_dir / "run.stdout").write_text(run_proc.stdout)
    (output_dir / "run.stderr").write_text(run_proc.stderr)
    run_transcript = run_proc.stdout + run_proc.stderr
    transcript_path = output_dir / "run.transcript"
    transcript_path.write_text(run_transcript)
    return transcript_path, run_transcript

def _stop_fabro_server(fabro_bin: Path, storage_dir: Path, output_dir: Path, env: dict[str, str]) -> None:
    stop_proc = run_fabro_command(
        fabro_bin,
        ["--no-upgrade-check", "server", "stop", "--storage-dir", str(storage_dir)],
        env=env,
    )
    (output_dir / "stop.stdout").write_text(stop_proc.stdout)
    (output_dir / "stop.stderr").write_text(stop_proc.stderr)

class MiniSweProcessBlock(Exception):
    def __init__(self, *, reason: str, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.detail = detail
    def to_failure(self, *, task_id: str, attempt: str) -> dict[str, str]:
        failure = {
            "kind": "process_block",
            "task_id": task_id,
            "attempt": attempt,
            "reason": self.reason,
            "message": self.message,
        }
        if self.detail:
            failure["detail"] = self.detail
        return failure

def slice_case_dir_name(case: MiniSweCase) -> str:
    digest = hashlib.sha1(case.case_id.encode("utf-8")).hexdigest()[:10]
    slug = "".join(char if char.isalnum() or char in "-_" else "-" for char in case.case_id)
    return f"{slug[:32]}-{digest}"
