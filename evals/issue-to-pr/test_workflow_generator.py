import unittest

from workflow_generator import (
    SIMPLE_PROFILE,
    STRUCTURED_PROFILE,
    VERIFY_DIFF_CHECK,
    DIFF_AUDIT_PATH,
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
        validate_generated_workflow(workflow, workflow_profile=STRUCTURED_PROFILE)

    def test_structured_preflight_rejects_stale_loop_budget(self):
        workflow = generate_issue_to_pr_workflow(
            graph_name="IssueToPr",
            setup_script="git clone https://example.test/repo .",
            workflow_profile=STRUCTURED_PROFILE,
            verify_mode=VERIFY_DIFF_CHECK,
        ).replace("max_visits=4", "max_visits=2").replace("max_visits=3", "max_visits=2")

        with self.assertRaisesRegex(ValueError, "verify, snapshot_patch, audit, review, fixup"):
            validate_generated_workflow(workflow, workflow_profile=STRUCTURED_PROFILE)

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


if __name__ == "__main__":
    unittest.main()
