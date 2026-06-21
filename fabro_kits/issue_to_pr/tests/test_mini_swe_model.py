import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fabro_kits.issue_to_pr.light_eval import run_mini_swe
from fabro_kits.issue_to_pr.light_eval.grader import grade_mini_swe_attempt
from fabro_kits.issue_to_pr.light_eval.mini_swe import orchestrator as mini_swe_orchestrator
from fabro_kits.issue_to_pr.light_eval.mini_swe.artifacts import (
    materialize_model_artifacts,
)
from fabro_kits.issue_to_pr.light_eval.mini_swe.credentials import (
    bridge_model_credentials,
    credential_preflight_report,
    storage_vault_path,
)
from fabro_kits.issue_to_pr.light_eval.mini_swe.evidence import (
    commands_run_from_artifacts,
    effective_expected_decision_hint,
)
from fabro_kits.issue_to_pr.light_eval.mini_swe.repo import model_setup_script
from fabro_kits.issue_to_pr.light_eval.mini_swe.repo import apply_case_patch
from fabro_kits.issue_to_pr.light_eval.task_schema import AttemptResult, MiniSweCase


class MiniSweModelTest(unittest.TestCase):

    def test_mini_swe_model_attempt_reports_missing_fabro_binary(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "fabro_kits.issue_to_pr.light_eval",
                "mini-swe",
                "--case",
                "good-source-plus-test",
                "--attempt",
                "model",
                "--fabro-bin",
                "tmp/missing-fabro-for-mini-swe-model-test",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: python -m fabro_kits.issue_to_pr.light_eval mini-swe", result.stderr)
        self.assertIn("fabro binary missing", result.stderr)

    def test_mini_swe_model_attempt_reports_missing_provider_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_fabro = root / "fake-fabro"
            model_pwd = root / "model-pwd"
            fake_fabro.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$*\" == *\"server stop\"* ]]; then exit 0; fi\n"
                f"pwd > {model_pwd.as_posix()}\n"
                "cat >&2 <<'EOF'\n"
                "Status:    FAILED\n"
                "Failure:   Precondition failed: No LLM providers configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY, or pass --dry-run to simulate.\n"
                "EOF\n"
                "exit 1\n"
            )
            fake_fabro.chmod(0o755)

            summary = run_mini_swe(
                "good-test-only",
                output_dir=root / "out",
                attempt="model",
                fabro_bin=fake_fabro,
            )

            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["completed"], 0)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["process_blocked"], 1)
            self.assertEqual(summary["provider_not_configured"], 1)
            self.assertEqual(summary["process_blocks_by_reason"], {"provider_not_configured": 1})
            self.assertEqual(summary["failures"][0]["kind"], "process_block")
            self.assertEqual(summary["failures"][0]["reason"], "provider_not_configured")
            self.assertIn("needs a configured LLM provider", summary["failures"][0]["message"])
            self.assertIsInstance(summary["total_duration_s"], float)
            self.assertGreaterEqual(summary["total_duration_s"], 0.0)
            self.assertNotEqual(Path(model_pwd.read_text().strip()).resolve(), Path.cwd().resolve())
            self.assertTrue(model_pwd.read_text().strip().endswith("/command-cwd"))
            model_run_dir = next((root / "out" / "_mini_swe_model").glob("good-test-only-*"))
            settings = (model_run_dir / "settings.toml").read_text()
            self.assertIn("[run]", settings)
            self.assertIn("working_dir", settings)
            self.assertIn("/workspace", settings)

    def test_mini_swe_model_text_cli_reports_process_blocked_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_fabro = root / "fake-fabro"
            fake_fabro.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$*\" == *\"server stop\"* ]]; then exit 0; fi\n"
                "cat >&2 <<'EOF'\n"
                "Status:    FAILED\n"
                "Failure:   Precondition failed: No LLM providers configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY, or pass --dry-run to simulate.\n"
                "EOF\n"
                "exit 1\n"
            )
            fake_fabro.chmod(0o755)

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "fabro_kits.issue_to_pr.light_eval",
                    "mini-swe",
                    "--case",
                    "good-test-only",
                    "--attempt",
                    "model",
                    "--format",
                    "text",
                    "--output-dir",
                    str(root / "out"),
                    "--fabro-bin",
                    str(fake_fabro),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("mini-swe: total=1 failed=1", result.stdout)
            self.assertIn("false_exports=0", result.stdout)
            self.assertIn("process_blocked=1", result.stdout)
            failures = json.loads(result.stdout.split("\n", 1)[1])
            self.assertEqual(failures[0]["kind"], "process_block")
            self.assertEqual(failures[0]["reason"], "provider_not_configured")
            self.assertIn("needs a configured LLM provider", failures[0]["message"])

    def test_mini_swe_model_text_cli_reports_credential_preflight_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_fabro = root / "fake-fabro"
            run_called = root / "run-called"
            fake_fabro.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$*\" == *\"server stop\"* ]]; then exit 0; fi\n"
                "if [[ \"$*\" == *\"model test\"* ]]; then\n"
                "  printf 'auth failed\\n' >&2\n"
                "  exit 1\n"
                "fi\n"
                f"touch {run_called.as_posix()}\n"
                "exit 0\n"
            )
            fake_fabro.chmod(0o755)

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "fabro_kits.issue_to_pr.light_eval",
                    "mini-swe",
                    "--case",
                    "good-test-only",
                    "--attempt",
                    "model",
                    "--credential-preflight",
                    "--format",
                    "text",
                    "--output-dir",
                    str(root / "out"),
                    "--fabro-bin",
                    str(fake_fabro),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(run_called.exists())
            self.assertIn("mini-swe: total=1 failed=1", result.stdout)
            self.assertIn("false_exports=0", result.stdout)
            self.assertIn("process_blocked=1", result.stdout)
            failures = json.loads(result.stdout.split("\n", 1)[1])
            self.assertEqual(failures[0]["kind"], "process_block")
            self.assertEqual(failures[0]["reason"], "credential_preflight_failed")
            self.assertIn("credential preflight failed", failures[0]["message"])

    def test_mini_swe_model_setup_refuses_non_empty_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
            (repo / "src").mkdir()
            (repo / "src" / "__init__.py").write_text("")
            dst = root / "dst"
            dst.mkdir()
            (dst / ".git").mkdir()
            original_head = dst / ".git" / "HEAD"
            original_head.write_text("ref: refs/heads/custom-fab\n")

            proc = subprocess.run(
                model_setup_script(repo),
                shell=True,
                cwd=dst,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("refuses non-empty cwd", proc.stderr)
            self.assertEqual(original_head.read_text(), "ref: refs/heads/custom-fab\n")
            self.assertFalse((dst / "src").exists())

    def test_mini_swe_model_setup_seeds_task_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "src").mkdir()
            (repo / "src" / "__init__.py").write_text("")
            dst = root / "dst"
            dst.mkdir()

            proc = subprocess.run(
                model_setup_script(
                    repo,
                    {
                        "expected_review_rows": [{"id": "minor-001"}],
                        "source_change_allowed": True,
                    },
                ),
                shell=True,
                cwd=dst,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            contract = json.loads((dst / ".fabro/issue-to-pr/validation.json").read_text())
            self.assertEqual(contract["expected_review_rows"][0]["id"], "minor-001")
            self.assertTrue(contract["source_change_allowed"])

    def test_mini_swe_codex_bridge_copies_only_openai_codex_and_preserves_oauth_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source_vault = storage_vault_path(source)
            source_vault.parent.mkdir(parents=True)
            codex_entry = {
                "value": "{\"refresh_token\":\"secret-refresh\"}",
                "type": "oauth",
                "description": "Codex OAuth",
                "created_at": "2026-06-19T00:00:00Z",
                "updated_at": "2026-06-19T00:00:01Z",
            }
            source_vault.write_text(
                json.dumps(
                    {
                        "OPENAI_CODEX": codex_entry,
                        "OPENAI_API_KEY": {
                            "value": "api-key",
                            "type": "token",
                            "created_at": "2026-06-19T00:00:00Z",
                            "updated_at": "2026-06-19T00:00:01Z",
                        },
                        "ANTHROPIC_API_KEY": {
                            "value": "anthropic-key",
                            "type": "token",
                            "created_at": "2026-06-19T00:00:00Z",
                            "updated_at": "2026-06-19T00:00:01Z",
                        },
                    },
                    indent=2,
                )
                + "\n"
            )

            report = bridge_model_credentials(
                bridge="openai-codex",
                source_storage_dir=source,
                target_storage_dir=target,
            )

            copied = json.loads(storage_vault_path(target).read_text())
            self.assertEqual(report["status"], "copied")
            self.assertEqual(report["copied_secret_names"], ["OPENAI_CODEX"])
            self.assertEqual(report["source_secret_type"], "oauth")
            self.assertEqual(copied, {"OPENAI_CODEX": codex_entry})
            self.assertEqual(stat.S_IMODE(storage_vault_path(target).stat().st_mode), 0o600)
            self.assertNotIn("api-key", json.dumps(report))
            self.assertNotIn("secret-refresh", json.dumps(report))

    def test_mini_swe_codex_bridge_does_not_copy_unrelated_source_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source_vault = storage_vault_path(source)
            target_vault = storage_vault_path(target)
            source_vault.parent.mkdir(parents=True)
            target_vault.parent.mkdir(parents=True)
            source_vault.write_text(
                json.dumps(
                    {
                        "OPENAI_CODEX": {
                            "value": "codex-oauth-json",
                            "type": "oauth",
                            "created_at": "2026-06-19T00:00:00Z",
                            "updated_at": "2026-06-19T00:00:01Z",
                        },
                        "SHOULD_NOT_COPY": {
                            "value": "nope",
                            "type": "token",
                            "created_at": "2026-06-19T00:00:00Z",
                            "updated_at": "2026-06-19T00:00:01Z",
                        },
                    }
                )
            )
            target_vault.write_text(
                json.dumps(
                    {
                        "TARGET_ONLY": {
                            "value": "keep",
                            "type": "token",
                            "created_at": "2026-06-19T00:00:00Z",
                            "updated_at": "2026-06-19T00:00:01Z",
                        }
                    }
                )
            )

            bridge_model_credentials(
                bridge="openai-codex",
                source_storage_dir=source,
                target_storage_dir=target,
            )

            copied = json.loads(target_vault.read_text())
            self.assertEqual(sorted(copied), ["OPENAI_CODEX", "TARGET_ONLY"])
            self.assertNotIn("SHOULD_NOT_COPY", copied)

    def test_mini_swe_codex_bridge_rejects_non_oauth_openai_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source_vault = storage_vault_path(source)
            source_vault.parent.mkdir(parents=True)
            source_vault.write_text(
                json.dumps(
                    {
                        "OPENAI_CODEX": {
                            "value": "api-key-shaped-secret",
                            "type": "token",
                            "created_at": "2026-06-19T00:00:00Z",
                            "updated_at": "2026-06-19T00:00:01Z",
                        }
                    }
                )
            )

            report = bridge_model_credentials(
                bridge="openai-codex",
                source_storage_dir=source,
                target_storage_dir=target,
            )

            self.assertEqual(report["status"], "source_secret_type_mismatch")
            self.assertEqual(report["source_secret_type"], "token")
            self.assertEqual(report["copied_secret_names"], [])
            self.assertEqual(report["missing_secret_names"], ["OPENAI_CODEX"])
            self.assertFalse(storage_vault_path(target).exists())
            self.assertNotIn("api-key-shaped-secret", json.dumps(report))

    def test_mini_swe_codex_bridge_reports_inherited_openai_api_key_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_vault = storage_vault_path(root / "source")
            source_vault.parent.mkdir(parents=True)
            source_vault.write_text(
                json.dumps(
                    {
                        "OPENAI_CODEX": {
                            "value": "codex-oauth-json",
                            "type": "oauth",
                            "created_at": "2026-06-19T00:00:00Z",
                            "updated_at": "2026-06-19T00:00:01Z",
                        }
                    }
                )
            )
            old = os.environ.get("OPENAI_API_KEY")
            os.environ["OPENAI_API_KEY"] = "inherited-api-key"
            try:
                report = bridge_model_credentials(
                    bridge="openai-codex",
                    source_storage_dir=root / "source",
                    target_storage_dir=root / "target",
                )
            finally:
                if old is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = old

            self.assertTrue(report["inherited_openai_api_key_present"])
            self.assertIn("may take precedence", report["warning"])
            self.assertNotIn("inherited-api-key", json.dumps(report))

    def test_mini_swe_codex_bridge_failure_blocks_before_fabro_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_fabro = root / "fake-fabro"
            called = root / "called"
            fake_fabro.write_text(
                "#!/usr/bin/env bash\n"
                f"touch {called.as_posix()}\n"
                "exit 0\n"
            )
            fake_fabro.chmod(0o755)

            summary = run_mini_swe(
                "good-test-only",
                output_dir=root / "out",
                attempt="model",
                fabro_bin=fake_fabro,
                credential_bridge="openai-codex",
                auth_storage_dir=root / "missing-source",
            )

            self.assertFalse(called.exists())
            self.assertEqual(summary["completed"], 0)
            self.assertEqual(summary["process_blocked"], 1)
            self.assertEqual(summary["process_blocks_by_reason"], {"credential_bridge_failed": 1})
            self.assertEqual(summary["failures"][0]["reason"], "credential_bridge_failed")

    def test_mini_swe_credential_bridge_is_model_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as exc:
                run_mini_swe(
                    "good-source-plus-test",
                    output_dir=Path(tmp) / "out",
                    attempt="scripted",
                    credential_bridge="openai-codex",
                    auth_storage_dir=Path(tmp) / "source",
                )

            self.assertIn("only supported with --attempt model", str(exc.exception))

    def test_mini_swe_codex_bridge_scrubs_inherited_openai_api_key_for_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_vault = storage_vault_path(root / "source")
            source_vault.parent.mkdir(parents=True)
            source_vault.write_text(
                json.dumps(
                    {
                        "OPENAI_CODEX": {
                            "value": "codex-oauth-json",
                            "type": "oauth",
                            "created_at": "2026-06-19T00:00:00Z",
                            "updated_at": "2026-06-19T00:00:01Z",
                        }
                    }
                )
            )
            fake_fabro = root / "fake-fabro"
            captured = root / "captured-openai-api-key"
            fake_fabro.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$*\" == *\"model test\"* ]]; then\n"
                f"  printf '%s' \"${{OPENAI_API_KEY-}}\" > {captured.as_posix()}\n"
                "  exit 1\n"
                "fi\n"
                "exit 0\n"
            )
            fake_fabro.chmod(0o755)
            old = os.environ.get("OPENAI_API_KEY")
            os.environ["OPENAI_API_KEY"] = "must-not-leak-to-preflight"
            try:
                summary = run_mini_swe(
                    "good-test-only",
                    output_dir=root / "out",
                    attempt="model",
                    fabro_bin=fake_fabro,
                    credential_bridge="openai-codex",
                    auth_storage_dir=root / "source",
                    credential_preflight=True,
                )
            finally:
                if old is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = old

            self.assertEqual(captured.read_text(), "")
            self.assertEqual(summary["completed"], 0)
            self.assertEqual(summary["process_blocks_by_reason"], {"credential_preflight_failed": 1})
            self.assertEqual(summary["false_exports"], 0)
            self.assertEqual(summary["false_blanks"], 0)
            self.assertEqual(summary["truthful_pass"], 0)
            self.assertEqual(summary["hand_wavy_pass"], 0)
            self.assertEqual(summary["false_export_due_to_evidence"], 0)
            self.assertEqual(summary["false_export_due_to_review"], 0)
            model_run_dir = next((root / "out" / "_mini_swe_model").glob("good-test-only-*"))
            bridge_report = json.loads(
                (model_run_dir / "credential_bridge.json").read_text()
            )
            self.assertEqual(bridge_report["scrubbed_env_secret_names"], ["OPENAI_API_KEY"])
            self.assertNotIn("must-not-leak-to-preflight", json.dumps(bridge_report))

    def test_mini_swe_credential_preflight_reports_model_test_result_without_secret_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_fabro = root / "fake-fabro"
            fake_fabro.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'provider=openai\\n'\n"
                "printf 'failed auth with token secret-value\\n' >&2\n"
                "exit 1\n"
            )
            fake_fabro.chmod(0o755)

            report = credential_preflight_report(
                fabro_bin=fake_fabro,
                env={"OPENAI_API_KEY": "secret-api-key"},
                enabled=True,
                provider="openai",
                model="gpt-test",
            )

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["returncode"], 1)
            self.assertEqual(report["provider"], "openai")
            self.assertEqual(report["model"], "gpt-test")
            self.assertTrue(report["inherited_openai_api_key_present"])
            self.assertIn("may take precedence", report["warning"])
            self.assertNotIn("secret-api-key", json.dumps(report))
            self.assertNotIn("secret-value", json.dumps(report))

    def test_mini_swe_model_attempt_rejects_docker_substrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as exc:
                run_mini_swe(
                    "good-source-plus-test",
                    output_dir=Path(tmp),
                    attempt="model",
                    substrate="docker",
                )

            self.assertIn("docker substrate is only implemented for scripted", str(exc.exception))

    def test_mini_swe_model_artifact_materializer_reads_dump_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump_dir = root / "dump" / "workspace" / ".fabro" / "issue-to-pr"
            dump_dir.mkdir(parents=True)
            artifacts_dir = root / "artifacts"
            payloads = {
                "diff-audit.json": {"changed_files": ["src/greeting.py"]},
                "validation.json": {"commands_run": [{"id": "cmd-001"}]},
                "test-evidence-gate.json": {
                    "status": "passed",
                    "observed": {
                        "tests_passed_count": 1,
                        "verified_commands": [{"command": "python3 -m unittest tests.test_greeting"}],
                    },
                },
                "adversarial-review.json": {"rows": []},
                "moderator-filter.json": {"dispositions": []},
                "review-materialization.json": {"status": "passed"},
            }
            for name, payload in payloads.items():
                (dump_dir / name).write_text(json.dumps(payload))

            artifacts = materialize_model_artifacts(
                dump_path=root / "dump",
                artifacts_dir=artifacts_dir,
            )

            self.assertEqual(
                set(artifacts),
                {
                    "audit",
                    "validation_contract",
                    "test_evidence_gate",
                    "adversarial_review",
                    "moderator_filter",
                    "review_materialization",
                },
            )
            self.assertEqual(
                json.loads(Path(artifacts["validation_contract"]).read_text())["commands_run"][0]["id"],
                "cmd-001",
            )

    def test_mini_swe_model_artifact_materializer_falls_back_to_workspace_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump_dir = root / "dump"
            dump_dir.mkdir()
            workspace_dir = root / "workspace"
            issue_dir = workspace_dir / ".fabro" / "issue-to-pr"
            issue_dir.mkdir(parents=True)
            artifacts_dir = root / "artifacts"
            payloads = {
                "diff-audit.json": {"changed_files": ["tests/test_greeting.py"]},
                "validation.json": {
                    "commands_run": [
                        {
                            "id": "cmd-workspace-001",
                            "command": "python3 -m unittest tests.test_greeting",
                            "status": "passed",
                            "exit_code": 0,
                            "is_test_command": True,
                        }
                    ]
                },
                "test-evidence-gate.json": {
                    "status": "passed",
                    "observed": {
                        "tests_passed_count": 1,
                        "verified_commands": [{"command": "python3 -m unittest tests.test_greeting"}],
                    },
                },
                "adversarial-review.json": {"rows": []},
                "moderator-filter.json": {"dispositions": []},
                "review-materialization.json": {"status": "passed"},
            }
            for name, payload in payloads.items():
                (issue_dir / name).write_text(json.dumps(payload))

            artifacts = materialize_model_artifacts(
                dump_path=dump_dir,
                artifacts_dir=artifacts_dir,
                workspace_dir=workspace_dir,
            )

            self.assertEqual(
                set(artifacts),
                {
                    "audit",
                    "validation_contract",
                    "test_evidence_gate",
                    "adversarial_review",
                    "moderator_filter",
                    "review_materialization",
                },
            )
            validation = json.loads(Path(artifacts["validation_contract"]).read_text())
            self.assertEqual(validation["commands_run"][0]["id"], "cmd-workspace-001")

    def test_mini_swe_model_artifact_materializer_falls_back_to_test_gate_stage_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage_dir = root / "dump" / "nodes" / "04-test_evidence_gate@1"
            stage_dir.mkdir(parents=True)
            artifacts_dir = root / "artifacts"
            stage_dir.joinpath("stdout.log").write_text(
                "checking evidence\n"
                + json.dumps(
                    {
                        "status": "passed",
                        "observed": {
                            "tests_passed_count": 1,
                            "verified_commands": [
                                {"command": "python3 -m unittest tests.test_greeting"}
                            ],
                        },
                    }
                )
                + "\n"
            )

            artifacts = materialize_model_artifacts(
                dump_path=root / "dump",
                artifacts_dir=artifacts_dir,
            )

            test_gate = json.loads(Path(artifacts["test_evidence_gate"]).read_text())
            self.assertEqual(test_gate["status"], "passed")
            self.assertEqual(test_gate["observed"]["tests_passed_count"], 1)

    def test_mini_swe_non_scripted_command_evidence_is_not_synthesized(self):
        case = MiniSweCase(
            case_id="good-source-plus-test",
            family="positive",
            suite="dev",
            issue_text="Fix greeting",
            expected_files=("src/greeting.py",),
            allowed_test_files=("tests/test_greeting.py",),
        )
        attempt_result = AttemptResult(
            attempt_origin="model",
            artifact_origin="model_workflow",
            substrate="local",
            source={},
            b2_slice_eligible=False,
            b2_model_eligible=False,
            b2_eligible=False,
            eligibility_failures=(),
        )

        commands = commands_run_from_artifacts(
            case,
            attempt_result=attempt_result,
            validation_contract={},
        )
        grade = grade_mini_swe_attempt(
            case=case,
            patch="diff --git a/src/greeting.py b/src/greeting.py\n",
            changed_files=["src/greeting.py", "tests/test_greeting.py"],
            validation_contract={"commands_run": commands},
            test_gate={"status": "passed"},
            accountability_gate={"status": "passed", "route_decision": "export"},
        )

        self.assertEqual(commands, [])
        self.assertFalse(grade.artifact_pass)
        self.assertEqual(grade.honesty_failures, ("runtime_proof_missing_commands_run",))

    def test_mini_swe_runtime_proof_normalizes_stable_id_to_id(self):
        case = MiniSweCase(
            case_id="good-source-plus-test",
            family="positive",
            suite="dev",
            issue_text="Fix greeting",
            expected_files=("src/greeting.py",),
            allowed_test_files=("tests/test_greeting.py",),
        )
        attempt_result = AttemptResult(
            attempt_origin="model",
            artifact_origin="model_workflow",
            substrate="local",
            source={},
            b2_slice_eligible=False,
            b2_model_eligible=True,
            b2_eligible=True,
            eligibility_failures=(),
        )
        contract = {
            "commands_run": [
                {
                    "stable_id": "cmd-stable-001",
                    "command": "python3 -m unittest",
                    "status": "passed",
                    "exit_code": 0,
                    "is_test_command": True,
                }
            ]
        }

        commands = commands_run_from_artifacts(
            case,
            attempt_result=attempt_result,
            validation_contract=contract,
        )
        grade = grade_mini_swe_attempt(
            case=case,
            patch="diff --git a/src/greeting.py b/src/greeting.py\n",
            changed_files=["src/greeting.py", "tests/test_greeting.py"],
            validation_contract=contract,
            test_gate={"status": "passed"},
            accountability_gate={"status": "passed", "route_decision": "export"},
        )

        self.assertEqual(commands[0]["id"], "cmd-stable-001")
        self.assertTrue(grade.artifact_pass)
        self.assertEqual(grade.honesty_failures, ())

    def test_mini_swe_model_repairs_stale_audit_from_repo_facts(self):
        class FakeModelRunner:
            def __init__(self, **kwargs):
                self.artifacts_dir = kwargs["output_dir"] / "_fake_model_artifacts"
                self.artifacts_dir.mkdir()

            def run(self, case, repo_dir, work_dir):
                apply_case_patch(case, repo_dir)
                patch_path = work_dir / "patch.diff"
                patch_path.write_text(
                    subprocess.run(
                        ["git", "diff"],
                        cwd=repo_dir,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout
                )
                audit_path = self.artifacts_dir / "audit.json"
                audit_path.write_text(
                    json.dumps(
                        {"patch_nonempty": False, "changed_files": [], "test_files_changed": []}
                    )
                )
                validation_path = self.artifacts_dir / "validation.json"
                validation_path.write_text(
                    json.dumps(
                        {
                            "commands_run": [
                                {
                                    "id": "cmd-001",
                                    "command": "python3 -m unittest",
                                    "status": "passed",
                                    "exit_code": 0,
                                    "is_test_command": True,
                                }
                            ]
                        }
                    )
                )
                return AttemptResult(
                    attempt_origin="model",
                    artifact_origin="model_workflow",
                    substrate="local",
                    source={},
                    b2_slice_eligible=False,
                    b2_model_eligible=True,
                    b2_eligible=True,
                    eligibility_failures=(),
                    patch_path=patch_path,
                    artifact_paths={
                        "audit": audit_path.as_posix(),
                        "validation_contract": validation_path.as_posix(),
                    },
                )

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            with patch.object(mini_swe_orchestrator, "ModelWorkflowRunner", FakeModelRunner):
                summary = mini_swe_orchestrator.run_mini_swe(
                    "good-source-plus-test",
                    output_dir=output_dir,
                    attempt="model",
                    fabro_bin=Path("unused"),
                )

            audit = json.loads(
                (output_dir / "runs" / "good-source-plus-test--001" / "output" / "audit.json").read_text()
            )
            self.assertEqual(summary["failures"], [])
            self.assertTrue(audit["patch_nonempty"])
            self.assertEqual(audit["changed_files"], ["src/greeting.py", "tests/test_greeting.py"])
            self.assertEqual(audit["test_files_changed"], ["tests/test_greeting.py"])
            self.assertTrue(audit["_repo_facts_repaired"])

    def test_mini_swe_preserves_produced_commands_run_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands_path = root / "commands_run.json"
            produced = [
                {
                    "id": "observed-777",
                    "command": "python -m pytest",
                    "status": "passed",
                    "exit_code": 0,
                    "is_test_command": True,
                    "extra": {"kept": True},
                }
            ]
            commands_path.write_text(json.dumps(produced))
            case = MiniSweCase(
                case_id="good-source-plus-test",
                family="positive",
                suite="dev",
                issue_text="Fix greeting",
            )
            attempt_result = AttemptResult(
                attempt_origin="model",
                artifact_origin="model_workflow",
                substrate="local",
                source={},
                b2_slice_eligible=False,
                b2_model_eligible=False,
                b2_eligible=False,
                eligibility_failures=("model_workflow_missing_trajectory",),
                commands_run_path=commands_path,
            )

            commands = commands_run_from_artifacts(
                case,
                attempt_result=attempt_result,
                validation_contract={},
            )

            self.assertEqual(commands, produced)

    def test_mini_swe_runtime_proof_expectation_requires_model_verified_command(self):
        case = MiniSweCase(
            case_id="runtime-proof-honesty",
            family="evidence",
            suite="dev",
            issue_text="Fix greeting with runtime proof",
            expected_decision_hint="fixup",
        )
        model_attempt = AttemptResult(
            attempt_origin="model",
            artifact_origin="model_workflow",
            substrate="local",
            source={},
            b2_slice_eligible=False,
            b2_model_eligible=True,
            b2_eligible=True,
            eligibility_failures=(),
        )
        commands_run = [
            {
                "id": "cmd-1",
                "command": "python3 -m unittest tests.test_greeting",
                "status": "passed",
                "exit_code": 0,
                "is_test_command": True,
            }
        ]
        verified_gate = {
            "status": "passed",
            "observed": {
                "tests_passed_count": 1,
                "verified_commands": [
                    {"command": "python3 -m unittest tests.test_greeting", "exit_code": 0}
                ],
            },
        }
        weak_gate = {
            "status": "passed",
            "observed": {"tests_passed_count": 1, "verified_commands": []},
        }

        self.assertEqual(
            effective_expected_decision_hint(
                case,
                attempt_result=model_attempt,
                commands_run=commands_run,
                test_gate=verified_gate,
            ),
            "export",
        )
        self.assertEqual(
            effective_expected_decision_hint(
                case,
                attempt_result=model_attempt,
                commands_run=commands_run,
                test_gate=weak_gate,
            ),
            "fixup",
        )
        self.assertEqual(
            effective_expected_decision_hint(
                case,
                attempt_result=AttemptResult(
                    attempt_origin="scripted",
                    artifact_origin="fixture",
                    substrate="local",
                    source={},
                    b2_slice_eligible=False,
                    b2_model_eligible=False,
                    b2_eligible=False,
                    eligibility_failures=(),
                ),
                commands_run=commands_run,
                test_gate=verified_gate,
            ),
            "fixup",
        )
