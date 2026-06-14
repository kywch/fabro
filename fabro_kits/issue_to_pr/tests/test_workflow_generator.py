import unittest
from pathlib import Path

from fabro_kits.issue_to_pr.run_attempt import _stage_dir_matches
from fabro_kits.issue_to_pr.workflow_generator import (
    SIMPLE_PROFILE,
    STRUCTURED_GATED_PROFILE,
    STRUCTURED_MODERATED_PROFILE,
    STRUCTURED_PROFILE,
    ACCEPTANCE_AUDIT_PATH,
    ADVERSARIAL_REVIEW_PATH,
    VERIFY_DIFF_CHECK,
    DIFF_AUDIT_PATH,
    MODERATOR_FILTER_PATH,
    REVIEW_LEDGER_PATH,
    TEST_EVIDENCE_GATE_PATH,
    VALIDATION_CONTRACT_PATH,
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

        self.assertIn("solve", workflow)
        self.assertIn("start -> setup -> solve -> extract_patch -> exit", workflow)
        self.assertNotIn("research", workflow)
        self.assertNotIn("fixup", workflow)

    def test_structured_profile_adds_verify_fix_loop(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        )

        self.assertIn("research", workflow)
        self.assertIn("implement", workflow)
        self.assertIn("verify", workflow)
        self.assertIn("snapshot_patch", workflow)
        self.assertIn("audit", workflow)
        self.assertIn("review", workflow)
        self.assertIn('output_schema="routing"', workflow)
        self.assertIn("fixup", workflow)
        self.assertIn("verify        [label=\"Verify\"", workflow)
        self.assertIn("max_retries=0, max_visits=4", workflow)
        self.assertIn("snapshot_patch [label=\"Snapshot Patch\"", workflow)
        self.assertIn("audit         [label=\"Audit Diff\"", workflow)
        self.assertIn("stdout=subprocess.PIPE", workflow)
        self.assertNotIn("capture_output", workflow)
        self.assertIn("review        [label=\"Review\", goal_gate=true, max_visits=4", workflow)
        self.assertIn("fixup         [label=\"Fixup\", max_visits=3", workflow)
        self.assertIn("verify -> snapshot_patch [condition=\"outcome=succeeded\"]", workflow)
        self.assertIn("verify -> fixup        [condition=\"outcome=failed\"]", workflow)
        self.assertIn("verify -> fixup        [label=\"Fallback\"]", workflow)
        self.assertIn("snapshot_patch -> audit -> review", workflow)
        self.assertIn("fixup -> verify", workflow)
        self.assertIn("review -> extract_patch [label=\"Approve\"]", workflow)
        self.assertIn("review -> fixup        [label=\"Fix\"]", workflow)
        self.assertIn("extract_patch -> exit", workflow)
        self.assertIn(DIFF_AUDIT_PATH, workflow)
        self.assertIn(VALIDATION_CONTRACT_PATH, workflow)
        self.assertIn("changed_files", workflow)
        self.assertIn("commands_run", workflow)
        self.assertNotIn("gate [", workflow)
        self.assertNotIn("test_evidence_gate", workflow)
        validate_generated_workflow(workflow, workflow_profile=STRUCTURED_PROFILE)

    def test_structured_gated_profile_adds_test_evidence_gate(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_GATED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        )

        self.assertIn("test_evidence_gate", workflow)
        self.assertIn("Test Evidence Gate", workflow)
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
        self.assertIn('test_evidence_gate -> fixup  [label="Fallback"]', workflow)
        self.assertIn("validation_claims_tests_but_diff_has_no_test_files", workflow)
        self.assertIn("json.JSONDecodeError", workflow)
        self.assertIn("stdout=subprocess.PIPE", workflow)
        self.assertNotIn("capture_output", workflow)
        validate_generated_workflow(workflow, workflow_profile=STRUCTURED_GATED_PROFILE)

    def test_structured_moderated_profile_adds_three_stage_review(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_MODERATED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        )

        self.assertIn("test_evidence_gate", workflow)
        self.assertIn("adversarial_review", workflow)
        self.assertIn("moderator_filter", workflow)
        self.assertIn("acceptance_audit", workflow)
        self.assertNotIn("review        [label=\"Review\"", workflow)
        self.assertIn(ADVERSARIAL_REVIEW_PATH, workflow)
        self.assertIn(MODERATOR_FILTER_PATH, workflow)
        self.assertIn(ACCEPTANCE_AUDIT_PATH, workflow)
        self.assertIn(REVIEW_LEDGER_PATH, workflow)
        self.assertIn(
            'test_evidence_gate -> adversarial_review [condition="outcome=succeeded"]',
            workflow,
        )
        self.assertIn(
            "adversarial_review -> moderator_filter -> acceptance_audit",
            workflow,
        )
        self.assertIn(
            'acceptance_audit -> extract_patch [label="Approve"]',
            workflow,
        )
        self.assertIn(
            'acceptance_audit -> fixup         [label="Fix"]',
            workflow,
        )
        self.assertIn("ready_verified|ready_unverified|needs_fix_code", workflow)
        self.assertIn("fix_code|fix_tests|metadata_warning|none", workflow)
        validate_generated_workflow(
            workflow,
            workflow_profile=STRUCTURED_MODERATED_PROFILE,
        )

    def test_structured_preflight_rejects_stale_loop_budget(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        ).replace("max_visits=4", "max_visits=2").replace("max_visits=3", "max_visits=2")

        with self.assertRaisesRegex(ValueError, "verify, snapshot_patch, audit, review, fixup"):
            validate_generated_workflow(workflow, workflow_profile=STRUCTURED_PROFILE)

    def test_structured_gated_preflight_rejects_missing_gate_budget(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_GATED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        ).replace("test_evidence_gate [label=\"Test Evidence Gate\", shape=parallelogram, goal_gate=true, max_retries=0, max_visits=4", "test_evidence_gate [label=\"Test Evidence Gate\", shape=parallelogram, goal_gate=true, max_retries=0, max_visits=2")

        with self.assertRaisesRegex(ValueError, "test_evidence_gate"):
            validate_generated_workflow(
                workflow,
                workflow_profile=STRUCTURED_GATED_PROFILE,
            )

    def test_structured_moderated_preflight_rejects_missing_review_budget(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_MODERATED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        ).replace(
            "moderator_filter   [label=\"Moderator Filter\", max_visits=4",
            "moderator_filter   [label=\"Moderator Filter\", max_visits=2",
        )

        with self.assertRaisesRegex(ValueError, "moderator_filter"):
            validate_generated_workflow(
                workflow,
                workflow_profile=STRUCTURED_MODERATED_PROFILE,
            )

    def test_simple_preflight_does_not_require_structured_loop(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=SIMPLE_PROFILE,
        )

        validate_generated_workflow(workflow, workflow_profile=SIMPLE_PROFILE)

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

    def test_stage_dir_matcher_does_not_confuse_audit_with_acceptance_audit(self):
        self.assertTrue(_stage_dir_matches(Path("007-audit@1"), "audit"))
        self.assertFalse(
            _stage_dir_matches(Path("011-acceptance_audit@1"), "audit"),
        )


if __name__ == "__main__":
    unittest.main()
