import unittest

from workflow_generator import (
    SIMPLE_PROFILE,
    STRUCTURED_PROFILE,
    VERIFY_DIFF_CHECK,
    generate_issue_to_pr_workflow,
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
        self.assertIn("fixup", workflow)
        self.assertIn("max_visits=1", workflow)
        self.assertIn("verify -> extract_patch [condition=\"outcome=succeeded\"]", workflow)
        self.assertIn("verify -> fixup        [condition=\"outcome=failed\"]", workflow)
        self.assertIn("verify -> fixup        [label=\"Fallback\"]", workflow)
        self.assertIn("fixup -> verify", workflow)
        self.assertIn("extract_patch -> exit", workflow)
        self.assertNotIn("review", workflow)
        self.assertNotIn("gate [", workflow)


if __name__ == "__main__":
    unittest.main()
