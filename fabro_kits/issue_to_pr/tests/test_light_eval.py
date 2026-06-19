import json
import tempfile
import unittest
from pathlib import Path

from fabro_kits.issue_to_pr.light_eval import (
    DEFAULT_SYNTHETIC_DOCKER_IMAGE,
    docker_image_available,
    run_replay,
    run_synthetic,
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
            self.assertEqual(task["source"]["kind"], "synthetic_sandboxed_workflow")
            self.assertEqual(run["source"]["kind"], "synthetic_sandboxed_workflow")

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


def _malformed_artifact(gate, *, artifact, error):
    for item in gate["malformed_artifacts"]:
        if item.get("artifact") == artifact and item.get("error") == error:
            return item
    raise AssertionError(f"missing malformed artifact {artifact}/{error}: {gate}")


if __name__ == "__main__":
    unittest.main()
