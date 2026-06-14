"""Shared workflow graph generation for issue-to-PR style attempts."""

from __future__ import annotations

from .evidence_gate import build_embedded_gate_script


SIMPLE_PROFILE = "simple"
STRUCTURED_PROFILE = "structured"
STRUCTURED_GATED_PROFILE = "structured-gated"
STRUCTURED_MODERATED_PROFILE = "structured-moderated"
VERIFY_NONE = "none"
VERIFY_DIFF_CHECK = "diff-check"
TEMPLATE_START_MARKERS = ("{{", "{%", "{#")
STRUCTURED_FIXUP_MAX_VISITS = 3
STRUCTURED_VERIFY_REVIEW_MAX_VISITS = STRUCTURED_FIXUP_MAX_VISITS + 1
VALIDATION_CONTRACT_PATH = "/tmp/fabro-validation.json"
DIFF_AUDIT_PATH = "/tmp/fabro-diff-audit.json"
TEST_EVIDENCE_GATE_PATH = "/tmp/fabro-test-evidence-gate.json"
ADVERSARIAL_REVIEW_PATH = "/tmp/fabro-adversarial-review.json"
MODERATOR_FILTER_PATH = "/tmp/fabro-moderator-filter.json"
ACCEPTANCE_AUDIT_PATH = "/tmp/fabro-acceptance-audit.json"
REVIEW_LEDGER_PATH = "/tmp/fabro-review-ledger.json"
READY_TIERS = (
    "ready_verified",
    "ready_unverified",
    "needs_fix_code",
    "needs_fix_tests",
    "metadata_only_warning",
    "process_failed",
)
BLOCKING_READY_TIERS = {"needs_fix_code", "needs_fix_tests", "process_failed"}


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
    if workflow_profile in {
        STRUCTURED_PROFILE,
        STRUCTURED_GATED_PROFILE,
        STRUCTURED_MODERATED_PROFILE,
    }:
        return VERIFY_DIFF_CHECK
    return VERIFY_NONE


def validate_generated_workflow(workflow: str, *, workflow_profile: str) -> None:
    """Validate invariants the eval harness relies on before launching Fabro."""
    if workflow_profile not in {
        STRUCTURED_PROFILE,
        STRUCTURED_GATED_PROFILE,
        STRUCTURED_MODERATED_PROFILE,
    }:
        return

    expected_by_node = {
        "verify": f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}",
        "snapshot_patch": f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}",
        "audit": f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}",
        "review": f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}",
        "fixup": f"max_visits={STRUCTURED_FIXUP_MAX_VISITS}",
    }
    if workflow_profile == STRUCTURED_GATED_PROFILE:
        expected_by_node["test_evidence_gate"] = (
            f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}"
        )
    if workflow_profile == STRUCTURED_MODERATED_PROFILE:
        expected_by_node.pop("review")
        expected_by_node["test_evidence_gate"] = (
            f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}"
        )
        expected_by_node["adversarial_review"] = (
            f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}"
        )
        expected_by_node["moderator_filter"] = (
            f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}"
        )
        expected_by_node["acceptance_audit"] = (
            f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}"
        )
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
        return _structured_workflow(
            graph_name,
            setup_script,
            verify_mode,
            include_test_evidence_gate=False,
        )
    if workflow_profile == STRUCTURED_GATED_PROFILE:
        return _structured_workflow(
            graph_name,
            setup_script,
            verify_mode,
            include_test_evidence_gate=True,
            include_moderated_review=False,
        )
    if workflow_profile == STRUCTURED_MODERATED_PROFILE:
        return _structured_workflow(
            graph_name,
            setup_script,
            verify_mode,
            include_test_evidence_gate=True,
            include_moderated_review=True,
        )
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


