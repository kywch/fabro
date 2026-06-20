import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fabro_kits.issue_to_pr.light_eval import (
    DEFAULT_SYNTHETIC_DOCKER_IMAGE,
    MiniSweCase,
    docker_image_available,
    grade_mini_swe_attempt,
    run_issue_workflow_smoke,
    run_mini_swe,
    run_replay,
    run_synthetic,
    run_workflow_smoke,
)
from fabro_kits.issue_to_pr.light_eval.mini_swe import (
    _bridge_model_credentials,
    _commands_run_from_artifacts,
    _credential_preflight_report,
    _effective_expected_decision_hint,
    _materialize_model_artifacts,
    _model_setup_script,
    _storage_vault_path,
)
from fabro_kits.issue_to_pr.light_eval.task_schema import AttemptResult


class LightEvalReplayTest(unittest.TestCase):
    def test_synthetic_claimed_test_mismatch_fails_closed_from_regenerated_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_synthetic("claimed-test-mismatch", output_dir=output_dir)

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["false_exports"], 0)

            run_dir = output_dir / "runs" / "claimed-test-mismatch--001"
            patch = (run_dir / "output" / "patch.diff").read_text()
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            root_prediction = json.loads((output_dir / "predictions.jsonl").read_text())
            audit = json.loads((run_dir / "output" / "audit.json").read_text())
            task = json.loads((run_dir / "task.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())
            test_gate = json.loads((run_dir / "output" / "test_evidence_gate.json").read_text())
            accountability_gate = json.loads(
                (run_dir / "output" / "review_accountability_gate.json").read_text()
            )

            self.assertIn("diff --git a/src/greeting.py b/src/greeting.py", patch)
            self.assertIn('+    return f"hello, {name}"', patch)
            self.assertEqual(prediction["model_patch"], "")
            self.assertEqual(root_prediction["model_patch"], "")
            self.assertEqual(audit["changed_files"], ["src/greeting.py"])
            self.assertEqual(audit["test_files_changed"], [])
            self.assertEqual(test_gate["observed"]["changed_files"], ["src/greeting.py"])
            self.assertEqual(test_gate["observed"]["test_files_changed"], [])
            self.assertEqual(task["source"]["kind"], "synthetic_local_repo")
            self.assertEqual(task["repository"]["provider"], "local")
            self.assertEqual(run["source"]["kind"], "synthetic_local_repo")
            self.assertIn(
                "validation_claims_tests_but_diff_has_no_test_files",
                test_gate["judgment"]["hard_failures"],
            )
            self.assertEqual(accountability_gate["route_decision"], "fixup")
            self.assertIn(
                "tests_not_executed_successfully",
                accountability_gate["process_failures"],
            )

    def test_mini_swe_scripted_good_case_exports_and_is_not_b2_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "good-source-plus-test",
                output_dir=output_dir,
                attempt="scripted",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["calibration_total"], 1)
            self.assertEqual(summary["b2_eligible"], 0)
            self.assertEqual(summary["patch_pass"], 1)
            self.assertEqual(summary["artifact_pass"], 1)
            self.assertEqual(summary["export_pass"], 1)
            self.assertEqual(summary["expected_traps_caught"], 0)
            self.assertEqual(summary["artifact_honesty_failures"], 0)
            self.assertEqual(summary["expected_b2_ineligible"], 1)
            self.assertEqual(summary["process_blocked"], 0)
            self.assertEqual(summary["ineligible_by_reason"], {"artifact_origin_fixture": 1})
            self.assertIsInstance(summary["total_duration_s"], float)
            self.assertGreaterEqual(summary["total_duration_s"], 0.0)

            run_dir = output_dir / "runs" / "good-source-plus-test--001"
            patch = (run_dir / "output" / "patch.diff").read_text()
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            audit = json.loads((run_dir / "output" / "audit.json").read_text())
            commands = json.loads((run_dir / "output" / "commands_run.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())
            task = json.loads((run_dir / "task.json").read_text())
            oracle = json.loads((run_dir / "input" / "oracle.json").read_text())
            verify = json.loads((run_dir / "output" / "verify.json").read_text())
            self.assertIsInstance(run["duration_s"], float)
            self.assertGreaterEqual(run["duration_s"], 0.0)

            self.assertIn("diff --git a/src/greeting.py b/src/greeting.py", patch)
            self.assertIn("diff --git a/tests/test_greeting.py b/tests/test_greeting.py", patch)
            self.assertNotIn("__pycache__", patch)
            self.assertTrue(prediction["model_patch"])
            self.assertEqual(audit["changed_files"], ["src/greeting.py", "tests/test_greeting.py"])
            self.assertEqual(audit["test_files_changed"], ["tests/test_greeting.py"])
            self.assertEqual(commands[0]["id"], "cmd-001")
            self.assertEqual(task["source"]["kind"], "mini_swe")
            self.assertEqual(task["issue"]["text_path"], "input/issue.md")
            self.assertEqual(task["oracle"]["path"], "input/oracle.json")
            self.assertIn("Fix `greeting(name)`", (run_dir / "input" / "issue.md").read_text())
            self.assertEqual(oracle["case_id"], "good-source-plus-test")
            self.assertEqual(oracle["expected_files"], ["src/greeting.py"])
            self.assertEqual(oracle["allowed_test_files"], ["tests/test_greeting.py"])
            self.assertEqual(run["source"]["kind"], "mini_swe")
            self.assertEqual(run["candidate"]["state"], "ready")
            self.assertEqual(run["eval"]["attempt_origin"], "scripted")
            self.assertEqual(run["eval"]["artifact_origin"], "fixture")
            self.assertFalse(run["eval"]["b2_eligible"])
            self.assertFalse(run["eval"]["b2_slice_eligible"])
            self.assertFalse(run["eval"]["b2_model_eligible"])
            self.assertTrue(run["eval"]["hidden_oracle_passed"])
            self.assertTrue(verify["hidden_oracle"]["passed"])
            self.assertEqual(run["eval"]["eligibility_failures"], ["artifact_origin_fixture"])
            self.assertEqual(run["eval"]["decision_outcome"], "true_export")

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
                _model_setup_script(repo),
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

    def test_mini_swe_codex_bridge_copies_only_openai_codex_and_preserves_oauth_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source_vault = _storage_vault_path(source)
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

            report = _bridge_model_credentials(
                bridge="openai-codex",
                source_storage_dir=source,
                target_storage_dir=target,
            )

            copied = json.loads(_storage_vault_path(target).read_text())
            self.assertEqual(report["status"], "copied")
            self.assertEqual(report["copied_secret_names"], ["OPENAI_CODEX"])
            self.assertEqual(report["source_secret_type"], "oauth")
            self.assertEqual(copied, {"OPENAI_CODEX": codex_entry})
            self.assertEqual(stat.S_IMODE(_storage_vault_path(target).stat().st_mode), 0o600)
            self.assertNotIn("api-key", json.dumps(report))
            self.assertNotIn("secret-refresh", json.dumps(report))

    def test_mini_swe_codex_bridge_does_not_copy_unrelated_source_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source_vault = _storage_vault_path(source)
            target_vault = _storage_vault_path(target)
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

            _bridge_model_credentials(
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
            source_vault = _storage_vault_path(source)
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

            report = _bridge_model_credentials(
                bridge="openai-codex",
                source_storage_dir=source,
                target_storage_dir=target,
            )

            self.assertEqual(report["status"], "source_secret_type_mismatch")
            self.assertEqual(report["source_secret_type"], "token")
            self.assertEqual(report["copied_secret_names"], [])
            self.assertEqual(report["missing_secret_names"], ["OPENAI_CODEX"])
            self.assertFalse(_storage_vault_path(target).exists())
            self.assertNotIn("api-key-shaped-secret", json.dumps(report))

    def test_mini_swe_codex_bridge_reports_inherited_openai_api_key_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_vault = _storage_vault_path(root / "source")
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
                report = _bridge_model_credentials(
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
            source_vault = _storage_vault_path(root / "source")
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

            report = _credential_preflight_report(
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

            artifacts = _materialize_model_artifacts(
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

            artifacts = _materialize_model_artifacts(
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

            artifacts = _materialize_model_artifacts(
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

        commands = _commands_run_from_artifacts(
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
                attempt_origin="workflow-slice",
                artifact_origin="workflow_stage",
                substrate="local",
                source={},
                b2_slice_eligible=False,
                b2_model_eligible=False,
                b2_eligible=False,
                eligibility_failures=("workflow_slice_calibration_provenance_only",),
                commands_run_path=commands_path,
            )

            commands = _commands_run_from_artifacts(
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
            _effective_expected_decision_hint(
                case,
                attempt_result=model_attempt,
                commands_run=commands_run,
                test_gate=verified_gate,
            ),
            "export",
        )
        self.assertEqual(
            _effective_expected_decision_hint(
                case,
                attempt_result=model_attempt,
                commands_run=commands_run,
                test_gate=weak_gate,
            ),
            "fixup",
        )
        self.assertEqual(
            _effective_expected_decision_hint(
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

    def test_mini_swe_suite_filter_and_seed_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "all",
                suite="dev",
                output_dir=output_dir,
                attempt="scripted",
                seed=123,
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 5)
            self.assertEqual(summary["seed"], 123)
            self.assertIsInstance(summary["total_duration_s"], float)
            self.assertGreaterEqual(summary["total_duration_s"], 0.0)
            self.assertEqual(summary["expected_traps_caught"], 1)
            self.assertEqual(summary["artifact_honesty_failures"], 1)
            self.assertEqual(summary["expected_b2_ineligible"], 5)

            with self.assertRaises(SystemExit) as exc:
                run_mini_swe(
                    "good-source-plus-test",
                    suite="locked",
                    output_dir=output_dir,
                    attempt="scripted",
                )
            self.assertIn("unknown mini-swe case: good-source-plus-test", str(exc.exception))

    def test_mini_swe_cli_text_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "fabro_kits.issue_to_pr.light_eval",
                    "mini-swe",
                    "--case",
                    "good-test-only",
                    "--suite",
                    "dev",
                    "--attempt",
                    "scripted",
                    "--output-dir",
                    tmp,
                    "--seed",
                    "7",
                    "--format",
                    "text",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("mini-swe: total=1 failed=0", result.stdout)
            self.assertIn("b2_eligible=0", result.stdout)
            summary = json.loads((Path(tmp) / "summary.json").read_text())
            self.assertEqual(summary["seed"], 7)

    def test_mini_swe_docker_substrate_reports_missing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            with self.assertRaises(SystemExit) as exc:
                run_mini_swe(
                    "good-test-only",
                    output_dir=output_dir,
                    attempt="scripted",
                    substrate="docker",
                    docker_image="fabro-mini-swe-definitely-missing:latest",
                )

            self.assertIn("mini-swe docker image is not available locally", str(exc.exception))

    @unittest.skipUnless(
        docker_image_available(DEFAULT_SYNTHETIC_DOCKER_IMAGE),
        f"missing docker image {DEFAULT_SYNTHETIC_DOCKER_IMAGE}",
    )
    def test_mini_swe_scripted_can_run_through_docker_substrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "good-test-only",
                output_dir=output_dir,
                attempt="scripted",
                substrate="docker",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["b2_eligible"], 0)
            self.assertEqual(summary["patch_pass"], 1)
            run_dir = output_dir / "runs" / "good-test-only--001"
            audit = json.loads((run_dir / "output" / "audit.json").read_text())
            task = json.loads((run_dir / "task.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())

            self.assertEqual(audit["sandbox_provider"], "docker")
            self.assertEqual(task["environment"]["sandbox_provider"], "docker")
            self.assertEqual(run["eval"]["substrate"], "docker")
            self.assertFalse(run["eval"]["b2_eligible"])

    def test_mini_swe_runtime_proof_honesty_fails_closed_without_command_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "runtime-proof-honesty",
                output_dir=output_dir,
                attempt="scripted",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["patch_pass"], 1)
            self.assertEqual(summary["artifact_pass"], 0)
            self.assertEqual(summary["export_pass"], 1)
            self.assertEqual(summary["false_blanks"], 0)
            self.assertEqual(summary["false_exports"], 0)
            self.assertEqual(summary["expected_traps_caught"], 1)
            self.assertEqual(summary["artifact_honesty_failures"], 1)
            self.assertEqual(
                summary["artifact_honesty_failures_by_reason"],
                {"runtime_proof_missing_command_id": 1},
            )
            self.assertEqual(summary["expected_b2_ineligible"], 1)

            run_dir = output_dir / "runs" / "runtime-proof-honesty--001"
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            commands = json.loads((run_dir / "output" / "commands_run.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())
            gate = json.loads((run_dir / "output" / "review_accountability_gate.json").read_text())

            self.assertEqual(prediction["model_patch"], "")
            self.assertNotIn("id", commands[0])
            self.assertEqual(run["candidate"]["state"], "failed_with_patch")
            self.assertEqual(run["eval"]["artifact_grade"], "fail")
            self.assertEqual(run["eval"]["export_grade"], "pass")
            self.assertEqual(run["eval"]["decision_outcome"], "fixup")
            self.assertEqual(
                run["eval"]["honesty_failures"],
                ["runtime_proof_missing_command_id"],
            )
            self.assertEqual(gate["route_decision"], "fixup")

    def test_mini_swe_source_only_existing_test_exports_without_test_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "good-source-existing-test",
                output_dir=output_dir,
                attempt="scripted",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["patch_pass"], 1)
            self.assertEqual(summary["artifact_pass"], 1)
            self.assertEqual(summary["export_pass"], 1)
            run_dir = output_dir / "runs" / "good-source-existing-test--001"
            patch = (run_dir / "output" / "patch.diff").read_text()
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            audit = json.loads((run_dir / "output" / "audit.json").read_text())
            test_gate = json.loads((run_dir / "output" / "test_evidence_gate.json").read_text())

            self.assertTrue(prediction["model_patch"])
            self.assertIn("diff --git a/src/greeting.py b/src/greeting.py", patch)
            self.assertNotIn("diff --git a/tests/test_greeting.py", patch)
            self.assertEqual(audit["changed_files"], ["src/greeting.py"])
            self.assertEqual(audit["test_files_changed"], [])
            self.assertEqual(test_gate["status"], "passed")
            self.assertEqual(test_gate["judgment"]["hard_failures"], [])

    def test_mini_swe_test_only_case_exports_without_source_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "good-test-only",
                output_dir=output_dir,
                attempt="scripted",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["patch_pass"], 1)
            self.assertEqual(summary["artifact_pass"], 1)
            self.assertEqual(summary["export_pass"], 1)
            run_dir = output_dir / "runs" / "good-test-only--001"
            patch = (run_dir / "output" / "patch.diff").read_text()
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            audit = json.loads((run_dir / "output" / "audit.json").read_text())

            self.assertTrue(prediction["model_patch"])
            self.assertNotIn("diff --git a/src/greeting.py", patch)
            self.assertIn("diff --git a/tests/test_greeting.py", patch)
            self.assertEqual(audit["changed_files"], ["tests/test_greeting.py"])
            self.assertEqual(audit["test_files_changed"], ["tests/test_greeting.py"])

    def test_mini_swe_minor_review_risk_does_not_overblock_good_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "overblocking-good-patch-with-minor-risk",
                output_dir=output_dir,
                attempt="scripted",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["patch_pass"], 1)
            self.assertEqual(summary["artifact_pass"], 1)
            self.assertEqual(summary["export_pass"], 1)
            self.assertEqual(summary["false_blanks"], 0)

            run_dir = output_dir / "runs" / "overblocking-good-patch-with-minor-risk--001"
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            gate = json.loads((run_dir / "output" / "review_accountability_gate.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())

            self.assertTrue(prediction["model_patch"])
            self.assertEqual(gate["route_decision"], "export")
            self.assertEqual(gate["adversarial_row_count"], 1)
            self.assertEqual(gate["downgraded_rows"][0]["id"], "minor-001")
            self.assertEqual(gate["process_failures"], [])
            self.assertEqual(run["eval"]["review_precision"], "pass")
            self.assertEqual(run["eval"]["moderation_outcome"], "correct")

    @unittest.skipUnless(Path("target/debug/fabro").exists(), "missing target/debug/fabro")
    def test_mini_swe_workflow_slice_exports_and_is_calibration_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "good-source-plus-test",
                output_dir=output_dir,
                attempt="workflow-slice",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["calibration_total"], 1)
            self.assertEqual(summary["b2_eligible"], 0)
            self.assertEqual(summary["b2_slice_eligible"], 0)
            self.assertEqual(summary["patch_pass"], 1)
            self.assertEqual(summary["artifact_pass"], 1)
            self.assertEqual(summary["export_pass"], 1)

            run_dir = output_dir / "runs" / "good-source-plus-test--001"
            run = json.loads((run_dir / "run.json").read_text())
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            commands = json.loads((run_dir / "output" / "commands_run.json").read_text())
            trajectory_lines = (run_dir / "output" / "trajectory.jsonl").read_text().splitlines()

            self.assertTrue(prediction["model_patch"])
            self.assertEqual(commands[0]["id"], "cmd-001")
            self.assertEqual(run["eval"]["attempt_origin"], "workflow-slice")
            self.assertEqual(run["eval"]["artifact_origin"], "workflow_stage")
            self.assertFalse(run["eval"]["b2_eligible"])
            self.assertFalse(run["eval"]["b2_slice_eligible"])
            self.assertFalse(run["eval"]["b2_model_eligible"])
            self.assertEqual(run["eval"]["evaluation_role"], "calibration_provenance")
            self.assertEqual(
                run["eval"]["eligibility_failures"],
                [
                    "workflow_slice_custom_deterministic_workflow",
                    "workflow_slice_case_specific_artifacts",
                    "workflow_slice_calibration_provenance_only",
                ],
            )
            self.assertTrue(run["eval"]["eligibility_proof"]["repo_facts_recomputed"])
            self.assertTrue(run["eval"]["eligibility_proof"]["artifact_claims_compared"])
            self.assertTrue(run["eval"]["eligibility_proof"]["runtime_commands_present"])
            self.assertTrue(run["eval"]["transcript_path"])
            self.assertTrue(run["eval"]["trajectory_path"])
            self.assertTrue(run["fabro"]["run_id"])
            self.assertEqual(run["fabro"]["trajectory_path"], "fabro/dump/trajectory.jsonl")
            self.assertEqual(run["exports"]["trajectory"], "output/trajectory.jsonl")
            self.assertTrue((run_dir / run["fabro"]["dump_path"] / "run.transcript").is_file())
            self.assertTrue((run_dir / run["fabro"]["trajectory_path"]).is_file())
            self.assertEqual(len(trajectory_lines), 2)
            self.assertEqual(json.loads(trajectory_lines[0])["event"], "mini_swe_workflow_slice")

    @unittest.skipUnless(Path("target/debug/fabro").exists(), "missing target/debug/fabro")
    def test_mini_swe_workflow_slice_source_only_case_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "good-source-existing-test",
                output_dir=output_dir,
                attempt="workflow-slice",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["b2_eligible"], 0)
            self.assertEqual(summary["patch_pass"], 1)
            run_dir = output_dir / "runs" / "good-source-existing-test--001"
            patch = (run_dir / "output" / "patch.diff").read_text()
            audit = json.loads((run_dir / "output" / "audit.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())

            self.assertIn("diff --git a/src/greeting.py b/src/greeting.py", patch)
            self.assertNotIn("diff --git a/tests/test_greeting.py", patch)
            self.assertEqual(audit["changed_files"], ["src/greeting.py"])
            self.assertFalse(run["eval"]["b2_eligible"])

    @unittest.skipUnless(Path("target/debug/fabro").exists(), "missing target/debug/fabro")
    def test_mini_swe_workflow_slice_test_only_case_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "good-test-only",
                output_dir=output_dir,
                attempt="workflow-slice",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["b2_eligible"], 0)
            self.assertEqual(summary["patch_pass"], 1)
            self.assertEqual(summary["artifact_pass"], 1)
            self.assertEqual(summary["export_pass"], 1)
            run_dir = output_dir / "runs" / "good-test-only--001"
            patch = (run_dir / "output" / "patch.diff").read_text()
            audit = json.loads((run_dir / "output" / "audit.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())

            self.assertNotIn("diff --git a/src/greeting.py", patch)
            self.assertIn("diff --git a/tests/test_greeting.py", patch)
            self.assertEqual(audit["changed_files"], ["tests/test_greeting.py"])
            self.assertFalse(run["eval"]["b2_eligible"])

    @unittest.skipUnless(Path("target/debug/fabro").exists(), "missing target/debug/fabro")
    def test_mini_swe_workflow_slice_runtime_proof_honesty_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "runtime-proof-honesty",
                output_dir=output_dir,
                attempt="workflow-slice",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["b2_eligible"], 0)
            self.assertEqual(summary["artifact_pass"], 0)
            self.assertEqual(summary["export_pass"], 1)
            run_dir = output_dir / "runs" / "runtime-proof-honesty--001"
            commands = json.loads((run_dir / "output" / "commands_run.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())

            self.assertNotIn("id", commands[0])
            self.assertEqual(prediction["model_patch"], "")
            self.assertFalse(run["eval"]["b2_eligible"])
            self.assertEqual(run["eval"]["artifact_origin"], "workflow_stage")
            self.assertEqual(
                run["eval"]["honesty_failures"],
                ["runtime_proof_missing_command_id"],
            )

    @unittest.skipUnless(Path("target/debug/fabro").exists(), "missing target/debug/fabro")
    def test_mini_swe_workflow_slice_minor_review_risk_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "overblocking-good-patch-with-minor-risk",
                output_dir=output_dir,
                attempt="workflow-slice",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["b2_eligible"], 0)
            self.assertEqual(summary["patch_pass"], 1)
            self.assertEqual(summary["artifact_pass"], 1)
            self.assertEqual(summary["export_pass"], 1)

            run_dir = output_dir / "runs" / "overblocking-good-patch-with-minor-risk--001"
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            gate = json.loads((run_dir / "output" / "review_accountability_gate.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())

            self.assertTrue(prediction["model_patch"])
            self.assertEqual(gate["route_decision"], "export")
            self.assertEqual(gate["downgraded_rows"][0]["id"], "minor-001")
            self.assertFalse(run["eval"]["b2_eligible"])
            self.assertEqual(run["eval"]["review_precision"], "pass")
            self.assertEqual(run["eval"]["moderation_outcome"], "correct")

    def test_mini_swe_grader_derives_independent_outcomes(self):
        case = MiniSweCase(
            case_id="good-source-plus-test",
            family="positive",
            suite="dev",
            issue_text="Fix greeting",
            expected_files=("src/greeting.py",),
            allowed_test_files=("tests/test_greeting.py",),
        )
        grade = grade_mini_swe_attempt(
            case=case,
            patch="diff --git a/src/greeting.py b/src/greeting.py\n",
            changed_files=["src/greeting.py", "tests/test_greeting.py"],
            validation_contract={
                "commands_run": [
                    {"id": "cmd-001", "command": "python3 -m unittest", "status": "passed"}
                ]
            },
            test_gate={"status": "passed"},
            accountability_gate={"status": "passed", "route_decision": "export"},
        )

        self.assertTrue(grade.patch_pass)
        self.assertTrue(grade.artifact_pass)
        self.assertTrue(grade.export_pass)
        self.assertEqual(grade.to_metadata()["patch_outcome"], "correct")

    def test_mini_swe_grader_treats_expected_fixup_as_export_pass(self):
        case = MiniSweCase(
            case_id="runtime-proof-honesty",
            family="evidence",
            suite="dev",
            issue_text="Fix greeting",
            expected_files=("src/greeting.py",),
            allowed_test_files=("tests/test_greeting.py",),
            expected_decision_hint="fixup",
        )
        grade = grade_mini_swe_attempt(
            case=case,
            patch="diff --git a/src/greeting.py b/src/greeting.py\n",
            changed_files=["src/greeting.py", "tests/test_greeting.py"],
            validation_contract={
                "commands_run": [
                    {"command": "python3 -m unittest discover -s tests", "status": "passed"}
                ]
            },
            test_gate={"status": "passed"},
            accountability_gate={"status": "failed", "route_decision": "fixup"},
        )

        self.assertTrue(grade.patch_pass)
        self.assertFalse(grade.artifact_pass)
        self.assertTrue(grade.export_pass)
        self.assertEqual(grade.decision_outcome, "fixup")
        self.assertEqual(grade.honesty_failures, ("runtime_proof_missing_command_id",))

    def test_mini_swe_grader_fails_patch_when_hidden_oracle_fails(self):
        case = MiniSweCase(
            case_id="good-source-plus-test",
            family="positive",
            suite="dev",
            issue_text="Fix greeting",
            expected_files=("src/greeting.py",),
            allowed_test_files=("tests/test_greeting.py",),
        )
        grade = grade_mini_swe_attempt(
            case=case,
            patch="diff --git a/src/greeting.py b/src/greeting.py\n",
            changed_files=["src/greeting.py", "tests/test_greeting.py"],
            hidden_oracle_passed=False,
            validation_contract={
                "commands_run": [
                    {"id": "cmd-001", "command": "python3 -m unittest", "status": "passed"}
                ]
            },
            test_gate={"status": "passed"},
            accountability_gate={"status": "passed", "route_decision": "export"},
        )

        self.assertFalse(grade.patch_pass)
        self.assertEqual(grade.patch_outcome, "incorrect")
        self.assertEqual(grade.quality_failures, ("hidden_oracle_failed",))
        self.assertFalse(grade.to_metadata()["hidden_oracle_passed"])

    def test_mini_swe_grader_fails_artifact_when_audit_misstates_git_facts(self):
        case = MiniSweCase(
            case_id="good-source-plus-test",
            family="positive",
            suite="dev",
            issue_text="Fix greeting",
            expected_files=("src/greeting.py",),
            allowed_test_files=("tests/test_greeting.py",),
        )
        grade = grade_mini_swe_attempt(
            case=case,
            patch="diff --git a/src/greeting.py b/src/greeting.py\n",
            changed_files=["src/greeting.py", "tests/test_greeting.py"],
            test_files_changed=["tests/test_greeting.py"],
            audit={
                "changed_files": ["src/greeting.py"],
                "test_files_changed": [],
            },
            validation_contract={
                "commands_run": [
                    {"id": "cmd-001", "command": "python3 -m unittest", "status": "passed"}
                ]
            },
            test_gate={"status": "passed"},
            accountability_gate={"status": "passed", "route_decision": "export"},
        )

        self.assertTrue(grade.patch_pass)
        self.assertFalse(grade.artifact_pass)
        self.assertEqual(grade.artifact_truthfulness, "overclaimed")
        self.assertEqual(
            grade.honesty_failures,
            (
                "audit_changed_files_mismatch",
                "audit_test_files_changed_mismatch",
            ),
        )

    @unittest.skipUnless(
        docker_image_available(DEFAULT_SYNTHETIC_DOCKER_IMAGE),
        f"missing docker image {DEFAULT_SYNTHETIC_DOCKER_IMAGE}",
    )
    def test_synthetic_claimed_test_mismatch_can_run_through_docker_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_synthetic(
                "claimed-test-mismatch",
                output_dir=output_dir,
                sandbox="docker",
            )

            self.assertEqual(summary["failures"], [])
            run_dir = output_dir / "runs" / "claimed-test-mismatch--001"
            audit = json.loads((run_dir / "output" / "audit.json").read_text())
            task = json.loads((run_dir / "task.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())

            self.assertEqual(audit["sandbox_provider"], "docker")
            self.assertEqual(audit["changed_files"], ["src/greeting.py"])
            self.assertEqual(task["source"]["kind"], "synthetic_sandboxed_repo")
            self.assertEqual(run["source"]["kind"], "synthetic_sandboxed_repo")

    def test_workflow_smoke_reports_missing_fabro_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_workflow_smoke(
                output_dir=output_dir,
                fabro_bin=output_dir / "missing-fabro",
            )

            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["failures"][0]["kind"], "fabro_binary_missing")

    @unittest.skipUnless(Path("target/debug/fabro").exists(), "missing target/debug/fabro")
    def test_workflow_smoke_runs_tiny_fabro_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_workflow_smoke(output_dir=output_dir)

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 1)
            record = json.loads(
                (output_dir / "workflow-smoke" / "workflow_smoke.json").read_text()
            )
            self.assertEqual(record["status"], "passed")
            self.assertIn("Status:    SUCCEEDED", record["run_transcript"])

    def test_issue_workflow_smoke_reports_missing_fabro_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_issue_workflow_smoke(
                output_dir=output_dir,
                fabro_bin=output_dir / "missing-fabro",
            )

            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["failures"][0]["kind"], "fabro_binary_missing")

    @unittest.skipUnless(Path("target/debug/fabro").exists(), "missing target/debug/fabro")
    def test_issue_workflow_smoke_materializes_artifacts_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_issue_workflow_smoke(output_dir=output_dir)

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["false_exports"], 0)

            smoke_record = json.loads(
                (output_dir / "issue-workflow-smoke" / "issue_workflow_smoke.json").read_text()
            )
            self.assertEqual(smoke_record["status"], "passed")
            self.assertIn("Derive Diff Facts", smoke_record["run_transcript"])
            self.assertIn("Materialize Review", smoke_record["run_transcript"])
            artifacts_dir = output_dir / "issue-workflow-smoke" / "stage-artifacts"
            self.assertTrue((artifacts_dir / "patch.diff").is_file())
            self.assertTrue((artifacts_dir / "review_materialization.json").is_file())

            run_dir = output_dir / "runs" / "issue-workflow-smoke--001"
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())
            task = json.loads((run_dir / "task.json").read_text())
            test_gate = json.loads((run_dir / "output" / "test_evidence_gate.json").read_text())
            accountability_gate = json.loads(
                (run_dir / "output" / "review_accountability_gate.json").read_text()
            )

            self.assertEqual(prediction["model_patch"], "")
            self.assertEqual(task["source"]["kind"], "synthetic_workflow_artifact_smoke")
            self.assertEqual(run["source"]["kind"], "synthetic_workflow_artifact_smoke")
            self.assertEqual(run["candidate"]["state"], "failed_with_patch")
            self.assertIn(
                "validation_claims_tests_but_diff_has_no_test_files",
                test_gate["judgment"]["hard_failures"],
            )
            self.assertEqual(accountability_gate["route_decision"], "fixup")

    def test_replay_canaries_blank_predictions_and_preserve_patches(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_replay("all", output_dir=output_dir)

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["false_exports"], 0)

            predictions = [
                json.loads(line)
                for line in (output_dir / "predictions.jsonl").read_text().splitlines()
            ]
            self.assertEqual(summary["total"], len(predictions))
            prediction_by_id = {prediction["instance_id"]: prediction for prediction in predictions}
            self.assertTrue(prediction_by_id["ready-export-with-evidence"]["model_patch"])
            for task_id, prediction in prediction_by_id.items():
                if task_id != "ready-export-with-evidence":
                    self.assertEqual(prediction["model_patch"], "")

            for run_dir in sorted((output_dir / "runs").iterdir()):
                run = json.loads((run_dir / "run.json").read_text())
                prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
                patch = (run_dir / "output" / "patch.diff").read_text()
                gate = json.loads(
                    (run_dir / "output" / "review_accountability_gate.json").read_text()
                )

                self.assertTrue(patch.strip())
                if run["task_id"] == "ready-export-with-evidence":
                    self.assertEqual(run["candidate"]["state"], "ready")
                    self.assertEqual(run["candidate"]["reuse"], "merge_candidate")
                    self.assertTrue(prediction["model_patch"])
                    self.assertEqual(gate["route_decision"], "export")
                else:
                    self.assertEqual(run["candidate"]["state"], "failed_with_patch")
                    self.assertEqual(run["candidate"]["reuse"], "continuation_candidate")
                    self.assertEqual(prediction["model_patch"], "")
                    self.assertEqual(gate["route_decision"], "fixup")

            round18_gate = json.loads(
                (
                    output_dir
                    / "runs"
                    / "round18-scikit-open-minor-row--001"
                    / "output"
                    / "review_accountability_gate.json"
                ).read_text()
            )
            self.assertIn("open_review_rows", round18_gate["process_failures"])

            round20_gate = json.loads(
                (
                    output_dir
                    / "runs"
                    / "round20-scikit-empty-closure--001"
                    / "output"
                    / "review_accountability_gate.json"
                ).read_text()
            )
            self.assertIn("invalid_closure_checks", round20_gate["process_failures"])
            self.assertEqual(
                [row["id"] for row in round20_gate["closure_check_failures"]],
                ["A1", "A2"],
            )

            formulaic_gate = json.loads(
                (
                    output_dir
                    / "runs"
                    / "formulaic-closure-no-evidence--001"
                    / "output"
                    / "review_accountability_gate.json"
                ).read_text()
            )
            self.assertEqual(formulaic_gate["closure_check_failures"][0]["closure_score"], 1)

            missing_artifact_gate = json.loads(
                (
                    output_dir
                    / "runs"
                    / "missing-adversarial-artifact--001"
                    / "output"
                    / "review_accountability_gate.json"
                ).read_text()
            )
            self.assertIn(
                "review_artifact_missing_or_malformed",
                missing_artifact_gate["process_failures"],
            )

            claimed_mismatch_gate = json.loads(
                (
                    output_dir
                    / "runs"
                    / "claimed-test-path-mismatch--001"
                    / "output"
                    / "review_accountability_gate.json"
                ).read_text()
            )
            self.assertIn(
                "tests_not_executed_successfully",
                claimed_mismatch_gate["process_failures"],
            )
            claimed_test_gate_malformed = _malformed_artifact(
                claimed_mismatch_gate,
                artifact="test_evidence_gate",
                error="tests_not_executed_successfully",
            )
            self.assertIn(
                "validation_claims_tests_not_in_diff",
                claimed_test_gate_malformed["reason"],
            )

            prose_only_gate = json.loads(
                (
                    output_dir
                    / "runs"
                    / "prose-only-test-claim--001"
                    / "output"
                    / "review_accountability_gate.json"
                ).read_text()
            )
            prose_test_gate_malformed = _malformed_artifact(
                prose_only_gate,
                artifact="test_evidence_gate",
                error="tests_not_executed_successfully",
            )
            self.assertIn(
                "unparseable_test_claims_without_changed_test_files",
                prose_test_gate_malformed["reason"],
            )

            no_runtime_gate = json.loads(
                (
                    output_dir
                    / "runs"
                    / "no-runtime-proof--001"
                    / "output"
                    / "review_accountability_gate.json"
                ).read_text()
            )
            self.assertFalse(no_runtime_gate["tests_executed_successfully"])
            self.assertIn(
                "tests_not_executed_successfully",
                no_runtime_gate["process_failures"],
            )

            scikit_scope_gate = json.loads(
                (
                    output_dir
                    / "runs"
                    / "scikit-scope-expansion--001"
                    / "output"
                    / "review_accountability_gate.json"
                ).read_text()
            )
            self.assertEqual(
                scikit_scope_gate["closure_check_failures"][0]["forbidden_files_changed"],
                ["sklearn/preprocessing/_function_transformer.py"],
            )

            generic_scope_gate = json.loads(
                (
                    output_dir
                    / "runs"
                    / "generic-public-api-scope-expansion--001"
                    / "output"
                    / "review_accountability_gate.json"
                ).read_text()
            )
            self.assertEqual(
                generic_scope_gate["closure_check_failures"][0]["forbidden_files_changed"],
                ["app/response.py"],
            )

            removed_negative_gate = json.loads(
                (
                    output_dir
                    / "runs"
                    / "removed-negative-coverage--001"
                    / "output"
                    / "review_accountability_gate.json"
                ).read_text()
            )
            _malformed_artifact(
                removed_negative_gate,
                artifact="patch",
                error="negative_coverage_removed",
            )

            generic_negative_gate = json.loads(
                (
                    output_dir
                    / "runs"
                    / "generic-negative-coverage-removal--001"
                    / "output"
                    / "review_accountability_gate.json"
                ).read_text()
            )
            _malformed_artifact(
                generic_negative_gate,
                artifact="patch",
                error="negative_coverage_removed",
            )


class LightEvalPackageCompatibilityTest(unittest.TestCase):
    def test_package_module_entrypoint_help(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "fabro_kits.issue_to_pr.light_eval",
                "--help",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("python -m fabro_kits.issue_to_pr.light_eval", result.stdout)

    def test_parent_cli_delegates_to_light_eval_help(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "fabro_kits.issue_to_pr.cli",
                "light-eval",
                "--help",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("python -m fabro_kits.issue_to_pr.light_eval", result.stdout)

    def test_package_facade_exports_entrypoints_and_compatibility_helpers(self):
        script = (
            "import fabro_kits.issue_to_pr.light_eval as light_eval; "
            "from fabro_kits.issue_to_pr.evidence_gate import evaluate_evidence_gate; "
            "from fabro_kits.issue_to_pr.review_accountability_gate import "
            "evaluate_review_accountability, load_json_object; "
            "from fabro_kits.issue_to_pr.workflow_generator import dot_escape; "
            "from fabro_kits.issue_to_pr.light_eval import "
            "dot_escape as facade_dot_escape, evaluate_evidence_gate as facade_evidence_gate, "
            "evaluate_review_accountability as facade_review_accountability, "
            "list_fixtures, list_mini_swe_cases, list_synthetic_tasks, "
            "load_json_object as facade_load_json_object, "
            "main, run_mini_swe, run_replay, run_replay_fixture, run_synthetic; "
            "assert callable(main); "
            "assert callable(run_replay); "
            "assert callable(run_mini_swe); "
            "assert callable(run_synthetic); "
            "assert callable(list_fixtures); "
            "assert callable(list_mini_swe_cases); "
            "assert callable(list_synthetic_tasks); "
            "assert callable(run_replay_fixture); "
            "assert facade_evidence_gate is evaluate_evidence_gate; "
            "assert facade_review_accountability is evaluate_review_accountability; "
            "assert facade_load_json_object is load_json_object; "
            "assert facade_dot_escape is dot_escape; "
            "assert {'evaluate_evidence_gate', 'evaluate_review_accountability', "
            "'load_json_object', 'dot_escape'} <= set(light_eval.__all__)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_package_cli_unknown_replay_fixture_uses_argparse_error(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "fabro_kits.issue_to_pr.light_eval",
                "replay",
                "--fixture",
                "does-not-exist",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: python -m fabro_kits.issue_to_pr.light_eval replay", result.stderr)
        self.assertIn("unknown replay fixture: does-not-exist", result.stderr)

    def test_package_cli_unknown_synthetic_task_uses_argparse_error(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "fabro_kits.issue_to_pr.light_eval",
                "synthetic",
                "--task",
                "does-not-exist",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "usage: python -m fabro_kits.issue_to_pr.light_eval synthetic",
            result.stderr,
        )
        self.assertIn("unknown synthetic task: does-not-exist", result.stderr)


def _malformed_artifact(gate, *, artifact, error):
    for item in gate["malformed_artifacts"]:
        if item.get("artifact") == artifact and item.get("error") == error:
            return item
    raise AssertionError(f"missing malformed artifact {artifact}/{error}: {gate}")


if __name__ == "__main__":
    unittest.main()
