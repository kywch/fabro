"""Shared workflow graph generation for issue-to-PR style attempts."""

from __future__ import annotations


SIMPLE_PROFILE = "simple"
STRUCTURED_PROFILE = "structured"
VERIFY_NONE = "none"
VERIFY_DIFF_CHECK = "diff-check"
TEMPLATE_START_MARKERS = ("{{", "{%", "{#")
STRUCTURED_FIXUP_MAX_VISITS = 3
STRUCTURED_VERIFY_REVIEW_MAX_VISITS = STRUCTURED_FIXUP_MAX_VISITS + 1
VALIDATION_CONTRACT_PATH = "/tmp/fabro-validation.json"
DIFF_AUDIT_PATH = "/tmp/fabro-diff-audit.json"


def dot_escape(text: str) -> str:
    """Escape text for a DOT double-quoted attribute value."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def escape_goal_for_template(text: str) -> str:
    """Protect issue text that looks like MiniJinja template syntax.

    Fabro renders graph goals as templates. SWE-bench issue text can contain
    Django template examples such as `{% static '...' %}`. Goal text can be
    rendered more than once, so raw blocks are not enough; neutralize template
    delimiters while keeping the examples readable for the agent.
    """
    replacements = (
        ("{%", "{ %"),
        ("%}", "% }"),
        ("{{", "{ {"),
        ("}}", "} }"),
        ("{#", "{ #"),
        ("#}", "# }"),
    )
    escaped = text
    for source, target in replacements:
        escaped = escaped.replace(source, target)
    return escaped


def default_verify_mode(workflow_profile: str, verify_mode: str | None) -> str:
    if verify_mode:
        return verify_mode
    if workflow_profile == STRUCTURED_PROFILE:
        return VERIFY_DIFF_CHECK
    return VERIFY_NONE


def validate_generated_workflow(workflow: str, *, workflow_profile: str) -> None:
    """Validate invariants the eval harness relies on before launching Fabro."""
    if workflow_profile != STRUCTURED_PROFILE:
        return

    expected_by_node = {
        "verify": f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}",
        "snapshot_patch": f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}",
        "audit": f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}",
        "review": f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}",
        "fixup": f"max_visits={STRUCTURED_FIXUP_MAX_VISITS}",
    }
    missing = [
        node_id
        for node_id, expected in expected_by_node.items()
        if not _node_definition_contains(workflow, node_id, expected)
    ]
    if missing:
        raise ValueError(
            "structured workflow repair-loop preflight failed: "
            f"{', '.join(missing)} must include expected max_visits values"
        )


def _node_definition_contains(workflow: str, node_id: str, needle: str) -> bool:
    for line in workflow.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{node_id} ") or stripped.startswith(f"{node_id}["):
            return needle in stripped
    return False


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
    verify        [label="Verify", shape=parallelogram, goal_gate=true, max_retries=0, max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, script="{dot_escape(_verify_script(verify_mode))}"]
    snapshot_patch [label="Snapshot Patch", shape=parallelogram, max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, script="git diff"]
    audit         [label="Audit Diff", shape=parallelogram, max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, script="{dot_escape(_audit_script())}"]
    review        [label="Review", goal_gate=true, max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, output_schema="routing", prompt="{dot_escape(_review_prompt())}"]
    fixup         [label="Fixup", max_visits={STRUCTURED_FIXUP_MAX_VISITS}, prompt="{dot_escape(_fixup_prompt())}"]
    extract_patch [label="Extract Patch", shape=parallelogram, script="git diff"]
    start -> setup -> research -> implement -> verify
    verify -> snapshot_patch [condition="outcome=succeeded"]
    verify -> fixup        [condition="outcome=failed"]
    verify -> fixup        [label="Fallback"]
    snapshot_patch -> audit -> review
    fixup -> verify
    review -> extract_patch [label="Approve"]
    review -> fixup        [label="Fix"]
    extract_patch -> exit
}}
'''


def _research_prompt() -> str:
    return """Research the issue and repository before editing.

This is a non-interactive benchmark/batch run:
- Do not ask the user questions.
- Do not call request_user_input or any interactive clarification tool.
- If scope is ambiguous, make the smallest defensible assumption from the issue
  text and repository evidence, then record that assumption.

Hard contract:
- Do not modify repository files during this stage.
- Do not use edit/write tools on repository files.
- Do not run shell commands that write into the repository.
- Do not create temporary regression tests in repository files, even if you
  intend to revert them.
- Do not use git checkout, git restore, git reset, or cleanup commands as a way
  to hide research-stage writes. A clean diff after research is not enough; the
  stage itself must be read-only.
- If you need notes, write them only to /tmp/fabro-research.md so they cannot
  pollute git diff.
- Also write {VALIDATION_CONTRACT_PATH} with a JSON object containing:
  acceptance_criteria, risky_shortcuts, likely_files, test_plan, and
  research_assumptions. This file is the durable goal/evidence handoff for the
  fresh agents in later stages and must not live inside the repository.

Identify:
- the exact acceptance criteria from the issue text;
- likely files and code paths;
- whether docs, release notes, migrations, or tests are part of the requested fix;
- visible tests or commands that can validate the change;
- risks where an obvious shortcut would not satisfy the deeper contract.

End with a concise research summary that includes acceptance criteria and a test plan.""".replace(
        "{VALIDATION_CONTRACT_PATH}", VALIDATION_CONTRACT_PATH
    )


def _implement_prompt() -> str:
    return """Fix this issue in the repository. Make the minimal code change needed.

This is a non-interactive benchmark/batch run. Do not ask the user questions and
do not call request_user_input. If scope is ambiguous, make the smallest
defensible assumption from the issue text and repository evidence, then record it
in the final response.

