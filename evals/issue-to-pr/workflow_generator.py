"""Shared workflow graph generation for issue-to-PR style attempts."""

from __future__ import annotations


SIMPLE_PROFILE = "simple"
STRUCTURED_PROFILE = "structured"
VERIFY_NONE = "none"
VERIFY_DIFF_CHECK = "diff-check"


def dot_escape(text: str) -> str:
    """Escape text for a DOT double-quoted attribute value."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def default_verify_mode(workflow_profile: str, verify_mode: str | None) -> str:
    if verify_mode:
        return verify_mode
    if workflow_profile == STRUCTURED_PROFILE:
        return VERIFY_DIFF_CHECK
    return VERIFY_NONE


def generate_issue_to_pr_workflow(
    *,
    graph_name: str,
    setup_script: str,
    workflow_profile: str = SIMPLE_PROFILE,
    verify_mode: str | None = None,
    solve_prompt: str = "Fix this issue in the repository. Make the minimal code change needed.",
) -> str:
    """Generate a Fabro DOT workflow for issue-to-PR attempts."""
    verify_mode = default_verify_mode(workflow_profile, verify_mode)
    if workflow_profile == SIMPLE_PROFILE:
        return _simple_workflow(graph_name, setup_script, solve_prompt)
    if workflow_profile == STRUCTURED_PROFILE:
        return _structured_workflow(graph_name, setup_script, verify_mode)
    raise ValueError(f"unsupported workflow profile: {workflow_profile}")


def _simple_workflow(graph_name: str, setup_script: str, solve_prompt: str) -> str:
    return f'''digraph {graph_name} {{
    rankdir=LR
    start [shape=Mdiamond]
    exit  [shape=Msquare]
    setup         [label="Setup", shape=parallelogram, script="{dot_escape(setup_script)}"]
    solve         [label="Solve", prompt="{dot_escape(solve_prompt)}"]
    extract_patch [label="Extract Patch", shape=parallelogram, script="git diff"]
    start -> setup -> solve -> extract_patch -> exit
}}
'''


def _structured_workflow(graph_name: str, setup_script: str, verify_mode: str) -> str:
    return f'''digraph {graph_name} {{
    rankdir=LR
    start [shape=Mdiamond]
    exit  [shape=Msquare]
    setup         [label="Setup", shape=parallelogram, script="{dot_escape(setup_script)}"]
    research      [label="Research", prompt="{dot_escape(_research_prompt())}"]
    implement     [label="Implement", prompt="{dot_escape(_implement_prompt())}"]
    verify        [label="Verify", shape=parallelogram, goal_gate=true, max_retries=0, script="{dot_escape(_verify_script(verify_mode))}"]
    fixup         [label="Fixup", max_visits=1, prompt="{dot_escape(_fixup_prompt())}"]
    extract_patch [label="Extract Patch", shape=parallelogram, script="git diff"]
    start -> setup -> research -> implement -> verify
    verify -> extract_patch [condition="outcome=succeeded"]
    verify -> fixup        [condition="outcome=failed"]
    verify -> fixup        [label="Fallback"]
    fixup -> verify
    extract_patch -> exit
}}
'''


def _research_prompt() -> str:
    return """Research the issue and repository before editing.

Do not modify repository files during this stage. Identify the likely files,
code paths, and any visible checks that would help validate the fix. If you need
notes, write them only to /tmp/fabro-research.md so they cannot pollute git diff."""


def _implement_prompt() -> str:
    return """Fix this issue in the repository. Make the minimal code change needed.

Use /tmp/fabro-research.md if it exists. Keep benchmark, grader, and hidden-test
assumptions out of the implementation."""


def _fixup_prompt() -> str:
    return """The verify stage failed. Read the verify output from context and fix the issue.

Keep the patch minimal. Do not create durable notes in the repository unless they
are part of the requested source change."""


def _verify_script(verify_mode: str) -> str:
    if verify_mode == VERIFY_NONE:
        return (
            "printf '%s\\n' "
            "'{\"schema_version\":1,\"status\":\"skipped\",\"mode\":\"none\","
            "\"patch_nonempty\":null,\"failure_reason\":null}'"
        )
    if verify_mode != VERIFY_DIFF_CHECK:
        raise ValueError(f"unsupported verify mode: {verify_mode}")
    return """set +e
if git diff --quiet --exit-code; then
  printf '%s\\n' '{"schema_version":1,"status":"failed","mode":"diff-check","patch_nonempty":false,"failure_reason":"No patch produced"}'
  exit 1
fi

git diff --check
check_status=$?
if [ "$check_status" -eq 0 ]; then
  printf '%s\\n' '{"schema_version":1,"status":"passed","mode":"diff-check","patch_nonempty":true,"failure_reason":null}'
  exit 0
fi

printf '%s\\n' '{"schema_version":1,"status":"failed","mode":"diff-check","patch_nonempty":true,"failure_reason":"git diff --check failed"}'
exit "$check_status"
"""
