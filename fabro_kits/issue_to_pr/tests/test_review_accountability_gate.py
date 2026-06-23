import ast
import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from fabro_kits.issue_to_pr.review_accountability_gate import (
    build_embedded_accountability_gate_script,
    evaluate_review_accountability,
)


class ReviewAccountabilityGateTest(unittest.TestCase):
    def test_open_minor_row_fails_export(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A2",
                        "category": "tests",
                        "severity": "minor",
                        "required_files": ["tests/test_label.py"],
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A2",
                        "state": "open",
                        "category": "tests",
                        "severity": "minor",
                        "reason": "Still unresolved.",
                    }
                ]
            ),
            test_gate=_test_gate(),
            materialization=_materialization(["A2"], ["A2"]),
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("open_review_rows", report["process_failures"])
        self.assertEqual(report["blocking_rows"][0]["id"], "A2")

    def test_empty_closure_checks_fail(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {"id": "A1", "category": "code", "severity": "minor"},
                    {"id": "A2", "category": "tests", "severity": "minor"},
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "code",
                        "severity": "minor",
                        "evidence": ["diff hunk"],
                        "closure_check": "",
                    },
                    {
                        "id": "A2",
                        "state": "closed_by_evidence",
                        "category": "tests",
                        "severity": "minor",
                        "evidence": ["test hunk"],
                        "closure_check": "",
                    },
                ]
            ),
            test_gate=_test_gate(),
            materialization=_materialization(["A1", "A2"], ["A1", "A2"]),
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn("invalid_closure_checks", report["process_failures"])
        self.assertEqual(
            [row["id"] for row in report["closure_check_failures"]],
            ["A1", "A2"],
        )

    def test_formulaic_closure_without_artifact_evidence_fails(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "tests",
                        "severity": "minor",
                        "required_files": ["tests/test_widget.py"],
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "tests",
                        "severity": "minor",
                        "evidence": ["tests pass"],
                        "closure_check": "Issue-scoped and tests pass.",
                    }
                ]
            ),
            test_gate=_test_gate(changed_files=["src/widget.py", "tests/test_widget.py"]),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertIn("invalid_closure_checks", report["process_failures"])
        self.assertEqual(report["closure_check_failures"][0]["closure_score"], 1)
        self.assertEqual(
            report["closure_check_failures"][0]["closure_score_reason"],
            "closure has no concrete artifact evidence",
        )

    def test_bare_diff_word_does_not_make_formulaic_minor_closure_safe(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "tests",
                        "severity": "minor",
                        "required_files": ["tests/test_widget.py"],
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "tests",
                        "severity": "minor",
                        "evidence": ["tests pass"],
                        "closure_check": "Verified the diff.",
                    }
                ]
            ),
            test_gate=_test_gate(changed_files=["src/widget.py", "tests/test_widget.py"]),
            materialization=_materialization(["A1"], ["A1"]),
            patch_diff="diff --git a/src/widget.py b/src/widget.py\n+ok\n",
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("invalid_closure_checks", report["process_failures"])
        self.assertEqual(report["closure_check_failures"][0]["closure_score"], 1)

    def test_major_closure_must_cite_all_required_files_for_score_three(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "code",
                        "severity": "major",
                        "required_files": ["src/a.py", "src/b.py"],
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "code",
                        "severity": "major",
                        "evidence": ["src/a.py"],
                        "closure_check": "Verified src/a.py changed.",
                    }
                ]
            ),
            test_gate=_test_gate(changed_files=["src/a.py", "src/b.py", "tests/test_fix.py"]),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("invalid_closure_checks", report["process_failures"])
        self.assertEqual(report["closure_check_failures"][0]["closure_score"], 2)

    def test_cited_evidence_counts_for_closure_scoring(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "code",
                        "severity": "major",
                        "required_files": ["src/fix.py"],
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "code",
                        "severity": "major",
                        "cited_evidence": ["src/fix.py"],
                        "closure_check": "Verified required implementation file.",
                    }
                ]
            ),
            test_gate=_test_gate(changed_files=["src/fix.py", "tests/test_fix.py"]),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertEqual(report["route_decision"], "export")
        self.assertEqual(report["closed_rows"][0]["closure_score"], 3)

    def test_gate_does_not_mutate_moderator_artifact(self):
        moderator = _moderator(
            dispositions=[
                {
                    "id": "A1",
                    "state": "closed_by_evidence",
                    "category": "tests",
                    "severity": "minor",
                    "evidence": ["tests pass"],
                    "closure_check": "Verified the diff.",
                }
            ]
        )
        evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "tests",
                        "severity": "minor",
                        "required_files": ["tests/test_widget.py"],
                    }
                ]
            ),
            moderator=moderator,
            test_gate=_test_gate(changed_files=["src/widget.py", "tests/test_widget.py"]),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertNotIn("closure_score", moderator["dispositions"][0])
        self.assertNotIn("missing_required_files", moderator["dispositions"][0])

    def test_missing_required_file_fails_severe_closure(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "code",
                        "severity": "major",
                        "required_files": ["src/required.py"],
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "code",
                        "severity": "major",
                        "evidence": ["src/other.py"],
                        "closure_check": "Checked diff.",
                    }
                ]
            ),
            test_gate=_test_gate(changed_files=["src/other.py"]),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertIn("invalid_closure_checks", report["process_failures"])
        self.assertEqual(
            report["closure_check_failures"][0]["missing_required_files"],
            ["src/required.py"],
        )

    def test_forbidden_changed_file_blocks_scope_expansion_closure(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "scope",
                        "severity": "major",
                        "required_files": ["src/request.py"],
                        "forbidden_files": ["src/response.py"],
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "scope",
                        "severity": "major",
                        "evidence": ["src/request.py", "src/response.py"],
                        "closure_check": "Verified src/request.py changed.",
                    }
                ]
            ),
            test_gate=_test_gate(changed_files=["src/request.py", "src/response.py"]),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("invalid_closure_checks", report["process_failures"])
        self.assertEqual(
            report["closure_check_failures"][0]["forbidden_files_changed"],
            ["src/response.py"],
        )

    def test_removed_negative_coverage_blocks_export(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(changed_files=["tests/test_parser.py"]),
            materialization=_materialization([], []),
            patch_diff=(
                "diff --git a/tests/test_parser.py b/tests/test_parser.py\n"
                "--- a/tests/test_parser.py\n"
                "+++ b/tests/test_parser.py\n"
                "@@ -1,4 +1,2 @@\n"
                "-def test_none_rejected():\n"
                "-    with pytest.raises(ValueError):\n"
                "-        parse(None)\n"
                "+def test_empty_ok():\n"
                "+    assert parse('') is None\n"
            ),
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("patch_malformed_or_scope_drift", report["process_failures"])
        self.assertEqual(report["malformed_artifacts"][0]["error"], "negative_coverage_removed")

    def test_replaced_negative_coverage_does_not_block_export(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(changed_files=["tests/test_parser.py"]),
            materialization=_materialization([], []),
            patch_diff=(
                "diff --git a/tests/test_parser.py b/tests/test_parser.py\n"
                "--- a/tests/test_parser.py\n"
                "+++ b/tests/test_parser.py\n"
                "@@ -1,4 +1,4 @@\n"
                "-def test_none_rejected():\n"
                "-    with pytest.raises(ValueError):\n"
                "-        parse(None)\n"
                "+def test_none_still_rejected():\n"
                "+    with pytest.raises(TypeError):\n"
                "+        parse(None)\n"
            ),
        )

        self.assertEqual(report["route_decision"], "export")

    def test_unrelated_same_file_negative_replacement_does_not_excuse_removal(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(changed_files=["tests/test_parser.py"]),
            materialization=_materialization([], []),
            patch_diff=(
                "diff --git a/tests/test_parser.py b/tests/test_parser.py\n"
                "--- a/tests/test_parser.py\n"
                "+++ b/tests/test_parser.py\n"
                "@@ -1,6 +1,6 @@\n"
                "-def test_none_rejected():\n"
                "-    with pytest.raises(ValueError):\n"
                "-        parse(None)\n"
                "+def test_empty_rejected():\n"
                "+    with pytest.raises(ValueError):\n"
                "+        parse('')\n"
            ),
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("patch_malformed_or_scope_drift", report["process_failures"])

    def test_replacement_in_other_file_does_not_excuse_removed_negative_coverage(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(changed_files=["tests/test_parser.py", "tests/test_other.py"]),
            materialization=_materialization([], []),
            patch_diff=(
                "diff --git a/tests/test_parser.py b/tests/test_parser.py\n"
                "--- a/tests/test_parser.py\n"
                "+++ b/tests/test_parser.py\n"
                "@@ -1,3 +1,1 @@\n"
                "-def test_none_rejected():\n"
                "-    with pytest.raises(ValueError):\n"
                "-        parse(None)\n"
                "diff --git a/tests/test_other.py b/tests/test_other.py\n"
                "--- a/tests/test_other.py\n"
                "+++ b/tests/test_other.py\n"
                "@@ -1,1 +1,3 @@\n"
                "+def test_other_error():\n"
                "+    with pytest.raises(ValueError):\n"
                "+        other(None)\n"
            ),
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("patch_malformed_or_scope_drift", report["process_failures"])

    def test_deleted_test_file_negative_coverage_blocks_export(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(changed_files=["tests/test_parser.py"]),
            materialization=_materialization([], []),
            patch_diff=(
                "diff --git a/tests/test_parser.py b/tests/test_parser.py\n"
                "deleted file mode 100644\n"
                "--- a/tests/test_parser.py\n"
                "+++ /dev/null\n"
                "@@ -1,3 +0,0 @@\n"
                "-def test_none_rejected():\n"
                "-    with pytest.raises(ValueError):\n"
                "-        parse(None)\n"
            ),
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("patch_malformed_or_scope_drift", report["process_failures"])

    def test_source_raises_line_removal_is_not_negative_test_coverage(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[], checked_risks=[{"risk": "source behavior change", "evidence": ["src/parser.py"], "counterexample_check": "src/parser.py diff changes only the source return path"}]),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(changed_files=["src/parser.py"]),
            materialization=_materialization([], []),
            patch_diff=(
                "diff --git a/src/parser.py b/src/parser.py\n"
                "--- a/src/parser.py\n"
                "+++ b/src/parser.py\n"
                "@@ -1,3 +1,2 @@\n"
                "-    raise ValueError('bad input')\n"
                "+    return None\n"
            ),
        )

        self.assertEqual(report["route_decision"], "export")

    def test_empty_rows_fail_for_nontrivial_source_diff_without_checked_risks(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(changed_files=["src/parser.py"]),
            materialization=_materialization([], []),
            patch_diff=(
                "diff --git a/src/parser.py b/src/parser.py\n"
                "--- a/src/parser.py\n"
                "+++ b/src/parser.py\n"
                "@@ -1,2 +1,2 @@\n"
                "-    return None\n"
                "+    return value\n"
            ),
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("review_artifact_missing_or_malformed", report["process_failures"])
        self.assertEqual(report["malformed_artifacts"][0]["error"], "empty_rows_without_checked_risks")

    def test_empty_rows_with_checked_risks_keep_zero_rows_and_export(self):
        for key in ("checked_risks", "counterexample_checks"):
            with self.subTest(key=key):
                report = evaluate_review_accountability(
                    adversarial=_adversarial(rows=[], **{key: [{"risk": "source behavior change", "check": "reviewed src/parser.py counterexample path", "evidence": ["src/parser.py"]}]}),
                    moderator=_moderator(dispositions=[]),
                    test_gate=_test_gate(changed_files=["src/parser.py"]),
                    materialization=_materialization([], []),
                    patch_diff=(
                        "diff --git a/src/parser.py b/src/parser.py\n"
                        "--- a/src/parser.py\n"
                        "+++ b/src/parser.py\n"
                        "@@ -1,2 +1,2 @@\n"
                        "-    return None\n"
                        "+    return value\n"
                    ),
                )

                self.assertEqual(report["route_decision"], "export")
                self.assertEqual(report["adversarial_row_count"], 0)
                self.assertEqual(report["moderator_disposition_count"], 0)

    def test_empty_rows_fail_when_plausible_risk_hidden_in_checked_risks(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[],
                checked_risks=[
                    {
                        "risk": "src/parser.py could regress empty input handling",
                        "evidence": ["src/parser.py"],
                        "counterexample_check": "possible issue remains unresolved",
                    }
                ],
            ),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(changed_files=["src/parser.py"]),
            materialization=_materialization([], []),
            patch_diff=(
                "diff --git a/src/parser.py b/src/parser.py\n"
                "--- a/src/parser.py\n"
                "+++ b/src/parser.py\n"
                "@@ -1,2 +1,2 @@\n"
                "-    return None\n"
                "+    return value\n"
            ),
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("review_artifact_missing_or_malformed", report["process_failures"])
        self.assertEqual(report["malformed_artifacts"][0]["error"], "plausible_risk_hidden_in_checked_risks")

    def test_empty_rows_broad_semantic_change_requires_option_matrix(self):
        report = _empty_review_with_scope(
            {
                "literal_issue_fixed": True,
                "scope_narrowed_reason": "",
                "broad_semantic_change": True,
                "option_matrix": [],
                "residual_risk": "none",
            }
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("review_artifact_missing_or_malformed", report["process_failures"])
        self.assertEqual(report["malformed_artifacts"][0]["error"], "missing_option_matrix")

    def test_empty_rows_broad_semantic_change_requires_option_matrix_list(self):
        report = _empty_review_with_scope(
            {
                "literal_issue_fixed": True,
                "scope_narrowed_reason": "",
                "broad_semantic_change": True,
                "option_matrix": "checked one option",
                "residual_risk": "none",
            }
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertEqual(report["malformed_artifacts"][0]["error"], "missing_option_matrix")

    def test_empty_rows_broad_semantic_change_with_option_matrix_exports(self):
        report = _empty_review_with_scope(
            {
                "literal_issue_fixed": True,
                "scope_narrowed_reason": "",
                "broad_semantic_change": True,
                "option_matrix": ["changed only the requested parser branch"],
                "residual_risk": "none",
            }
        )

        self.assertEqual(report["route_decision"], "export")

    def test_empty_rows_literal_issue_not_fixed_requires_reason(self):
        report = _empty_review_with_scope(
            {
                "literal_issue_fixed": False,
                "scope_narrowed_reason": "",
                "broad_semantic_change": False,
                "option_matrix": [],
                "residual_risk": "none",
            }
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("review_artifact_missing_or_malformed", report["process_failures"])
        self.assertEqual(report["malformed_artifacts"][0]["error"], "missing_scope_narrowed_reason")

    def test_empty_rows_literal_issue_not_fixed_requires_text_reason(self):
        report = _empty_review_with_scope(
            {
                "literal_issue_fixed": False,
                "scope_narrowed_reason": True,
                "broad_semantic_change": False,
                "option_matrix": [],
                "residual_risk": "none",
            }
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertEqual(report["malformed_artifacts"][0]["error"], "missing_scope_narrowed_reason")

    def test_empty_rows_literal_issue_not_fixed_with_reason_exports(self):
        report = _empty_review_with_scope(
            {
                "literal_issue_fixed": False,
                "scope_narrowed_reason": "Issue text was scoped to the explicit parser branch.",
                "broad_semantic_change": False,
                "option_matrix": [],
                "residual_risk": "none",
            }
        )

        self.assertEqual(report["route_decision"], "export")

    def test_empty_rows_narrow_scope_assessment_exports(self):
        report = _empty_review_with_scope(
            {
                "literal_issue_fixed": True,
                "scope_narrowed_reason": "",
                "broad_semantic_change": False,
                "option_matrix": [],
                "residual_risk": "none",
            }
        )

        self.assertEqual(report["route_decision"], "export")

    def test_scope_assessment_is_ignored_when_rows_are_nonempty(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[{"id": "A1", "category": "code", "severity": "minor"}],
                scope_assessment={
                    "literal_issue_fixed": False,
                    "scope_narrowed_reason": "",
                    "broad_semantic_change": True,
                    "option_matrix": [],
                    "residual_risk": "none",
                },
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "rejected",
                        "category": "code",
                        "severity": "minor",
                        "evidence": ["src/parser.py"],
                        "closure_check": "Rejected against src/parser.py evidence.",
                    }
                ]
            ),
            test_gate=_test_gate(changed_files=["src/parser.py"]),
            materialization=_materialization(["A1"], ["A1"]),
            patch_diff=_source_patch_diff(),
        )

        self.assertNotIn(
            "missing_scope_narrowed_reason",
            [item.get("error") for item in report["malformed_artifacts"]],
        )
        self.assertNotIn(
            "missing_option_matrix",
            [item.get("error") for item in report["malformed_artifacts"]],
        )

    def test_runtime_closure_requires_machine_observed_test_execution(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "tests",
                        "severity": "major",
                        "closure_requires": "runtime_tests",
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "tests",
                        "severity": "major",
                        "evidence": ["commands_reported_passed_count=1"],
                        "closure_check": "Reported test command passed.",
                    }
                ]
            ),
            test_gate=_test_gate(tests_passed_count=0, commands_reported_passed_count=1),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertIn("tests_not_executed_successfully", report["process_failures"])
        self.assertIn("invalid_closure_checks", report["process_failures"])
        self.assertEqual(
            report["closure_check_failures"][0]["missing_closure_requirement"],
            "runtime_tests",
        )

    def test_runtime_closure_allows_existing_test_without_changed_test_file(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "tests",
                        "severity": "minor",
                        "closure_requires": "runtime_tests",
                        "required_files": ["tests/test_greeting.py"],
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "downgraded",
                        "category": "tests",
                        "severity": "info",
                        "evidence": [".fabro/issue-to-pr/test-evidence-gate.json:observed.tests_passed_count=1"],
                        "closure_check": "Focused existing test passed for the requested behavior.",
                    }
                ]
            ),
            test_gate=_test_gate(changed_files=["src/greeting.py"], tests_passed_count=1),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertEqual(report["route_decision"], "export")
        self.assertEqual(report["downgraded_rows"][0]["closure_score"], 3)
        self.assertNotIn("missing_required_files", report["downgraded_rows"][0])

    def test_settings_ref_diff_is_injected(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(),
            materialization=_materialization([], []),
            settings_ref_diff=(
                "``OPTIONS``\n"
                "Extra parameters to pass to the cache backend\n"
                "+Default: ``0o644``\n"
            ),
        )

        self.assertIn("patch_malformed_or_scope_drift", report["process_failures"])
        self.assertEqual(report["malformed_artifacts"][0]["error"], "docs_settings_corruption")

    def test_failed_materialization_fails_even_without_structured_errors(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(),
            materialization={
                "schema_version": 1,
                "stage": "review_materialization",
                "status": "failed",
                "errors": [],
            },
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn("review_artifact_missing_or_malformed", report["process_failures"])
        self.assertEqual(
            report["malformed_artifacts"][0]["error"],
            "materialization_status_not_passed",
        )

    def test_orphan_moderator_disposition_fails(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A99",
                        "state": "rejected",
                        "category": "code",
                        "severity": "minor",
                        "closure_check": "Unsupported row.",
                    }
                ]
            ),
            test_gate=_test_gate(),
            materialization=_materialization([], ["A99"]),
        )

        self.assertIn("orphan_moderator_dispositions", report["process_failures"])
        self.assertEqual(report["orphan_dispositions"][0]["id"], "A99")
        self.assertEqual(report["fixup_required_rows"][0]["id"], "A99")

    def test_duplicate_adversarial_row_ids_fail(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {"id": "A1", "category": "code", "severity": "minor"},
                    {"id": "A1", "category": "tests", "severity": "minor"},
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "rejected",
                        "category": "code",
                        "severity": "minor",
                        "closure_check": "Duplicate row rejected.",
                    }
                ]
            ),
            test_gate=_test_gate(),
            materialization=_materialization(["A1", "A1"], ["A1"]),
        )

        self.assertIn("duplicate_adversarial_rows", report["process_failures"])
        self.assertEqual(report["duplicate_adversarial_row_ids"], ["A1"])

    def test_boolean_test_count_does_not_satisfy_runtime_proof(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "tests",
                        "severity": "major",
                        "closure_requires": "runtime_tests",
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "tests",
                        "severity": "major",
                        "evidence": ["tests_passed_count=true"],
                        "closure_check": "Boolean count claimed.",
                    }
                ]
            ),
            test_gate={
                "schema_version": 1,
                "status": "passed",
                "changed_files": ["tests/test_fix.py"],
                "observed": {"tests_passed_count": True},
            },
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertIn("tests_not_executed_successfully", report["process_failures"])
        self.assertIn("invalid_closure_checks", report["process_failures"])

    def test_embedded_script_matches_pure_failure_report(self):
        adversarial = _adversarial(
            rows=[{"id": "A2", "category": "tests", "severity": "minor"}]
        )
        moderator = _moderator(
            dispositions=[
                {"id": "A2", "state": "open", "category": "tests", "severity": "minor"}
            ]
        )
        test_gate = _test_gate()
        materialization = _materialization(["A2"], ["A2"])
        expected = evaluate_review_accountability(
            adversarial=deepcopy(adversarial),
            moderator=deepcopy(moderator),
            test_gate=deepcopy(test_gate),
            materialization=deepcopy(materialization),
        )
        actual = _run_embedded(adversarial, moderator, test_gate, materialization)
        self.assertEqual(actual, expected)

    def test_embedded_script_is_old_python_annotation_compatible(self):
        script = build_embedded_accountability_gate_script(
            adversarial_path="adversarial.json",
            moderator_path="moderator.json",
            test_gate_path="test-gate.json",
            materialization_path="materialization.json",
            output_path="gate.json",
        )
        body = script.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]

        compile(body, "<embedded-accountability-gate>", "exec")
        ast.parse(body, "<embedded-accountability-gate>", feature_version=(3, 6))
        self.assertNotIn("from __future__ import annotations", script)
        self.assertNotIn(" | None", script)
        self.assertNotRegex(script, r"\b(?:dict|list|set|tuple)\[")

    def test_embedded_script_matches_pure_pass_report(self):
        adversarial = _adversarial(
            rows=[
                {
                    "id": "A1",
                    "category": "code",
                    "severity": "major",
                    "required_files": ["src/fix.py"],
                }
            ]
        )
        moderator = _moderator(
            dispositions=[
                {
                    "id": "A1",
                    "state": "closed_by_evidence",
                    "category": "code",
                    "severity": "major",
                    "evidence": ["src/fix.py"],
                    "closure_check": "Verified src/fix.py changed the scoped fix.",
                }
            ]
        )
        test_gate = _test_gate(changed_files=["src/fix.py"], tests_passed_count=1)
        materialization = _materialization(["A1"], ["A1"])
        expected = evaluate_review_accountability(
            adversarial=deepcopy(adversarial),
            moderator=deepcopy(moderator),
            test_gate=deepcopy(test_gate),
            materialization=deepcopy(materialization),
        )
        actual = _run_embedded(adversarial, moderator, test_gate, materialization)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["route_decision"], "export")

    def test_minor_rejected_metadata_row_with_no_closure_requirement_exports(self):
        adversarial = _adversarial(
            rows=[
                {
                    "id": "A1",
                    "category": "metadata",
                    "severity": "minor",
                    "closure_requires": "none",
                    "required_files": [],
                }
            ]
        )
        moderator = _moderator(
            dispositions=[
                {
                    "id": "A1",
                    "state": "rejected",
                    "category": "metadata",
                    "severity": "minor",
                    "closure_check": "No changelog artifact is required by the issue contract.",
                }
            ]
        )
        report = evaluate_review_accountability(
            adversarial=adversarial,
            moderator=moderator,
            test_gate=_test_gate(changed_files=["tests/test_greeting.py"], tests_passed_count=1),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertEqual(report["route_decision"], "export")
        self.assertEqual(report["closure_check_failures"], [])
        self.assertEqual(report["rejected_rows"][0]["closure_score"], 1)

    def test_rejected_non_metadata_row_without_evidence_fails(self):
        adversarial = _adversarial(
            rows=[
                {
                    "id": "A1",
                    "category": "scope",
                    "severity": "minor",
                    "closure_requires": "none",
                }
            ]
        )
        moderator = _moderator(
            dispositions=[
                {
                    "id": "A1",
                    "state": "rejected",
                    "category": "scope",
                    "severity": "minor",
                    "closure_check": "This is outside the issue contract.",
                }
            ]
        )
        report = evaluate_review_accountability(
            adversarial=adversarial,
            moderator=moderator,
            test_gate=_test_gate(changed_files=["src/greeting.py"], tests_passed_count=1),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("invalid_closure_checks", report["process_failures"])
        self.assertTrue(report["closure_check_failures"][0]["missing_rejection_evidence"])

    def test_minor_rejected_metadata_row_with_unsupported_required_files_exports(self):
        adversarial = _adversarial(
            rows=[
                {
                    "id": "A1",
                    "category": "metadata",
                    "severity": "minor",
                    "closure_requires": "changed_files",
                    "required_files": ["CHANGELOG.md", "RELEASE_NOTES.md"],
                }
            ]
        )
        moderator = _moderator(
            dispositions=[
                {
                    "id": "A1",
                    "state": "rejected",
                    "category": "metadata",
                    "severity": "minor",
                    "evidence": [
                        "diff-audit.json changed_files only includes src/greeting.py.",
                        "test-evidence-gate.json records a passing machine-verified command.",
                    ],
                    "closure_check": "The issue scope is the greeting implementation fix.",
                }
            ]
        )
        report = evaluate_review_accountability(
            adversarial=adversarial,
            moderator=moderator,
            test_gate=_test_gate(changed_files=["src/greeting.py"], tests_passed_count=1),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertEqual(report["route_decision"], "export")
        self.assertEqual(report["closure_check_failures"], [])
        self.assertEqual(
            report["rejected_rows"][0]["missing_required_files"],
            ["CHANGELOG.md", "RELEASE_NOTES.md"],
        )
        self.assertEqual(report["rejected_rows"][0]["closure_score"], 2)

    def test_issue_artifact_required_file_does_not_require_patch_change(self):
        adversarial = _adversarial(
            rows=[
                {
                    "id": "A1",
                    "category": "metadata",
                    "severity": "minor",
                    "required_files": [".fabro/issue-to-pr/validation.json"],
                }
            ]
        )
        moderator = _moderator(
            dispositions=[
                {
                    "id": "A1",
                    "state": "closed_by_evidence",
                    "category": "metadata",
                    "severity": "minor",
                    "evidence": [
                        ".fabro/issue-to-pr/validation.json cites command id cmd-1",
                        "python3 -m unittest tests.test_greeting passed",
                    ],
                    "closure_check": "Validation artifact cites the observed command id.",
                }
            ]
        )
        test_gate = _test_gate(changed_files=["src/greeting.py"], tests_passed_count=1)
        materialization = _materialization(["A1"], ["A1"])
        report = evaluate_review_accountability(
            adversarial=deepcopy(adversarial),
            moderator=deepcopy(moderator),
            test_gate=deepcopy(test_gate),
            materialization=deepcopy(materialization),
        )
        embedded = _run_embedded(adversarial, moderator, test_gate, materialization)

        self.assertEqual(report["route_decision"], "export")
        self.assertEqual(report["closure_check_failures"], [])
        self.assertEqual(embedded, report)


def _run_embedded(adversarial, moderator, test_gate, materialization):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = {
            "adversarial": root / "adversarial.json",
            "moderator": root / "moderator.json",
            "test_gate": root / "test-gate.json",
            "materialization": root / "materialization.json",
            "output": root / "gate.json",
        }
        paths["adversarial"].write_text(json.dumps(adversarial))
        paths["moderator"].write_text(json.dumps(moderator))
        paths["test_gate"].write_text(json.dumps(test_gate))
        paths["materialization"].write_text(json.dumps(materialization))
        script = build_embedded_accountability_gate_script(
            adversarial_path=str(paths["adversarial"]),
            moderator_path=str(paths["moderator"]),
            test_gate_path=str(paths["test_gate"]),
            materialization_path=str(paths["materialization"]),
            output_path=str(paths["output"]),
        )
        proc = subprocess.run(
            script,
            shell=True,
            executable="/bin/bash",
            cwd=root,
            capture_output=True,
            text=True,
        )
        if not paths["output"].exists():
            raise AssertionError(proc.stdout + proc.stderr)
        self_status = json.loads(paths["output"].read_text())["status"]
        expected_returncode = 1 if self_status == "failed" else 0
        if proc.returncode != expected_returncode:
            raise AssertionError(proc.stdout + proc.stderr)
        return json.loads(paths["output"].read_text())


def _empty_review_with_scope(scope_assessment):
    return evaluate_review_accountability(
        adversarial=_adversarial(
            rows=[],
            checked_risks=[
                {
                    "risk": "source behavior change",
                    "check": "reviewed src/parser.py counterexample path",
                    "evidence": ["src/parser.py"],
                }
            ],
            scope_assessment=scope_assessment,
        ),
        moderator=_moderator(dispositions=[]),
        test_gate=_test_gate(changed_files=["src/parser.py"]),
        materialization=_materialization([], []),
        patch_diff=_source_patch_diff(),
    )


def _source_patch_diff():
    return (
        "diff --git a/src/parser.py b/src/parser.py\n"
        "--- a/src/parser.py\n"
        "+++ b/src/parser.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-    return None\n"
        "+    return value\n"
    )


def _adversarial(rows, **extra):
    return {
        "schema_version": 1,
        "stage": "adversarial_review",
        "summary": "review",
        "rows": rows,
        "overall_risk": "medium",
        **extra,
    }


def _moderator(dispositions):
    return {
        "schema_version": 1,
        "stage": "moderator_filter",
        "dispositions": dispositions,
        "readiness_tier": "ready_verified",
        "next_agent_guidance": "Proceed.",
    }


def _test_gate(
    *,
    changed_files=None,
    tests_passed_count=1,
    commands_reported_passed_count=0,
):
    changed_files = changed_files or ["src/fix.py", "tests/test_fix.py"]
    return {
        "schema_version": 1,
        "status": "passed",
        "changed_files": changed_files,
        "observed": {
            "changed_files": changed_files,
            "tests_passed_count": tests_passed_count,
            "commands_reported_passed_count": commands_reported_passed_count,
        },
    }


def _materialization(row_ids, disposition_ids):
    return {
        "schema_version": 1,
        "stage": "review_materialization",
        "status": "passed",
        "errors": [],
        "adversarial_row_ids": row_ids,
        "moderator_disposition_ids": disposition_ids,
    }


if __name__ == "__main__":
    unittest.main()