def _structured_workflow(
    graph_name: str,
    setup_script: str,
    verify_mode: str,
    *,
    include_test_evidence_gate: bool,
    include_moderated_review: bool = False,
) -> str:
    gate_node = ""
    review_nodes = f'''    review        [label="Review", goal_gate=true, max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, output_schema="routing", prompt="{dot_escape(_review_prompt())}"]
'''
    gate_edges = """    snapshot_patch -> audit -> review
    review -> extract_patch [label="Approve"]
    review -> fixup        [label="Fix"]"""
    if include_test_evidence_gate:
        gate_node = f'''    test_evidence_gate [label="Test Evidence Gate", shape=parallelogram, goal_gate=true, max_retries=0, max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, script="{dot_escape(_test_evidence_gate_script())}"]
'''
        gate_edges = """    snapshot_patch -> audit -> test_evidence_gate
    test_evidence_gate -> review [condition="outcome=succeeded"]
    test_evidence_gate -> fixup  [condition="outcome=failed"]
    test_evidence_gate -> fixup  [label="Fallback"]
    review -> extract_patch [label="Approve"]
    review -> fixup        [label="Fix"]"""
    if include_moderated_review:
        review_nodes = f'''    adversarial_review [label="Adversarial Review", max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, prompt="{dot_escape(_adversarial_review_prompt())}"]
    moderator_filter   [label="Moderator Filter", max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, prompt="{dot_escape(_moderator_filter_prompt())}"]
    acceptance_audit   [label="Acceptance Audit", goal_gate=true, max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, output_schema="routing", prompt="{dot_escape(_acceptance_audit_prompt())}"]
'''
        gate_edges = """    snapshot_patch -> audit -> test_evidence_gate
    test_evidence_gate -> adversarial_review [condition="outcome=succeeded"]
    test_evidence_gate -> fixup              [condition="outcome=failed"]
    test_evidence_gate -> fixup              [label="Fallback"]
    adversarial_review -> moderator_filter -> acceptance_audit
    acceptance_audit -> extract_patch [label="Approve"]
    acceptance_audit -> fixup         [label="Fix"]"""
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
{gate_node}{review_nodes}    fixup         [label="Fixup", max_visits={STRUCTURED_FIXUP_MAX_VISITS}, prompt="{dot_escape(_fixup_prompt())}"]
    extract_patch [label="Extract Patch", shape=parallelogram, script="git diff"]
    start -> setup -> research -> implement -> verify
    verify -> snapshot_patch [condition="outcome=succeeded"]
    verify -> fixup        [condition="outcome=failed"]
    verify -> fixup        [label="Fallback"]
{gate_edges}
    fixup -> verify
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


def _adversarial_review_prompt() -> str:
    return """Adversarially review the patch before export.

This is pass 1 of a three-stage review pattern. Your job is discovery, not final
approval. Be maximally critical and look for plausible failure modes, missing
tests, overbroad changes, and evidence gaps. You may over-report. A later
moderator will reject weak or unsupported objections.

Hard contract:
- Do not ask the user questions.
- Do not call request_user_input or any interactive clarification tool.
- Do not modify repository files.
- Use read-only inspection only: git diff, file reads, searches, and recorded
  artifacts/logs are allowed.
- Do not run tests, compilers, formatters, installers, or commands that may write
  caches or mutate the repository.
- Machine artifacts outrank agent-authored claims. Treat {DIFF_AUDIT_PATH},
  {TEST_EVIDENCE_GATE_PATH}, and the current `git diff` as stronger evidence
  than narrative summaries.

Inspect:
- the issue goal and /tmp/fabro-research.md if present;
- {VALIDATION_CONTRACT_PATH};
- {DIFF_AUDIT_PATH};
- {TEST_EVIDENCE_GATE_PATH};
- the current `git diff` and touched source/tests.

Write {ADVERSARIAL_REVIEW_PATH} with a single JSON object:
{
  "schema_version": 1,
  "stage": "adversarial_review",
  "summary": "<one sentence>",
  "rows": [
    {
      "id": "A1",
      "category": "code|tests|metadata|process",
      "severity": "blocker|major|minor|info",
      "failure_mode": "<specific possible failure>",
      "evidence": ["<file/path, diff fact, artifact field, or command claim>"],
      "falsifiable_check": "<minimal read-only or future executable check>",
      "why_it_matters": "<impact if true>"
    }
  ],
  "overall_risk": "low|medium|high"
}

End with exactly the same JSON object on one line. Do not include Markdown.""".replace(
        "{VALIDATION_CONTRACT_PATH}", VALIDATION_CONTRACT_PATH
    ).replace(
        "{DIFF_AUDIT_PATH}", DIFF_AUDIT_PATH
    ).replace(
        "{TEST_EVIDENCE_GATE_PATH}", TEST_EVIDENCE_GATE_PATH
    ).replace(
        "{ADVERSARIAL_REVIEW_PATH}", ADVERSARIAL_REVIEW_PATH
    )


