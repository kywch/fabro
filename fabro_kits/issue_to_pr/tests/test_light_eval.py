import json
import tempfile
import unittest
from pathlib import Path

from fabro_kits.issue_to_pr.light_eval import run_replay


class LightEvalReplayTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
