import unittest
from pathlib import Path

from fabro_kits.issue_to_pr.run_attempt import _stage_dir_matches
from fabro_kits.issue_to_pr.workflow_generator import (
    ADVERSARIAL_REVIEW_PATH,
    DIFF_AUDIT_PATH,
    MODERATOR_FILTER_PATH,
    REVIEW_ACCOUNTABILITY_GATE_PATH,
    REVIEW_MATERIALIZATION_PATH,
    SIMPLE_PROFILE,
    STRUCTURED_GATED_PROFILE,
    STRUCTURED_MODERATED_PROFILE,
    STRUCTURED_PROFILE,
    TEST_EVIDENCE_GATE_PATH,
    VALIDATION_CONTRACT_PATH,
    VERIFY_DIFF_CHECK,
    escape_goal_for_template,
    generate_issue_to_pr_workflow,
    validate_generated_workflow,
)


class WorkflowGeneratorTest(unittest.TestCase):
    def test_simple_profile_preserves_single_solve_shape(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=SIMPLE_PROFILE,
        )

        self.assertIn("start -> setup -> solve -> extract_patch -> exit", workflow)
        self.assertNotIn("research", workflow)
        validate_generated_workflow(workflow, workflow_profile=SIMPLE_PROFILE)

    def test_structured_profile_adds_verify_review_loop(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        )

        self.assertIn("research", workflow)
        self.assertIn("implement", workflow)
        self.assertIn("verify", workflow)
        self.assertIn("snapshot_patch -> audit -> review", workflow)
        self.assertIn("review -> extract_patch [label=\"Approve\"]", workflow)
        self.assertIn("fixup -> verify", workflow)
        self.assertIn(DIFF_AUDIT_PATH, workflow)
        self.assertIn(VALIDATION_CONTRACT_PATH, workflow)
        validate_generated_workflow(workflow, workflow_profile=STRUCTURED_PROFILE)

    def test_structured_gated_profile_adds_test_evidence_gate(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_GATED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        )

        self.assertIn("test_evidence_gate", workflow)
        self.assertIn(TEST_EVIDENCE_GATE_PATH, workflow)
        self.assertIn("snapshot_patch -> audit -> test_evidence_gate", workflow)
        self.assertIn(
            'test_evidence_gate -> review [condition="outcome=succeeded"]',
            workflow,
        )
        self.assertIn(
            'test_evidence_gate -> fixup  [condition="outcome=failed"]',
            workflow,
        )
        validate_generated_workflow(workflow, workflow_profile=STRUCTURED_GATED_PROFILE)

    def test_structured_moderated_profile_uses_materializer_and_one_gate(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_MODERATED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        )

        self.assertIn("adversarial_review", workflow)
        self.assertIn("moderator_filter", workflow)
        self.assertIn("materialize_review_artifacts", workflow)
        self.assertIn("review_accountability_gate", workflow)
        self.assertNotIn("acceptance_audit", workflow)
        self.assertNotIn("review_ledger", workflow)
        self.assertNotIn("Acceptance Audit", workflow)
        self.assertIn(ADVERSARIAL_REVIEW_PATH, workflow)
        self.assertIn(MODERATOR_FILTER_PATH, workflow)
        self.assertIn(REVIEW_MATERIALIZATION_PATH, workflow)
        self.assertIn(REVIEW_ACCOUNTABILITY_GATE_PATH, workflow)
        self.assertIn(
            "adversarial_review -> moderator_filter -> materialize_review_artifacts",
            workflow,
        )
        self.assertIn(
            'materialize_review_artifacts -> review_accountability_gate [condition="outcome=succeeded"]',
            workflow,
        )
        self.assertIn(
            'materialize_review_artifacts -> review_accountability_gate [condition="outcome=failed"]',
            workflow,
        )
        self.assertIn(
            'review_accountability_gate -> extract_patch     [condition="outcome=succeeded"]',
            workflow,
        )
        self.assertIn(
            'review_accountability_gate -> fixup            [condition="outcome=failed"]',
            workflow,
        )
        self.assertIn('materialize_review_artifacts -> review_accountability_gate [label="Fallback"]', workflow)
        self.assertIn('review_accountability_gate -> fixup            [label="Fallback"]', workflow)
        validate_generated_workflow(
            workflow,
            workflow_profile=STRUCTURED_MODERATED_PROFILE,
        )

    def test_structured_moderated_prompt_contract_is_row_accountable(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_MODERATED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        )

        self.assertIn("open|closed_by_evidence|rejected|downgraded", workflow)
        self.assertIn("Every adversarial row", workflow)
        self.assertIn("same-id disposition", workflow)
        self.assertIn("closure_check", workflow)
        self.assertIn("closure_check_failures", workflow)
        self.assertIn("closure_requires", workflow)
        self.assertIn("missing_closure_requirement", workflow)
        self.assertIn("category_mismatch", workflow)
        self.assertIn("required_files", workflow)
        self.assertIn("unaccounted_adversarial_rows", workflow)
        self.assertIn("fixup_required_rows", workflow)
        self.assertIn("offending_diff", workflow)
        self.assertIn("Repair the whole patch", workflow)
        self.assertIn("Proceed to patch extraction.", workflow)
    def test_structured_preflight_rejects_stale_loop_budget(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        ).replace("max_visits=4", "max_visits=2").replace("max_visits=3", "max_visits=2")

        with self.assertRaisesRegex(ValueError, "verify, snapshot_patch, audit, review, fixup"):
            validate_generated_workflow(workflow, workflow_profile=STRUCTURED_PROFILE)

    def test_structured_moderated_preflight_checks_materializer_and_gate(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_MODERATED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        ).replace(
            "review_accountability_gate [label=\"Review Accountability Gate\", shape=parallelogram, goal_gate=true, max_retries=0, max_visits=4",
            "review_accountability_gate [label=\"Review Accountability Gate\", shape=parallelogram, goal_gate=true, max_retries=0, max_visits=2",
        )

        with self.assertRaisesRegex(ValueError, "review_accountability_gate"):
            validate_generated_workflow(
                workflow,
                workflow_profile=STRUCTURED_MODERATED_PROFILE,
            )

    def test_escape_goal_for_template_neutralizes_django_template_examples(self):
        goal = "Use {% static 'admin/base.css' %} and {{ value }} literally."
        escaped = escape_goal_for_template(goal)

        self.assertEqual(
            escaped,
            "Use { % static 'admin/base.css' % } and { { value } } literally.",
        )

    def test_escape_goal_for_template_leaves_plain_goal_alone(self):
        goal = "Fix the model field validation message."
        self.assertEqual(escape_goal_for_template(goal), goal)

    def test_stage_dir_matcher_does_not_match_substrings(self):
        self.assertTrue(_stage_dir_matches(Path("007-audit@1"), "audit"))
        self.assertFalse(_stage_dir_matches(Path("011-diff_audit@1"), "audit"))


if __name__ == "__main__":
    unittest.main()
