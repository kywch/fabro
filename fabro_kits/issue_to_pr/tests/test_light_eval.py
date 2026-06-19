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
            self.assertEqual(summary["total"], 2)

            predictions = [
                json.loads(line)
                for line in (output_dir / "predictions.jsonl").read_text().splitlines()
            ]
            self.assertEqual([prediction["model_patch"] for prediction in predictions], ["", ""])

            for run_id in (
                "round18-scikit-open-minor-row--001",
                "round20-scikit-empty-closure--001",
            ):
                run_dir = output_dir / "runs" / run_id
                run = json.loads((run_dir / "run.json").read_text())
                prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
                patch = (run_dir / "output" / "patch.diff").read_text()
                gate = json.loads(
                    (run_dir / "output" / "review_accountability_gate.json").read_text()
                )

                self.assertEqual(run["candidate"]["state"], "failed_with_patch")
                self.assertEqual(run["candidate"]["reuse"], "continuation_candidate")
                self.assertEqual(prediction["model_patch"], "")
                self.assertTrue(patch.strip())
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


if __name__ == "__main__":
    unittest.main()
