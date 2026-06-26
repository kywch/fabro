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
    prompt_review_summary,
    row_match_failure_reasons,
    row_matches_expected,
    prompt_review_tmp_research,
    run_prompt_review,
    score_adversarial_review,
    score_moderator_filter,
    write_prompt_review_workflow,
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
            if case.expected.get("expected_no_findings"):
                self.assertEqual(case.expected["expected_rows"], [])
                self.assertIn("expected_max_severity", case.expected)
            else:
                self.assertGreater(len(case.expected["expected_rows"]), 0)

    def test_expected_fixture_schema_is_normalized(self):
        stale_artifact_tokens = {
            "commands_run.json",
            "validation_contract.json",
            "test_evidence_gate.json",
            "review_accountability_gate.json",
        }
        for case_dir in list_prompt_review_cases("all"):
            expected = json.loads((case_dir / "expected.json").read_text())
            seen_ids = set()
            for row in expected.get("expected_rows", []):
                self.assertNotIn("category", row, case_dir.name)
                self.assertIsInstance(row.get("categories"), list, case_dir.name)
                self.assertTrue(row["categories"], case_dir.name)
                row_id = row.get("id")
                self.assertNotIn(row_id, seen_ids, case_dir.name)
                seen_ids.add(row_id)
                self.assertTrue(
                    set(row.get("required_path_tokens", [])).isdisjoint(stale_artifact_tokens),
                    case_dir.name,
                )

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

    def test_near_miss_can_use_evidence_without_granting_exact_match(self):
        expected = {
            "expected_rows": [
                {
                    "id": "manual-format-risk",
                    "categories": ["code"],
                    "min_severity": "major",
                    "required_path_tokens": ["sympy/printing/latex.py"],
                    "keyword_groups": [
                        ["manual", "str(poly.as_expr", "replace"],
                        ["Poly", "polynomial"],
                        ["latex", "printer", "format"],
                    ],
                }
            ]
        }
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "Manual formatting risk.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "failure_mode": "Poly output can bypass printer semantics.",
                    "evidence": [
                        "sympy/printing/latex.py",
                        "body = str(poly.as_expr())",
                        "manual textual replacements",
                    ],
                    "falsifiable_check": "Compare a Poly with LaTeX-specific coefficient rendering.",
                    "why_it_matters": "Existing polynomial formatting can regress.",
                }
            ],
            "overall_risk": "medium",
        }

        score = score_adversarial_review(review, expected)

        self.assertEqual(score["matched_expected_ids"], [])
        self.assertEqual(score["near_miss_expected_ids"], ["manual-format-risk"])
        self.assertFalse(score["passed"])

    def test_exact_match_failure_reasons_are_empty_for_matching_row(self):
        expected_row = {
            "id": "dataframe-index-risk",
            "categories": ["scope"],
            "min_severity": "major",
            "required_path_tokens": ["sklearn/utils/validation.py"],
            "keyword_groups": [
                ["DataFrame"],
                ["index"],
                ["non-opt-in", "accept_dataframe"],
            ],
        }
        row = {
            "id": "A1",
            "category": "scope",
            "severity": "major",
            "failure_mode": "DataFrame index handling now bypasses non-opt-in validation.",
            "evidence": ["sklearn/utils/validation.py"],
            "falsifiable_check": "Pass a DataFrame with accept_dataframe=False.",
            "why_it_matters": "The DataFrame index path can broaden accepted inputs.",
        }

        self.assertEqual(row_match_failure_reasons(row, expected_row), [])

    def test_exact_match_failure_reasons_explain_miss_without_changing_score(self):
        expected = {
            "expected_rows": [
                {
                    "id": "upload-permissions-risk",
                    "categories": ["code"],
                    "min_severity": "major",
                    "required_path_tokens": [
                        "django/conf/global_settings.py",
                        "docs/ref/settings.txt",
                    ],
                    "keyword_groups": [
                        ["FILE_UPLOAD_PERMISSIONS"],
                        ["0o644", "permissions"],
                    ],
                }
            ]
        }
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "A lower-severity docs-only row.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "tests",
                    "severity": "minor",
                    "failure_mode": "Docs mention upload permissions.",
                    "evidence": ["docs/ref/settings.txt"],
                    "falsifiable_check": "Inspect docs.",
                    "why_it_matters": "Readers may miss the default.",
                }
            ],
            "overall_risk": "low",
        }

        score = score_adversarial_review(review, expected)

        self.assertEqual(score["matched_expected_ids"], [])
        self.assertEqual(score["row_recall"], 0.0)
        self.assertFalse(score["passed"])
        self.assertEqual(
            score["exact_match_failures"],
            [
                {
                    "expected_id": "upload-permissions-risk",
                    "best_row_id": "A1",
                    "reasons": [
                        {
                            "reason": "category_mismatch",
                            "actual": "tests",
                            "expected": ["code"],
                        },
                        {
                            "reason": "severity_below_minimum",
                            "actual": "minor",
                            "expected_minimum": "major",
                        },
                        {
                            "reason": "missing_path_tokens",
                            "tokens": ["django/conf/global_settings.py"],
                        },
                        {
                            "reason": "missing_keyword_groups",
                            "groups": [["FILE_UPLOAD_PERMISSIONS"]],
                        },
                    ],
                }
            ],
        )

    def test_exact_match_failure_reasons_report_no_candidate_rows(self):
        expected = {
            "expected_rows": [
                {
                    "id": "hidden-risk",
                    "categories": ["code"],
                    "keyword_groups": [["bytes"], ["dispatch"]],
                }
            ]
        }
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "No rows.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [],
            "overall_risk": "low",
        }

        score = score_adversarial_review(review, expected)

        self.assertEqual(
            score["exact_match_failures"],
            [
                {
                    "expected_id": "hidden-risk",
                    "best_row_id": None,
                    "reasons": [{"reason": "no_candidate_rows"}],
                }
            ],
        )
        self.assertEqual(score["row_recall"], 0.0)
        self.assertFalse(score["passed"])

    def test_exact_match_failure_reasons_include_missing_required_fields(self):
        expected = {
            "expected_rows": [
                {
                    "id": "dataframe-index-risk",
                    "categories": ["scope"],
                    "min_severity": "major",
                    "required_path_tokens": ["sklearn/utils/validation.py"],
                    "keyword_groups": [["DataFrame"], ["index"]],
                }
            ]
        }
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "Missing falsifiable check.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "scope",
                    "severity": "major",
                    "failure_mode": "DataFrame index behavior is broadened.",
                    "evidence": ["sklearn/utils/validation.py"],
                    "why_it_matters": "Non-opt-in validation can change.",
                }
            ],
            "overall_risk": "medium",
        }

        score = score_adversarial_review(review, expected)

        self.assertEqual(score["row_recall"], 0.0)
        self.assertFalse(score["passed"])
        self.assertEqual(
            score["exact_match_failures"][0]["reasons"],
            [{"reason": "missing_required_fields", "fields": ["falsifiable_check"]}],
        )

    def test_exact_match_allows_diagnostic_missing_test_path_when_source_path_present(self):
        expected_row = {
            "id": "importlib-origin-risk",
            "categories": ["code"],
            "min_severity": "major",
            "required_path_tokens": [
                "src/_pytest/pathlib.py",
                "testing/acceptance_test.py",
            ],
            "keyword_groups": [
                ["sys.modules"],
                ["origin", "path", "wrong module"],
                ["module name"],
                ["importlib"],
            ],
        }
        row = {
            "id": "A1",
            "category": "code",
            "severity": "major",
            "failure_mode": (
                "import_path(..., mode='importlib') can return a wrong module "
                "when sys.modules contains the computed module name for another path."
            ),
            "evidence": ["src/_pytest/pathlib.py"],
            "falsifiable_check": "Compare the cached module __spec__.origin.",
            "why_it_matters": "The importlib mode can mask the wrong file origin.",
        }

        self.assertTrue(row_matches_expected(row, expected_row))
        self.assertEqual(row_match_failure_reasons(row, expected_row), [])

    def test_exact_match_allows_one_missing_keyword_group_from_evidence(self):
        expected_row = {
            "id": "bytes-diff-risk",
            "categories": ["code"],
            "min_severity": "major",
            "required_path_tokens": ["src/_pytest/assertion/util.py"],
            "keyword_groups": [
                ["str", "string"],
                ["repr", "quotes"],
                ["_diff_text"],
            ],
        }
        row = {
            "id": "A1",
            "category": "code",
            "severity": "major",
            "failure_mode": "Ordinary string assertion diffs can gain repr quotes.",
            "evidence": ["src/_pytest/assertion/util.py: str/str comparisons still use _diff_text"],
            "falsifiable_check": "Compare a focused string assertion diff.",
            "why_it_matters": "String diffs may become harder to read.",
        }

        self.assertTrue(row_matches_expected(row, expected_row))
        self.assertEqual(row_match_failure_reasons(row, expected_row), [])

    def test_exact_match_rejects_multiple_keyword_groups_only_in_evidence(self):
        expected_row = {
            "id": "stuffing-risk",
            "categories": ["code"],
            "min_severity": "major",
            "required_path_tokens": ["src/_pytest/assertion/util.py"],
            "keyword_groups": [
                ["bytes"],
                ["dispatch"],
                ["_diff_text"],
            ],
        }
        row = {
            "id": "A1",
            "category": "code",
            "severity": "major",
            "failure_mode": "A nearby assertion diff concern may regress.",
            "evidence": ["src/_pytest/assertion/util.py bytes dispatch _diff_text"],
            "falsifiable_check": "Inspect the diff.",
            "why_it_matters": "Output readability can regress.",
        }

        self.assertFalse(row_matches_expected(row, expected_row))

    def test_near_miss_normalizes_identifier_separators(self):
        expected = {
            "expected_rows": [
                {
                    "id": "importlib-origin-risk",
                    "categories": ["code"],
                    "min_severity": "major",
                    "required_path_tokens": [
                        "src/_pytest/pathlib.py",
                        "testing/acceptance_test.py",
                    ],
                    "keyword_groups": [
                        ["sys.modules"],
                        ["origin", "path", "wrong module"],
                        ["module name"],
                        ["importlib"],
                    ],
                }
            ]
        }
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "Cached module origin risk.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "failure_mode": (
                        "import_path(..., mode=\"importlib\") can return an unrelated cached module "
                        "when sys.modules already contains the computed module_name for a different file."
                    ),
                    "evidence": ["src/_pytest/pathlib.py"],
                    "falsifiable_check": "Compare the cached module __spec__.origin to the requested path.",
                    "why_it_matters": "A wrong module can mask the file pytest is trying to import.",
                }
            ],
            "overall_risk": "medium",
        }

        score = score_adversarial_review(review, expected)

        self.assertEqual(score["matched_expected_ids"], [])
        self.assertEqual(score["near_miss_expected_ids"], ["importlib-origin-risk"])
        self.assertFalse(score["passed"])

    def test_near_miss_takes_precedence_when_row_mentions_expected_risk(self):
        expected = {
            "expected_rows": [
                {
                    "id": "bytes-dispatch-risk",
                    "categories": ["code"],
                    "keyword_groups": [
                        ["bytes"],
                        ["dispatch"],
                    ],
                }
            ]
        }
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "Bytes dispatch checked.",
            "checked_risks": [
                {
                    "risk": "Bytes could still use the wrong dispatch path.",
                    "evidence": ["pytest_assertrepr_compare"],
                    "counterexample_check": "The focused test passed.",
                }
            ],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "failure_mode": "Adjacent string diff dispatch can regress.",
                    "evidence": ["bytes dispatch implementation is nearby"],
                    "falsifiable_check": "Compare string output.",
                    "why_it_matters": "Diff readability can regress.",
                }
            ],
            "overall_risk": "medium",
        }

        score = score_adversarial_review(review, expected)

        self.assertEqual(score["hidden_expected_ids"], [])
        self.assertEqual(score["near_miss_expected_ids"], ["bytes-dispatch-risk"])

    def test_visible_issue_shaped_row_reports_row_shape_miss(self):
        expected = {
            "expected_rows": [
                {
                    "id": "django-upload-permissions",
                    "categories": ["code", "tests"],
                    "min_severity": "major",
                    "required_path_tokens": [
                        "django/conf/global_settings.py",
                        "docs/ref/settings.txt",
                    ],
                    "keyword_groups": [
                        ["FILE_UPLOAD_PERMISSIONS"],
                        ["0o644", "permissions"],
                        ["TemporaryUploadedFile", "temporary upload"],
                        ["docs", "settings"],
                    ],
                }
            ]
        }
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "Temporary upload gap.",
            "checked_risks": [
                {
                    "risk": "Docs and settings are updated.",
                    "evidence": ["docs/ref/settings.txt", "django/conf/global_settings.py"],
                    "counterexample_check": "The docs name FILE_UPLOAD_PERMISSIONS.",
                }
            ],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "tests",
                    "severity": "major",
                    "failure_mode": (
                        "The TemporaryUploadedFile path is not covered under the new 0o644 "
                        "default permissions behavior."
                    ),
                    "evidence": ["tests/file_storage/tests.py"],
                    "falsifiable_check": "Exercise a temporary upload under a restrictive umask.",
                    "why_it_matters": "Large uploads could keep stale permissions.",
                }
            ],
            "overall_risk": "medium",
        }

        score = score_adversarial_review(review, expected)

        self.assertEqual(score["matched_expected_ids"], [])
        self.assertEqual(score["near_miss_expected_ids"], [])
        self.assertEqual(score["hidden_expected_ids"], [])
        self.assertEqual(
            score["visible_issue_shaped_expected_ids"],
            ["django-upload-permissions"],
        )
        self.assertEqual(score["row_shape_miss_expected_ids"], ["django-upload-permissions"])
        self.assertFalse(score["passed"])

    def test_checked_risk_only_expected_ids_remain_hidden(self):
        expected = {
            "expected_rows": [
                {
                    "id": "hidden-risk",
                    "categories": ["code"],
                    "keyword_groups": [
                        ["bytes"],
                        ["dispatch"],
                    ],
                }
            ]
        }
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "No rows.",
            "checked_risks": [
                {
                    "risk": "Bytes dispatch could still be wrong.",
                    "evidence": ["Focused test passed."],
                    "counterexample_check": "Trust the focused test.",
                }
            ],
            "counterexample_checks": [],
            "rows": [],
            "overall_risk": "low",
        }

        score = score_adversarial_review(review, expected)

        self.assertEqual(score["hidden_expected_ids"], ["hidden-risk"])
        self.assertEqual(score["checked_risk_only_expected_ids"], ["hidden-risk"])
        self.assertEqual(score["visible_issue_shaped_expected_ids"], [])
        self.assertFalse(score["passed"])

    def test_visible_row_shape_miss_does_not_mask_hidden_risk(self):
        expected = {
            "expected_rows": [
                {
                    "id": "split-risk",
                    "categories": ["code"],
                    "keyword_groups": [
                        ["temporary upload"],
                        ["0o644"],
                        ["settings"],
                    ],
                }
            ]
        }
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "Split evidence.",
            "checked_risks": [
                {
                    "risk": "temporary upload could miss 0o644 settings behavior",
                    "evidence": ["Focused test passed."],
                    "counterexample_check": "Trust the focused test.",
                }
            ],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "failure_mode": "The temporary upload path is not covered for 0o644 behavior.",
                    "evidence": ["tests/file_storage/tests.py"],
                    "falsifiable_check": "Exercise a temporary upload.",
                    "why_it_matters": "Large uploads can differ.",
                }
            ],
            "overall_risk": "medium",
        }

        score = score_adversarial_review(review, expected)

        self.assertEqual(score["hidden_expected_ids"], ["split-risk"])
        self.assertEqual(score["visible_issue_shaped_expected_ids"], ["split-risk"])
        self.assertEqual(score["row_shape_miss_expected_ids"], ["split-risk"])
        self.assertEqual(score["checked_risk_only_expected_ids"], [])
        self.assertFalse(score["passed"])

    def test_artifact_blocker_expected_ids_are_diagnostic_only(self):
        expected = {
            "expected_rows": [
                {
                    "id": "semantic-risk",
                    "categories": ["code"],
                    "keyword_groups": [
                        ["dask"],
                        ["lazy"],
                    ],
                }
            ]
        }
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "Syntax blocker.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "blocker",
                    "failure_mode": "xarray/core/concat.py raises SyntaxError before import.",
                    "evidence": ["python3 -m py_compile xarray/core/concat.py"],
                    "falsifiable_check": "Run py_compile.",
                    "why_it_matters": "The semantic behavior cannot be exercised.",
                }
            ],
            "overall_risk": "high",
        }

        score = score_adversarial_review(review, expected)

        self.assertEqual(score["artifact_blocker_expected_ids"], ["semantic-risk"])
        self.assertEqual(score["visible_issue_shaped_expected_ids"], [])
        self.assertEqual(score["row_recall"], 0.0)
        self.assertFalse(score["passed"])

    def test_scorer_accepts_quiet_control_fixture(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "django-sqlmigrate-nontransactional-ddl"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "No issue-scoped blocker found.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [],
            "overall_risk": "low",
        }

        score = score_adversarial_review(review, case.expected)

        self.assertEqual(score["row_recall"], 1.0)
        self.assertEqual(score["unexpected_severity_failures"], [])
        self.assertEqual(score["near_miss_expected_ids"], [])
        self.assertTrue(score["passed"])

    def test_prompt_review_summary_counts_diagnostic_metrics(self):
        results = [
            {
                "review_mode": "adversarial",
                "moderator_score": {"not_applicable": True},
                "score": {
                    "artifact_valid": True,
                    "row_recall": 0.0,
                    "not_hidden": True,
                    "precision": 0.0,
                    "visible_issue_shaped_expected_ids": ["A"],
                    "row_shape_miss_expected_ids": ["A"],
                    "checked_risk_only_expected_ids": [],
                    "artifact_blocker_expected_ids": ["B"],
                },
            },
            {
                "review_mode": "adversarial",
                "moderator_score": {"not_applicable": True},
                "score": {
                    "artifact_valid": True,
                    "row_recall": 1.0,
                    "not_hidden": False,
                    "precision": 1.0,
                    "visible_issue_shaped_expected_ids": [],
                    "row_shape_miss_expected_ids": [],
                    "checked_risk_only_expected_ids": ["C"],
                    "artifact_blocker_expected_ids": [],
                },
            },
        ]

        summary = prompt_review_summary(results, [])

        self.assertEqual(summary["visible_issue_shaped"], 1)
        self.assertEqual(summary["row_shape_miss"], 1)
        self.assertEqual(summary["checked_risk_only"], 1)
        self.assertEqual(summary["artifact_blocker"], 1)

    def test_scorer_rejects_overblocking_control_fixture(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "django-sqlmigrate-nontransactional-ddl"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "Invented blocker.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "failure_mode": "The sqlmigrate patch might still emit transaction wrappers.",
                    "evidence": ["django/core/management/commands/sqlmigrate.py"],
                    "falsifiable_check": "Inspect sqlmigrate output.",
                    "why_it_matters": "This would overblock a fixture marked as a control.",
                }
            ],
            "overall_risk": "high",
        }

        score = score_adversarial_review(review, case.expected)

        self.assertFalse(score["passed"])
        self.assertEqual(
            score["unexpected_severity_failures"][0]["reason"],
            "unexpected_severity_above_fixture_max",
        )

    def test_moderator_scorer_requires_expected_risk_to_stay_open(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "scikit-dataframe-index-broadening"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "failure_mode": "DataFrame index behavior is broadened for non-opt-in validation.",
                    "evidence": ["sklearn/utils/validation.py"],
                    "falsifiable_check": "Pass a pandas DataFrame with accept_dataframe=False.",
                    "why_it_matters": "Non-opt-in validation can silently accept a pandas DataFrame.",
                }
            ],
        }
        moderator = {
            "schema_version": 1,
            "stage": "moderator_filter",
            "dispositions": [
                {
                    "id": "A1",
                    "state": "open",
                    "category": "code",
                    "severity": "major",
                    "evidence": [],
                    "closure_check": "",
                    "reason": "The diff does not directly answer this issue-scoped risk.",
                }
            ],
            "readiness_tier": "needs_fix_code",
            "do_not_repeat": [],
            "next_agent_guidance": "Address the open row.",
        }

        score = score_moderator_filter(review, moderator, case.expected)

        self.assertTrue(score["artifact_valid"])
        self.assertTrue(score["rows_accounted"])
        self.assertEqual(score["expected_open_recall"], 1.0)
        self.assertTrue(score["passed"])

    def test_moderator_scorer_rejects_premature_closure_of_expected_risk(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "scikit-dataframe-index-broadening"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "failure_mode": "DataFrame index behavior is broadened for non-opt-in validation.",
                    "evidence": ["sklearn/utils/validation.py"],
                    "falsifiable_check": "Pass a pandas DataFrame with accept_dataframe=False.",
                    "why_it_matters": "Non-opt-in validation can silently accept a pandas DataFrame.",
                }
            ],
        }
        moderator = {
            "schema_version": 1,
            "stage": "moderator_filter",
            "dispositions": [
                {
                    "id": "A1",
                    "state": "closed_by_evidence",
                    "category": "code",
                    "severity": "major",
                    "evidence": ["validation claim"],
                    "closure_check": "Trust the validation claim.",
                    "reason": "The author claimed it is covered.",
                }
            ],
            "readiness_tier": "ready_verified",
            "do_not_repeat": [],
            "next_agent_guidance": "",
        }

        score = score_moderator_filter(review, moderator, case.expected)

        self.assertEqual(score["expected_non_open_ids"], ["scikit-dataframe-index-broadening"])
        self.assertFalse(score["passed"])

    def test_moderator_scorer_accepts_quiet_control_with_no_rows(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "django-sqlmigrate-nontransactional-ddl"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "rows": [],
        }
        moderator = {
            "schema_version": 1,
            "stage": "moderator_filter",
            "dispositions": [],
            "readiness_tier": "ready_verified",
            "do_not_repeat": [],
            "next_agent_guidance": "Proceed.",
        }

        score = score_moderator_filter(review, moderator, case.expected)

        self.assertTrue(score["artifact_valid"])
        self.assertTrue(score["rows_accounted"])
        self.assertEqual(score["expected_open_recall"], 1.0)
        self.assertTrue(score["passed"])

    def test_prompt_review_workflow_defaults_to_adversarial_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            workflow_path = Path(tmp) / "workflow.fabro"
            write_prompt_review_workflow(workflow_path)
            workflow = workflow_path.read_text()

        self.assertIn("adversarial_review", workflow)
        self.assertNotIn("moderator_filter", workflow)
        self.assertIn("start -> adversarial_review -> exit", workflow)

    def test_prompt_review_workflow_can_run_moderator_after_adversarial_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            workflow_path = Path(tmp) / "workflow.fabro"
            write_prompt_review_workflow(workflow_path, include_moderator=True)
            workflow = workflow_path.read_text()

        self.assertIn("adversarial_review", workflow)
        self.assertIn("moderator_filter", workflow)
        self.assertIn("start -> adversarial_review -> moderator_filter -> exit", workflow)

    def test_tmp_research_context_isolates_and_restores_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tmp_research = root / "fabro-research.md"
            tmp_lock = root / "fabro-research.md.lock"
            tmp_research.write_text("original research")
            case_with_research = root / "with-research"
            (case_with_research / "input").mkdir(parents=True)
            (case_with_research / "input" / "fabro-research.md").write_text("case research")
            case_without_research = root / "without-research"
            (case_without_research / "input").mkdir(parents=True)

            with (
                mock.patch(
                    "fabro_kits.issue_to_pr.light_eval.prompt_review.TMP_RESEARCH_PATH",
                    tmp_research,
                ),
                mock.patch(
                    "fabro_kits.issue_to_pr.light_eval.prompt_review.TMP_RESEARCH_LOCK_PATH",
                    tmp_lock,
                ),
            ):
                with prompt_review_tmp_research(case_with_research):
                    self.assertEqual(tmp_research.read_text(), "case research")
                self.assertEqual(tmp_research.read_text(), "original research")

                with prompt_review_tmp_research(case_without_research):
                    self.assertFalse(tmp_research.exists())
                self.assertEqual(tmp_research.read_text(), "original research")

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

    def test_scorer_reports_near_miss_for_semantic_row_that_is_not_full_match(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "scikit-dataframe-index-broadening"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "The risk is mentioned but not categorized as issue-scoped code risk.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "metadata",
                    "severity": "major",
                    "failure_mode": "DataFrame index behavior is broadened for non-opt-in validation.",
                    "evidence": ["sklearn/utils/validation.py"],
                    "falsifiable_check": "Pass a pandas DataFrame with accept_dataframe=False.",
                    "why_it_matters": "Non-opt-in validation can silently accept a pandas DataFrame.",
                }
            ],
            "overall_risk": "high",
        }

        score = score_adversarial_review(review, case.expected)

        self.assertEqual(score["matched_expected_ids"], [])
        self.assertEqual(score["hidden_expected_ids"], [])
        self.assertEqual(score["near_miss_expected_ids"], ["scikit-dataframe-index-broadening"])
        self.assertEqual(score["row_recall"], 0.0)
        self.assertFalse(score["passed"])

    def test_xarray_concat_expected_rows_require_specific_findings(self):
        case = load_prompt_review_case(
            PROMPT_REVIEW_FIXTURE_ROOT / "xarray-concat-missing-vars-placeholder-matrix"
        )
        review = {
            "schema_version": 1,
            "stage": "adversarial_review",
            "summary": "A vague xarray concat concern.",
            "checked_risks": [],
            "counterexample_checks": [],
            "rows": [
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "failure_mode": "Missing variables use fill_value and may affect coords or dask laziness.",
                    "evidence": ["xarray/core/concat.py", "xarray/tests/test_concat.py"],
                    "falsifiable_check": "Review xarray concat missing variable tests.",
                    "why_it_matters": "A broad missing vars change can have edge cases.",
                }
            ],
            "overall_risk": "high",
        }

        score = score_adversarial_review(review, case.expected)

        self.assertLess(score["row_recall"], 1.0)
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

    def test_prompt_review_default_mode_does_not_require_moderator_artifact(self):
        def fake_run(**kwargs):
            repo_dir = kwargs["workflow_path"].parents[1] / "workspace"
            artifact_dir = repo_dir / ".fabro" / "issue-to-pr"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "adversarial-review.json").write_text(
                json.dumps(_matching_adversarial_review()) + "\n"
            )
            return SimpleNamespace(returncode=0, stdout="Run: run_default\n", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            fabro_bin = Path(tmp) / "fabro"
            fabro_bin.write_text("#!/bin/sh\n")
            with (
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
                    "scikit-dataframe-index-broadening",
                    output_dir=Path(tmp) / "out",
                    fabro_bin=fabro_bin,
                )

        self.assertEqual(report["review_mode"], "adversarial")
        self.assertFalse(report["failures"])
        self.assertEqual(report["row_recall"], 1.0)
        self.assertEqual(report["moderator_prompt_miss"], 0)
        self.assertEqual(report["moderator_artifact_valid"], 0)

    def test_prompt_review_moderated_mode_requires_moderator_artifact(self):
        def fake_run(**kwargs):
            repo_dir = kwargs["workflow_path"].parents[1] / "workspace"
            artifact_dir = repo_dir / ".fabro" / "issue-to-pr"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "adversarial-review.json").write_text(
                json.dumps(_matching_adversarial_review()) + "\n"
            )
            return SimpleNamespace(returncode=0, stdout="Run: run_moderated\n", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            fabro_bin = Path(tmp) / "fabro"
            fabro_bin.write_text("#!/bin/sh\n")
            with (
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
                    "scikit-dataframe-index-broadening",
                    output_dir=Path(tmp) / "out",
                    fabro_bin=fabro_bin,
                    review_mode="moderated",
                )

        self.assertEqual(report["review_mode"], "moderated")
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["failures"][0]["kind"], "moderator_artifact_missing")
        self.assertEqual(report["moderator_prompt_miss"], 0)

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


def _matching_adversarial_review():
    return {
        "schema_version": 1,
        "stage": "adversarial_review",
        "summary": "The patch broadens non-opt-in DataFrame handling.",
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
            }
        ],
        "overall_risk": "high",
    }


if __name__ == "__main__":
    unittest.main()
