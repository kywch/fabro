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
    run_issue_workflow_smoke,
    run_mini_swe,
    run_replay,
    run_synthetic,
    run_workflow_smoke,
)


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
            self.assertEqual(summary["ineligible_by_reason"], {"artifact_origin_fixture": 1})

            run_dir = output_dir / "runs" / "good-source-plus-test--001"
            patch = (run_dir / "output" / "patch.diff").read_text()
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            audit = json.loads((run_dir / "output" / "audit.json").read_text())
            commands = json.loads((run_dir / "output" / "commands_run.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())
            task = json.loads((run_dir / "task.json").read_text())

            self.assertIn("diff --git a/src/greeting.py b/src/greeting.py", patch)
            self.assertIn("diff --git a/tests/test_greeting.py b/tests/test_greeting.py", patch)
            self.assertNotIn("__pycache__", patch)
            self.assertTrue(prediction["model_patch"])
            self.assertEqual(audit["changed_files"], ["src/greeting.py", "tests/test_greeting.py"])
            self.assertEqual(audit["test_files_changed"], ["tests/test_greeting.py"])
            self.assertEqual(commands[0]["id"], "cmd-001")
            self.assertEqual(task["source"]["kind"], "mini_swe")
            self.assertEqual(run["source"]["kind"], "mini_swe")
            self.assertEqual(run["candidate"]["state"], "ready")
            self.assertEqual(run["eval"]["attempt_origin"], "scripted")
            self.assertEqual(run["eval"]["artifact_origin"], "fixture")
            self.assertFalse(run["eval"]["b2_eligible"])
            self.assertFalse(run["eval"]["b2_slice_eligible"])
            self.assertFalse(run["eval"]["b2_model_eligible"])
            self.assertEqual(run["eval"]["eligibility_failures"], ["artifact_origin_fixture"])
            self.assertEqual(run["eval"]["decision_outcome"], "true_export")

    def test_mini_swe_unimplemented_attempt_uses_argparse_error(self):
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
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: python -m fabro_kits.issue_to_pr.light_eval mini-swe", result.stderr)
        self.assertIn("mini-swe attempt not implemented yet: model", result.stderr)

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
    def test_mini_swe_workflow_slice_exports_and_is_b2_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            summary = run_mini_swe(
                "good-source-plus-test",
                output_dir=output_dir,
                attempt="workflow-slice",
            )

            self.assertEqual(summary["failures"], [])
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["calibration_total"], 0)
            self.assertEqual(summary["b2_eligible"], 1)
            self.assertEqual(summary["b2_slice_eligible"], 1)
            self.assertEqual(summary["patch_pass"], 1)
            self.assertEqual(summary["artifact_pass"], 1)
            self.assertEqual(summary["export_pass"], 1)

            run_dir = output_dir / "runs" / "good-source-plus-test--001"
            run = json.loads((run_dir / "run.json").read_text())
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            commands = json.loads((run_dir / "output" / "commands_run.json").read_text())

            self.assertTrue(prediction["model_patch"])
            self.assertEqual(commands[0]["id"], "cmd-001")
            self.assertEqual(run["eval"]["attempt_origin"], "workflow-slice")
            self.assertEqual(run["eval"]["artifact_origin"], "workflow_stage")
            self.assertTrue(run["eval"]["b2_eligible"])
            self.assertTrue(run["eval"]["b2_slice_eligible"])
            self.assertFalse(run["eval"]["b2_model_eligible"])
            self.assertEqual(run["eval"]["eligibility_failures"], [])
            self.assertTrue(run["eval"]["transcript_path"])
            self.assertTrue(run["fabro"]["run_id"])
            self.assertTrue((run_dir / run["fabro"]["dump_path"] / "run.transcript").is_file())

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
            self.assertEqual(summary["b2_eligible"], 1)
            self.assertEqual(summary["patch_pass"], 1)
            run_dir = output_dir / "runs" / "good-source-existing-test--001"
            patch = (run_dir / "output" / "patch.diff").read_text()
            audit = json.loads((run_dir / "output" / "audit.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())

            self.assertIn("diff --git a/src/greeting.py b/src/greeting.py", patch)
            self.assertNotIn("diff --git a/tests/test_greeting.py", patch)
            self.assertEqual(audit["changed_files"], ["src/greeting.py"])
            self.assertTrue(run["eval"]["b2_eligible"])

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
            self.assertEqual(summary["b2_eligible"], 1)
            self.assertEqual(summary["artifact_pass"], 0)
            self.assertEqual(summary["export_pass"], 1)
            run_dir = output_dir / "runs" / "runtime-proof-honesty--001"
            commands = json.loads((run_dir / "output" / "commands_run.json").read_text())
            run = json.loads((run_dir / "run.json").read_text())
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())

            self.assertNotIn("id", commands[0])
            self.assertEqual(prediction["model_patch"], "")
            self.assertTrue(run["eval"]["b2_eligible"])
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
            self.assertEqual(summary["b2_eligible"], 1)
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
            self.assertTrue(run["eval"]["b2_eligible"])
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