def _moderator_filter_prompt() -> str:
    return """Moderate the adversarial review.

This is pass 2 of a three-stage review pattern. Your job is to filter. Keep only
objections that are supported by repository evidence, the current diff, or
machine artifacts. Reject plausible-sounding but unsupported criticism. Do not
invent new objections; you may only accept, downgrade, or reject rows from
{ADVERSARIAL_REVIEW_PATH}.

Hard contract:
- Do not ask the user questions.
- Do not call request_user_input or any interactive clarification tool.
- Do not modify repository files.
- Use read-only inspection only. Do not run tests, compilers, formatters,
  installers, or commands that may write caches or mutate the repository.
- Machine artifacts outrank agent-authored claims. Treat {DIFF_AUDIT_PATH},
  {TEST_EVIDENCE_GATE_PATH}, and the current `git diff` as authoritative when
  they conflict with narrative stage output.

Write {MODERATOR_FILTER_PATH} with a single JSON object:
{
  "schema_version": 1,
  "stage": "moderator_filter",
  "dispositions": [
    {
      "id": "A1",
      "disposition": "confirmed|downgraded|rejected",
      "category": "code|tests|metadata|process",
      "severity": "blocker|major|minor|info",
      "routing_effect": "fix_code|fix_tests|metadata_warning|none",
      "evidence_grade": "strong|weak|unsupported",
      "reason": "<why this disposition is evidence-bound>"
    }
  ],
  "readiness_tier": "ready_verified|ready_unverified|needs_fix_code|needs_fix_tests|metadata_only_warning|process_failed",
  "do_not_repeat": ["<specific failed approach to avoid>"],
  "next_agent_guidance": "<concrete guidance if a downstream agent continues>"
}

Readiness tier rules:
- needs_fix_code: any confirmed blocker/major code issue.
- needs_fix_tests: any confirmed blocker/major test-evidence issue with
  testable behavior.
- process_failed: missing/malformed required review artifacts or hard process
  contradiction.
- metadata_only_warning: only metadata/process rows remain and the diff itself
  appears exportable.
- ready_verified: no blocking rows remain and machine-visible test evidence is
  credible.
- ready_unverified: no blocking rows remain but validation is weak, unavailable,
  failed for environment reasons, or claim-only.

End with exactly the same JSON object on one line. Do not include Markdown.""".replace(
        "{ADVERSARIAL_REVIEW_PATH}", ADVERSARIAL_REVIEW_PATH
    ).replace(
        "{MODERATOR_FILTER_PATH}", MODERATOR_FILTER_PATH
    ).replace(
        "{DIFF_AUDIT_PATH}", DIFF_AUDIT_PATH
    ).replace(
        "{TEST_EVIDENCE_GATE_PATH}", TEST_EVIDENCE_GATE_PATH
    )


