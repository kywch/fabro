import subprocess
import sys
import unittest


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
