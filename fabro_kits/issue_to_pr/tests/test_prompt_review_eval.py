import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fabro_kits.issue_to_pr.light_eval.prompt_review import (
    PROMPT_REVIEW_FIXTURE_ROOT,
    list_prompt_review_cases,
    load_prompt_review_case,
    prepare_prompt_review_workspace,
    run_prompt_review,
    score_adversarial_review,
)


class PromptReviewEvalTest(unittest.TestCase):

    def test_loader_finds_round_06_derived_cases_without_validation_oracle(self):
        case_dirs = list_prompt_review_cases("all")
        case_names = {path.name for path in case_dirs}

        self.assertIn("sympy-poly-latex-manual-formatter-risk", case_names)
        self.assertIn("scikit-dataframe-index-broadening", case_names)
        self.assertIn("sphinx-escaped-starargs-issue-shape-gap", case_names)

        for case_dir in case_dirs:
            case = load_prompt_review_case(case_dir)
            self.assertNotIn("expected_review_rows", case.validation)
            self.assertNotIn("expected_rows", case.validation)
            self.assertGreater(len(case.expected["expected_rows"]), 0)

    def test_scorer_counts_recalled_row(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "scikit-dataframe-index-broadening"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "The patch broadens non-opt-in DataFrame handling.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "scope",
                    "severity": "major",
                    "failure_mode": "DataFrame index presence now bypasses the non-opt-in accept_dataframe rejection.",
                    "evidence": ["sklearn/utils/validation.py"],
                    "falsifiable_check": "Pass a pandas DataFrame with accept_dataframe=False.",
                    "why_it_matters": "This broadens non-opt-in pandas DataFrame validation behavior.",
                }
            ],
            "overall_risk": "high",
        }

        score = score_adversarial_review(review, case.expected)

        self.assertTrue(score["artifact_valid"])
        self.assertEqual(score["row_recall"], 1.0)
        self.assertTrue(score["not_hidden"])
        self.assertTrue(score["passed"])
        self.assertTrue(score["precision_pass"])

    def test_scorer_rejects_keyword_stuffing_outside_semantic_fields(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "scikit-dataframe-index-broadening"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "Keyword stuffing should not pass.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "failure_mode": "A validation edge case may be wrong.",
                    "evidence": [
                        "sklearn/utils/validation.py DataFrame pandas index broaden non-opt-in accept_dataframe"
                    ],
                    "falsifiable_check": "Inspect the changed file.",
                    "why_it_matters": "Users may see changed behavior.",
                }
            ],
            "overall_risk": "high",
        }

        score = score_adversarial_review(review, case.expected)

        self.assertEqual(score["row_recall"], 0.0)
        self.assertFalse(score["passed"])

    def test_scorer_rejects_rows_missing_required_fields(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "scikit-dataframe-index-broadening"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "Missing why_it_matters should fail closed.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "failure_mode": "DataFrame index behavior is broadened for non-opt-in validation.",
                    "evidence": ["sklearn/utils/validation.py"],
                    "falsifiable_check": "Pass a pandas DataFrame with accept_dataframe=False.",
                }
            ],
            "overall_risk": "high",
        }

        score = score_adversarial_review(review, case.expected)

        self.assertEqual(score["row_recall"], 0.0)
        self.assertFalse(score["passed"])

    def test_fixture_workspace_prep_applies_patch_and_writes_artifacts(self):
        case_dir = PROMPT_REVIEW_FIXTURE_ROOT / "sympy-poly-latex-manual-formatter-risk"
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "repo"
            prepare_prompt_review_workspace(case_dir, repo_dir)

            self.assertIn("str(poly.as_expr())", (repo_dir / "sympy/printing/latex.py").read_text())
            self.assertTrue((repo_dir / ".fabro/issue-to-pr/validation.json").exists())
            self.assertTrue((repo_dir / ".fabro/issue-to-pr/diff-audit.json").exists())
            self.assertTrue((repo_dir / ".fabro/issue-to-pr/test-evidence-gate.json").exists())

    def test_scorer_rejects_hidden_risk_in_checked_risks(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "sphinx-escaped-starargs-issue-shape-gap"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "No rows.",
            "checked_risks": [
                {
                    "risk": "A global replace may unescape ordinary literal escaped starargs text.",
                    "evidence": ["sphinx/ext/autodoc/importer.py"],
                    "counterexample_check": "The replace is limited enough.",
                }
            ],
            "counterexample_checks": [],
            "rows": [],
            "overall_risk": "low",
        }

        score = score_adversarial_review(review, case.expected)

        self.assertEqual(score["row_recall"], 0.0)
        self.assertFalse(score["not_hidden"])
        self.assertEqual(score["hidden_expected_ids"], ["sphinx-starargs-shape-gap"])
        self.assertFalse(score["passed"])

    def test_scorer_rejects_invented_major_changelog_row(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "scikit-dataframe-index-broadening"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "One real row and one invented metadata blocker.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "failure_mode": "DataFrame index behavior is broadened for non-opt-in validation.",
                    "evidence": ["sklearn/utils/validation.py"],
                    "falsifiable_check": "Pass a pandas DataFrame with accept_dataframe=False.",
                    "why_it_matters": "Non-opt-in validation can silently accept a pandas DataFrame.",
                },
                {
                    "id": "A2",
                    "category": "metadata",
                    "severity": "major",
                    "failure_mode": "Missing changelog entry blocks release.",
                    "evidence": ["No release note was added."],
                    "falsifiable_check": "Check changelog.",
                    "why_it_matters": "Release metadata would be incomplete if the issue required it.",
                },
            ],
            "overall_risk": "high",
        }

        score = score_adversarial_review(review, case.expected)

        self.assertEqual(score["row_recall"], 1.0)
        self.assertFalse(score["precision_pass"])
        self.assertFalse(score["passed"])

    def test_missing_fabro_binary_returns_process_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_prompt_review(
                "sympy-poly-latex-manual-formatter-risk",
                output_dir=Path(tmp),
                fabro_bin=Path(tmp) / "missing-fabro",
            )
            summary = json.loads((Path(tmp) / "summary.json").read_text())
            self.assertTrue(
                (
                    Path(tmp)
                    / "sympy-poly-latex-manual-formatter-risk"
                    / "output"
                    / "patch.diff"
                )
                .read_text()
                .startswith("diff --git")
            )

            self.assertEqual(report["total"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["process_failed"], 1)
            self.assertEqual(summary["prompt_miss"], 0)
            self.assertEqual(report["failures"][0]["kind"], "fabro_binary_missing")

    def test_prompt_review_cli_missing_fabro_binary_returns_process_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "out"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "fabro_kits.issue_to_pr.light_eval",
                    "prompt-review",
                    "--case",
                    "sympy-poly-latex-manual-formatter-risk",
                    "--output-dir",
                    str(output_dir),
                    "--fabro-bin",
                    str(Path(tmp) / "missing-fabro"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertEqual(report["process_failed"], 1)
            self.assertEqual(report["prompt_miss"], 0)
            self.assertEqual(report["failures"][0]["kind"], "fabro_binary_missing")

    def test_openai_codex_bridge_uses_openai_provider_for_preflight_and_run(self):
        captured = {"preflight_provider": None, "run_provider": None}

        def fake_preflight(**kwargs):
            captured["preflight_provider"] = kwargs["provider"]
            return {"status": "passed"}

        def fake_run(**kwargs):
            captured["run_provider"] = kwargs["provider"]
            return SimpleNamespace(returncode=1, stdout="", stderr="synthetic failure")

        with tempfile.TemporaryDirectory() as tmp:
            fabro_bin = Path(tmp) / "fabro"
            fabro_bin.write_text("#!/bin/sh\n")
            with (
                mock.patch(
                    "fabro_kits.issue_to_pr.light_eval.prompt_review.bridge_model_credentials",
                    return_value={"status": "copied"},
                ),
                mock.patch(
                    "fabro_kits.issue_to_pr.light_eval.prompt_review.credential_preflight_report",
                    side_effect=fake_preflight,
                ),
                mock.patch(
                    "fabro_kits.issue_to_pr.light_eval.prompt_review._run_prompt_review_workflow",
                    side_effect=fake_run,
                ),
                mock.patch(
                    "fabro_kits.issue_to_pr.light_eval.prompt_review.run_fabro_command",
                    return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
                ),
            ):
                report = run_prompt_review(
                    "sympy-poly-latex-manual-formatter-risk",
                    output_dir=Path(tmp) / "out",
                    fabro_bin=fabro_bin,
                    credential_bridge="openai-codex",
                    credential_preflight=True,
                )

        self.assertEqual(captured["preflight_provider"], "openai")
        self.assertEqual(captured["run_provider"], "openai")
        self.assertEqual(report["process_failed"], 1)
        self.assertEqual(report["prompt_miss"], 0)


class PromptReviewLiveModelTest(unittest.TestCase):

    @unittest.skipUnless(
        Path("target/debug/fabro").exists(),
        "target/debug/fabro is not available",
    )
    def test_live_model_prompt_review_smoke_if_credentials_available(self):
        import os

        if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
            self.skipTest("model credentials are not available")
        with tempfile.TemporaryDirectory() as tmp:
            report = run_prompt_review(
                "sympy-poly-latex-manual-formatter-risk",
                output_dir=Path(tmp),
                fabro_bin=Path("target/debug/fabro"),
            )
        self.assertEqual(report["total"], 1)
        self.assertFalse(report["failures"])


if __name__ == "__main__":
    unittest.main()