def _acceptance_audit_prompt() -> str:
    return """Audit the moderated review and decide whether the patch can be exported.

This is pass 3 of a three-stage review pattern. Your job is not to find new
issues. Audit the adversarial review and moderator filter already present in
context, verify that the moderator's surviving rows are evidence-bound, then
make the final route decision.

Hard contract:
- Do not ask the user questions.
- Do not call request_user_input or any interactive clarification tool.
- Do not modify repository files.
- Use read-only inspection only. Do not run tests, compilers, formatters,
  installers, or commands that may write caches or mutate the repository.
- Do not introduce new findings. You may only accept, reject, or downgrade
  findings already raised by the adversarial review and handled by the moderator.
- Machine artifacts outrank agent-authored claims. Treat {DIFF_AUDIT_PATH},
  {TEST_EVIDENCE_GATE_PATH}, and the current `git diff` as authoritative when
  they conflict with narrative stage output.

If /tmp review JSON files exist, read them. If they do not exist, use the prior
stage outputs in context; missing /tmp files alone should be a metadata/process
warning, not a reason to reject an otherwise evidence-backed patch.
If you can write files without touching the repository, also write the final
routing object to {ACCEPTANCE_AUDIT_PATH} and the nested review_ledger object to
{REVIEW_LEDGER_PATH}; the final response JSON is still the source of truth.

End with exactly one routing JSON object on one line:
{
  "preferred_next_label": "Approve|Fix",
  "outcome": "succeeded|failed",
  "status": "passed|failed",
  "mode": "moderated-review",
  "readiness_tier": "ready_verified|ready_unverified|needs_fix_code|needs_fix_tests|metadata_only_warning|process_failed",
  "failure_reason": null,
  "route_decision": "export|fixup",
  "blocking_rows": [],
  "do_not_repeat": ["<specific failed approach to avoid>"],
  "next_agent_guidance": "<concrete downstream guidance>",
  "review_ledger": {
    "schema_version": 1,
    "stage": "review_ledger",
    "status": "passed|failed",
    "readiness_tier": "ready_verified|ready_unverified|needs_fix_code|needs_fix_tests|metadata_only_warning|process_failed",
    "route_decision": "export|fixup",
    "confirmed_rows": [],
    "blocking_rows": [],
    "malformed_artifacts": []
  }
}

Decision rules:
- Use Approve/succeeded/passed/export for ready_verified, ready_unverified, or
  metadata_only_warning.
- Use Fix/failed/failed/fixup for needs_fix_code, needs_fix_tests, or
  process_failed.
- ready_verified requires credible machine-visible validation.
- ready_unverified is acceptable when no blocking issue remains but validation is
  weak, unavailable, failed for environment reasons, or claim-only.
- metadata_only_warning is acceptable only when the actual diff is ready and the
  remaining issue is stale/missing review metadata.
- needs_fix_code or needs_fix_tests must include blocking_rows, do_not_repeat,
  and next_agent_guidance so another agent can continue from this patch.""".replace(
        "{DIFF_AUDIT_PATH}", DIFF_AUDIT_PATH
    ).replace(
        "{TEST_EVIDENCE_GATE_PATH}", TEST_EVIDENCE_GATE_PATH
    ).replace(
        "{ACCEPTANCE_AUDIT_PATH}", ACCEPTANCE_AUDIT_PATH
    ).replace(
        "{REVIEW_LEDGER_PATH}", REVIEW_LEDGER_PATH
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


def _test_evidence_gate_script() -> str:
    return build_embedded_gate_script(
        audit_path=DIFF_AUDIT_PATH,
        contract_path=VALIDATION_CONTRACT_PATH,
        output_path=TEST_EVIDENCE_GATE_PATH,
    )


def _acceptance_audit_script() -> str:
    ready_values = ",".join(READY_TIERS)
    blocking_values = ",".join(sorted(BLOCKING_READY_TIERS))
    return f"""python - <<'PY'
import json
from pathlib import Path

ADVERSARIAL = Path("{ADVERSARIAL_REVIEW_PATH}")
MODERATOR = Path("{MODERATOR_FILTER_PATH}")
GATE = Path("{TEST_EVIDENCE_GATE_PATH}")
OUT = Path("{ACCEPTANCE_AUDIT_PATH}")
LEDGER = Path("{REVIEW_LEDGER_PATH}")
READY_VALUES = set("{ready_values}".split(","))
BLOCKING_VALUES = set("{blocking_values}".split(","))


def load(path):
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        return {{"_error": str(exc), "_path": str(path)}}


adversarial = load(ADVERSARIAL)
moderator = load(MODERATOR)
gate = load(GATE)
malformed = []
if "_error" in adversarial:
    malformed.append({{"path": str(ADVERSARIAL), "error": adversarial.get("_error")}})
if "_error" in moderator:
    malformed.append({{"path": str(MODERATOR), "error": moderator.get("_error")}})
if "_error" in gate:
    malformed.append({{"path": str(GATE), "error": gate.get("_error")}})

readiness = moderator.get("readiness_tier") if isinstance(moderator, dict) else None
if readiness not in READY_VALUES:
    malformed.append({{"path": str(MODERATOR), "error": "missing_or_invalid_readiness_tier"}})
    readiness = "process_failed"
if isinstance(gate, dict) and gate.get("status") not in (None, "passed"):
    readiness = "process_failed"

dispositions = moderator.get("dispositions") if isinstance(moderator, dict) else []
if not isinstance(dispositions, list):
    dispositions = []
confirmed = [
    row for row in dispositions
    if isinstance(row, dict) and row.get("disposition") in ("confirmed", "downgraded")
]
blocking_rows = [
    row for row in confirmed
    if row.get("routing_effect") in ("fix_code", "fix_tests")
]

if malformed:
    readiness = "process_failed"

status = "failed" if readiness in BLOCKING_VALUES else "passed"
audit = {{
    "schema_version": 1,
    "status": status,
    "mode": "moderated-review",
    "readiness_tier": readiness,
    "malformed_artifacts": malformed,
    "confirmed_rows": len(confirmed),
    "blocking_rows": blocking_rows,
    "failure_reason": None,
    "route_decision": "fixup" if status == "failed" else "export",
    "do_not_repeat": moderator.get("do_not_repeat") if isinstance(moderator, dict) else None,
    "next_agent_guidance": moderator.get("next_agent_guidance") if isinstance(moderator, dict) else None,
}}
if status == "failed":
    if malformed:
        audit["failure_reason"] = "review artifacts missing or malformed"
    elif blocking_rows:
        audit["failure_reason"] = blocking_rows[0].get("reason") or "moderator found blocking issue"
    else:
        audit["failure_reason"] = "moderated review blocked export"

ledger = {{
    "schema_version": 1,
    "stage": "review_ledger",
    "readiness_tier": readiness,
    "status": status,
    "adversarial_row_count": len(adversarial.get("rows", [])) if isinstance(adversarial, dict) and isinstance(adversarial.get("rows"), list) else None,
    "moderator_disposition_count": len(dispositions),
    "confirmed_rows": confirmed,
    "blocking_rows": blocking_rows,
    "malformed_artifacts": malformed,
    "route_decision": audit["route_decision"],
    "do_not_repeat": audit.get("do_not_repeat"),
    "next_agent_guidance": audit.get("next_agent_guidance"),
}}
audit["review_ledger"] = ledger
OUT.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\\n")
LEDGER.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\\n")
print(json.dumps(audit, sort_keys=True))
raise SystemExit(0 if status == "passed" else 1)
PY
"""