Use /tmp/fabro-research.md if it exists. Keep benchmark, grader, and hidden-test
assumptions out of the implementation.

Before editing, restate the acceptance criteria from the issue/research. The final
patch must satisfy all of them, not only the issue title. Add or update regression
tests when the behavior is testable in the repository. If docs or release notes are
explicitly requested, update them too.

Run the most relevant visible test command you can identify. Do not claim a command
passed unless it actually exited successfully. If no useful test can run, explain
why in the final response.

Before finishing, update {VALIDATION_CONTRACT_PATH}. Preserve the research fields
and add or update:
- changed_files: repository files intentionally changed;
- tests_added: regression tests added or updated;
- commands_run: exact commands with status values such as passed, failed,
  not_run, or unavailable;
- no_test_justification: required when behavior is testable but no regression test
  was added or run;
- residual_risks: known gaps, uncertainty, or environment limitations;
- final_claims: the behavior you believe the patch now satisfies.

Do not end with casual follow-up offers or questions. This is a batch run.""".replace(
        "{VALIDATION_CONTRACT_PATH}", VALIDATION_CONTRACT_PATH
    )


def _review_prompt() -> str:
    return """Review the patch before it is exported.

This is a non-interactive benchmark/batch run:
- Do not ask the user questions.
- Do not call request_user_input or any interactive clarification tool.
- Do not modify repository files.
- Use read-only inspection only: git diff, file reads, searches, and test-log
  inspection are allowed. Do not edit files.
- Do not run tests, compilers, formatters, or commands that may create caches or
  mutate the repository during review. Judge only from the patch, prior stage
  output, and recorded command results.

Check:
- {DIFF_AUDIT_PATH} exists and matches the actual `git diff`; treat this
  machine-generated audit and the current diff as authoritative over
  agent-authored validation claims;
- {VALIDATION_CONTRACT_PATH} exists and is consistent with the diff and prior
  stage output. If the validation contract disagrees with the audit/diff, judge
  the patch first and call out metadata drift separately;
- changed_files, tests_added, commands_run, residual_risks, and final_claims in
  the validation contract are specific and not contradictory;
- the patch satisfies every acceptance criterion from the issue and research;
- the fix is not an overbroad shortcut that changes unrelated behavior;
- test evidence is credible for the changed behavior;
- if the behavior is testable in this repository, the patch adds or updates a
  regression test unless there is a repository-specific reason this is impossible;
- the final response does not claim stronger validation than was actually run.

Classify any blocking issue as one of:
- code_blocking: the code diff is wrong, incomplete, overbroad, or unrelated;
- test_blocking: test coverage/evidence is missing or not credible for a
  testable behavior;
- metadata_blocking: only the agent-authored validation contract is stale,
  incomplete, or inconsistent while the actual code/test diff is otherwise ready.

Only route to Fix for code_blocking or test_blocking issues. If the only
remaining issue is metadata_blocking, approve export and mention the metadata
warning in context_updates.

If the patch is ready, end with exactly this routing JSON:
{"preferred_next_label":"Approve","outcome":"succeeded","context_updates":{"review_decision":"approve"}}

If changes are needed, explain the blocking issue briefly and end with exactly
this routing JSON:
{"preferred_next_label":"Fix","outcome":"failed","failure_class":"<code_blocking|test_blocking>","failure_reason":"<specific reason>","context_updates":{"review_decision":"fix","do_not_repeat":["<specific failed approach to avoid>"],"next_agent_guidance":"<what a downstream agent should try next>"}}""".replace(
        "{VALIDATION_CONTRACT_PATH}", VALIDATION_CONTRACT_PATH
    ).replace(
        "{DIFF_AUDIT_PATH}", DIFF_AUDIT_PATH
    )


def _fixup_prompt() -> str:
    return """A quality gate failed. Read the verify or review output from context and fix the issue.

This is a non-interactive benchmark/batch run. Do not ask the user questions and
do not call request_user_input. If scope is ambiguous, make the smallest
defensible assumption from the issue text and repository evidence, then record it
in the final response.

Keep the patch minimal. Do not create durable notes in the repository unless they
are part of the requested source change.

Read {VALIDATION_CONTRACT_PATH} before editing. Update it before finishing with:
- reviewer_objections addressed;
- changed_files after fixup;
- commands_run after fixup;
- residual_risks after fixup;
- final_claims after fixup.

Repair the actual patch before repairing metadata. If the validation contract
disagrees with the current diff or {DIFF_AUDIT_PATH}, update the code/tests first,
then make the contract match the real final state.

Do not end with casual follow-up offers or questions. This is a batch run.""".replace(
        "{VALIDATION_CONTRACT_PATH}", VALIDATION_CONTRACT_PATH
    ).replace(
        "{DIFF_AUDIT_PATH}", DIFF_AUDIT_PATH
    )


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


def _audit_script() -> str:
    return f"""python - <<'PY'
import json
import subprocess
from pathlib import Path


def run(*args):
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return proc.returncode, proc.stdout, proc.stderr


_, names, _ = run("git", "diff", "--name-only")
_, stat, _ = run("git", "diff", "--stat")
changed_files = [line for line in names.splitlines() if line.strip()]
test_files = [
    path for path in changed_files
    if path.startswith("tests/")
    or "/tests/" in path
    or path.startswith("test_")
    or path.endswith("_test.py")
    or path.endswith("/tests.py")
]
audit = {{
    "schema_version": 1,
    "patch_nonempty": bool(changed_files),
    "changed_files": changed_files,
    "test_files_changed": test_files,
    "diff_stat": [line for line in stat.splitlines() if line.strip()],
}}
Path("{DIFF_AUDIT_PATH}").write_text(json.dumps(audit, indent=2) + "\\n")
print(json.dumps(audit, sort_keys=True))
PY
"""
