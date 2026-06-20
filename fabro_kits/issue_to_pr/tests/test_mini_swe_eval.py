import json
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
    run_mini_swe,
)
from fabro_kits.issue_to_pr.light_eval.mini_swe.evidence import (
    commands_run_from_artifacts,
)
from fabro_kits.issue_to_pr.light_eval.task_schema import AttemptResult


class MiniSweEvalTest(unittest.TestCase):

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
