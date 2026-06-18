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
ARTIFACT_DIR = ".fabro/issue-to-pr"
VALIDATION_CONTRACT_PATH = f"{ARTIFACT_DIR}/validation.json"
DIFF_AUDIT_PATH = f"{ARTIFACT_DIR}/diff-audit.json"
TEST_EVIDENCE_GATE_PATH = f"{ARTIFACT_DIR}/test-evidence-gate.json"
ADVERSARIAL_REVIEW_PATH = f"{ARTIFACT_DIR}/adversarial-review.json"
MODERATOR_FILTER_PATH = f"{ARTIFACT_DIR}/moderator-filter.json"
REVIEW_MATERIALIZATION_PATH = f"{ARTIFACT_DIR}/review-materialization.json"
REVIEW_ACCOUNTABILITY_GATE_PATH = f"{ARTIFACT_DIR}/review-accountability-gate.json"


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
        expected_by_node["adversarial_artifact_gate"] = (
            f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}"
        )
        expected_by_node["moderator_filter"] = (
            f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}"
        )
        expected_by_node["materialize_review_artifacts"] = (
            f"max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}"
        )
        expected_by_node["review_accountability_gate"] = (
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
    adversarial_artifact_gate [label="Adversarial Artifact Gate", shape=parallelogram, goal_gate=true, max_retries=0, max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, script="{dot_escape(_adversarial_artifact_gate_script())}"]
    moderator_filter   [label="Moderator Filter", max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, prompt="{dot_escape(_moderator_filter_prompt())}"]
    materialize_review_artifacts [label="Materialize Review Artifacts", shape=parallelogram, goal_gate=true, max_retries=0, max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, script="{dot_escape(_review_materialization_script())}"]
    review_accountability_gate [label="Review Accountability Gate", shape=parallelogram, goal_gate=true, max_retries=0, max_visits={STRUCTURED_VERIFY_REVIEW_MAX_VISITS}, script="{dot_escape(_review_accountability_gate_script())}"]
'''
        gate_edges = """    snapshot_patch -> audit -> test_evidence_gate
    test_evidence_gate -> adversarial_review [condition="outcome=succeeded"]
    test_evidence_gate -> fixup              [condition="outcome=failed"]
    test_evidence_gate -> fixup              [label="Fallback"]
    adversarial_review -> adversarial_artifact_gate
    adversarial_artifact_gate -> moderator_filter [condition="outcome=succeeded"]
    adversarial_artifact_gate -> fixup           [condition="outcome=failed"]
    adversarial_artifact_gate -> fixup           [label="Fallback"]
    moderator_filter -> materialize_review_artifacts
    materialize_review_artifacts -> review_accountability_gate [condition="outcome=succeeded"]
    materialize_review_artifacts -> review_accountability_gate [condition="outcome=failed"]
    materialize_review_artifacts -> review_accountability_gate [label="Fallback"]
    review_accountability_gate -> extract_patch     [condition="outcome=succeeded"]
    review_accountability_gate -> fixup            [condition="outcome=failed"]
    review_accountability_gate -> fixup            [label="Fallback"]
"""
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
    return """Research only; do not edit repository files or ask questions. When uncertain, choose the smallest issue-scoped investigation path yourself.
Use read-only commands. Put notes in /tmp/fabro-research.md.
Write {VALIDATION_CONTRACT_PATH} with JSON fields:
	acceptance_criteria including requested release/changelog notes, risky_shortcuts, likely_files, test_plan,
research_assumptions. End with acceptance criteria and test plan.""".replace(
        "{VALIDATION_CONTRACT_PATH}", VALIDATION_CONTRACT_PATH
    )


def _implement_prompt() -> str:
    return """Fix the issue with the smallest defensible patch. Do not ask questions or call request_user_input.
	Use /tmp/fabro-research.md when present. Satisfy all acceptance criteria, including requested release/changelog notes, not only the title; if the contract names a release/changelog file, add one canonical note in the first patch. For testable behavior changes, change a regression test file in git diff; running existing tests alone is not enough.
Run the most relevant focused single-process test command; for Django prefer tracked files under `tests/` and class labels like `python tests/runtests.py file_storage.tests.FileStoragePermissions --settings=test_sqlite --verbosity 1 --parallel 1`, not package-local test files or pytest/django test. If bootstrap fails, fix the invocation before using weaker smoke evidence, and report only real exit results. Ensure `git diff --name-only` lists every claimed changed file; for new files use `git add -N` or edit tracked files.
For Django docs/ref/settings.txt, edit only the section named by the issue; verify the nearby heading before changing a Default line and revert unrelated hunks such as cache OPTIONS.
Before finishing, update {VALIDATION_CONTRACT_PATH}; preserve research fields and add changed_files, tests_added as objects with path/test_name_or_scope/behavior_guarded,
commands_run/status, no_test_justification, residual_risks, final_claims. Every changed test file must appear in tests_added or be reverted; commands_run alone is not enough.""".replace(
        "{VALIDATION_CONTRACT_PATH}", VALIDATION_CONTRACT_PATH
    )


def _review_prompt() -> str:
    return """Review read-only; do not ask questions, edit, or run mutating commands.
Treat {DIFF_AUDIT_PATH}, {VALIDATION_CONTRACT_PATH}, and git diff as evidence.
Check acceptance criteria, scope, tests, commands_run, residual_risks, final_claims.
Route Fix only for code_blocking or test_blocking. Metadata-only drift may approve.

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
    return """Adversarially review the patch, read-only. Do not ask questions,
modify files, or run mutating commands. Be critical; wrong requested release/changelog targets are major. Blocker/major rows must be bounded to the issue/current diff, not universal proof over all possible integrations.
Use the issue, /tmp/fabro-research.md, {VALIDATION_CONTRACT_PATH},
{DIFF_AUDIT_PATH}, {TEST_EVIDENCE_GATE_PATH}, git diff, and touched files.

Use a file-writing tool to write {ADVERSARIAL_REVIEW_PATH} with a single JSON object:
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
      "required_files": ["<optional exact repo-relative paths that must be changed to close this row>"],
      "closure_requires": "runtime_tests|changed_files|diff_evidence|none",
      "falsifiable_check": "<minimal read-only or future executable check>",
      "why_it_matters": "<impact if true>"
    }
  ],
  "overall_risk": "low|medium|high"
}

After writing, read {ADVERSARIAL_REVIEW_PATH} back and fix the file if it is missing, empty, or invalid JSON.
End with exactly the same JSON object on one line. Do not ask how to write it, include Markdown, or output the object twice.""".replace(
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

This is pass 2 of a three-stage review pattern. Filter only the rows from
{ADVERSARIAL_REVIEW_PATH}. Do not invent objections.

Hard contract:
- Do not ask or call request_user_input; if uncertain, keep the row open and write JSON.
- Do not modify repository files.
- Use read-only inspection only. Do not run tests or commands that may write.
- Machine artifacts outrank claims. Treat {DIFF_AUDIT_PATH}, {TEST_EVIDENCE_GATE_PATH}, and `git diff` as authoritative.
- First read {ADVERSARIAL_REVIEW_PATH}; if it is missing, empty, invalid JSON, or has no rows list, write process_failed with dispositions=[] and stop. Do not infer rows from chat history or other artifacts.

Use a file-writing tool to overwrite {MODERATOR_FILTER_PATH} with one JSON object before your final answer; escape literal backslashes as JSON \\\\; printing without writing fails:
{
  "schema_version": 1,
  "stage": "moderator_filter",
  "dispositions": [
    {
      "id": "A1",
      "state": "open|closed_by_evidence|rejected|downgraded",
      "category": "code|tests|metadata|process",
      "severity": "blocker|major|minor|info",
      "evidence": ["<required for closed_by_evidence or downgraded>"],
      "closure_check": "<required for closed/downgraded/rejected blocker|major>",
      "reason": "<why this disposition is evidence-bound>"
    }
  ],
  "readiness_tier": "ready_verified|ready_unverified|needs_fix_code|needs_fix_tests|metadata_only_warning|process_failed",
  "do_not_repeat": ["<specific failed approach to avoid>"],
  "next_agent_guidance": "<concrete guidance if a downstream agent continues>"
}

Rules:
- Every adversarial row needs exactly one same-id disposition and no extra IDs; empty is valid only with zero rows.
- Keep severe rows open when any required_files are absent from changed_files.
- Use open for unresolved blocker/major objections.
- Use closed_by_evidence only when cited evidence directly answers the row.
  Never close by denying cited git diff hunks; keep the row open unless current patch evidence disproves them.
- Use rejected only when the row is unsupported or demands universal proof beyond the issue contract. Use downgraded when representative issue-scoped evidence covers the concrete concern.
- Runtime-test proof requires machine-observed pass fields like tests_passed_count; never use test_evidence_gate.status, changed files, or commands_reported_passed_count to close test-execution rows or mark ready_verified.

After writing, read {MODERATOR_FILTER_PATH} back and fix the file if it is missing, empty, or invalid JSON.
End with exactly the same JSON object on one line. Do not ask how to write it, include Markdown, or output the object twice.""".replace(
        "{ADVERSARIAL_REVIEW_PATH}", ADVERSARIAL_REVIEW_PATH
    ).replace(
        "{MODERATOR_FILTER_PATH}", MODERATOR_FILTER_PATH
    ).replace(
        "{DIFF_AUDIT_PATH}", DIFF_AUDIT_PATH
    ).replace(
        "{TEST_EVIDENCE_GATE_PATH}", TEST_EVIDENCE_GATE_PATH
    )


def _fixup_prompt() -> str:
    return """A quality gate failed. Do not ask questions. Re-read the original goal,
/tmp/fabro-research.md, {VALIDATION_CONTRACT_PATH}, {DIFF_AUDIT_PATH}, and when
present {REVIEW_ACCOUNTABILITY_GATE_PATH}. Repair the whole patch, not only the
latest critic row. Metadata rows are not optional; keep one canonical release/changelog note. Testable behavior rows need changed regression tests in git diff; runtime-only evidence is not enough. Compatibility rows need representative tests with real fields/behavior, not only default or proxy-only paths. Inspect changed files for unrelated hunks. Address every
	fixup_required_rows and malformed_artifacts item; when an artifact names a
	path/check, repair that exact diff hunk before arguing it is stale. For Django docs/ref/settings.txt, verify the nearby section heading before changing a Default line and revert unrelated hunks such as cache OPTIONS. Then update
the validation contract with reviewer_objections, changed_files, commands_run,
residual_risks, and final_claims. Every changed test file must appear in tests_added or be reverted; commands_run alone is not enough. Ensure `git diff --name-only` lists every claimed changed file; for Django tests, use tracked files under `tests/`. Prefer focused single-process tests over broad
suites; for Django prefer class labels like `python tests/runtests.py file_storage.tests.FileStoragePermissions --settings=test_sqlite --verbosity 1 --parallel 1`, not pytest/django test. If the full issue contract remains broken,
keep it open.""".replace(
        "{VALIDATION_CONTRACT_PATH}", VALIDATION_CONTRACT_PATH
    ).replace(
        "{DIFF_AUDIT_PATH}", DIFF_AUDIT_PATH
    ).replace(
        "{REVIEW_ACCOUNTABILITY_GATE_PATH}", REVIEW_ACCOUNTABILITY_GATE_PATH
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
    or path.startswith("testing/")
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
for stale in ("{ADVERSARIAL_REVIEW_PATH}", "{MODERATOR_FILTER_PATH}", "{REVIEW_MATERIALIZATION_PATH}", "{REVIEW_ACCOUNTABILITY_GATE_PATH}"):
    if Path(stale).exists(): Path(stale).unlink()
out = Path("{DIFF_AUDIT_PATH}")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(audit, indent=2) + "\\n")
print(json.dumps(audit, sort_keys=True))
PY
"""


def _test_evidence_gate_script() -> str:
    return build_embedded_gate_script(
        audit_path=DIFF_AUDIT_PATH,
        contract_path=VALIDATION_CONTRACT_PATH,
        output_path=TEST_EVIDENCE_GATE_PATH,
    )


def _adversarial_artifact_gate_script() -> str:
    return f"""python - <<'PY'
import json; from pathlib import Path
SRC = Path("{ADVERSARIAL_REVIEW_PATH}"); OUT = Path("{REVIEW_ACCOUNTABILITY_GATE_PATH}")
try:
    obj = json.loads(SRC.read_text(encoding="utf-8")); error = "rows_not_list"
except Exception as exc:
    obj = None; error = str(exc)
ok = isinstance(obj, dict) and isinstance(obj.get("rows"), list)
bad = {{"artifact": "adversarial_review", "path": str(SRC), "error": error}}
report = {{"schema_version": 1, "stage": "adversarial_artifact_gate", "status": "passed" if ok else "failed", "process_status": "passed" if ok else "process_failed", "failure_reason": None if ok else "adversarial_review_file_missing_or_invalid", "process_failures": [] if ok else ["adversarial_review_file_missing_or_invalid"], "malformed_artifacts": [] if ok else [bad], "fixup_required_rows": [] if ok else [{{**bad, "reason": "Write valid JSON with a rows list before moderation."}}], "next_agent_guidance": "Proceed to moderator." if ok else f"Re-run review after fixing the patch; the adversarial reviewer must write {{SRC}} and read it back."}}
if not ok:
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
print(json.dumps(report, sort_keys=True))
raise SystemExit(0 if ok else 1)
PY
"""


def _review_materialization_script() -> str:
    return f"""python - <<'PY'
import json
import re
from pathlib import Path

SOURCES = {{
    "adversarial_review": Path("{ADVERSARIAL_REVIEW_PATH}"),
    "moderator_filter": Path("{MODERATOR_FILTER_PATH}"),
}}
OUT = Path("{REVIEW_MATERIALIZATION_PATH}")
OUT.parent.mkdir(parents=True, exist_ok=True)
REQUIRED_KEYS = {{
    "adversarial_review": ("schema_version", "stage", "summary", "rows", "overall_risk"),
    "moderator_filter": ("schema_version", "stage", "dispositions", "readiness_tier", "next_agent_guidance"),
}}

def load(name, path):
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return None, {{"artifact": name, "path": str(path), "error": str(exc)}}
    candidates = [text, re.sub(r'\\\\(?!["\\\\/bfnrtu])', r'\\\\\\\\', text)]
    decoder = json.JSONDecoder(strict=False)
    for candidate in candidates:
        try:
            value = json.loads(candidate, strict=False)
        except Exception:
            for index in reversed([idx for idx, char in enumerate(candidate) if char == "{{"]):
                try:
                    value, end = decoder.raw_decode(candidate[index:])
                except Exception:
                    continue
                if not candidate[index + end :].strip():
                    break
            else:
                continue
        if isinstance(value, dict):
            path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
            return value, None
        return None, {{"artifact": name, "path": str(path), "error": "expected_json_object"}}
    return None, {{"artifact": name, "path": str(path), "error": "invalid_json_object"}}

errors = []
loaded = {{}}
for name, path in SOURCES.items():
    value, error = load(name, path)
    loaded[name] = value or {{}}
    if error:
        errors.append(error)

for name, value in loaded.items():
    if not isinstance(value, dict):
        continue
    missing = [key for key in REQUIRED_KEYS[name] if key not in value]
    if missing:
        errors.append({{"artifact": name, "path": str(SOURCES[name]), "error": "missing_required_keys", "keys": missing}})
    if value.get("stage") not in (None, name):
        errors.append({{"artifact": name, "path": str(SOURCES[name]), "error": "wrong_stage", "stage": value.get("stage")}})

rows = loaded["adversarial_review"].get("rows")
dispositions = loaded["moderator_filter"].get("dispositions")
rows = rows if isinstance(rows, list) else []
dispositions = dispositions if isinstance(dispositions, list) else []
row_ids = [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id") not in (None, "")]
disposition_ids = [str(row.get("id")) for row in dispositions if isinstance(row, dict) and row.get("id") not in (None, "")]
status = "failed" if errors or (row_ids and not disposition_ids) or any(did not in row_ids for did in disposition_ids) else "passed"
report = {{
    "schema_version": 1,
    "stage": "review_materialization",
    "status": status,
    "sources": {{name: str(path) for name, path in SOURCES.items()}},
    "adversarial_row_ids": row_ids,
    "moderator_disposition_ids": disposition_ids,
    "artifacts": loaded,
    "errors": errors,
}}
OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
print(json.dumps(report, sort_keys=True))
raise SystemExit(1 if status != "passed" else 0)
PY
"""


def _review_accountability_gate_script() -> str:
    return f"""python - <<'PY'
import json, subprocess
from pathlib import Path

ADVERSARIAL = Path("{ADVERSARIAL_REVIEW_PATH}")
MODERATOR = Path("{MODERATOR_FILTER_PATH}")
TEST_GATE = Path("{TEST_EVIDENCE_GATE_PATH}")
MATERIALIZATION = Path("{REVIEW_MATERIALIZATION_PATH}")
OUT = Path("{REVIEW_ACCOUNTABILITY_GATE_PATH}")
OUT.parent.mkdir(parents=True, exist_ok=True)
MAJOR = {{"blocker", "critical", "major"}}
STATES = {{"open", "closed_by_evidence", "rejected", "downgraded"}}

def load(path):
    try:
        obj = json.loads(path.read_text())
    except Exception as exc:
        return None, {{"path": str(path), "error": str(exc)}}
    if not isinstance(obj, dict):
        return None, {{"path": str(path), "error": "expected_json_object"}}
    return obj, None

def row_id(row):
    value = row.get("id") if isinstance(row, dict) else None
    return None if value in (None, "") else str(value)

def as_list(value):
    return value if isinstance(value, list) else []

def clean_path(value):
    text = str(value).strip()
    return text[len("/workspace/"):] if text.startswith("/workspace/") else text

def has_evidence(row):
    if not isinstance(row, dict):
        return False
    for key in ("evidence", "evidence_citations", "cited_evidence"):
        value = row.get(key)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return bool(row.get("artifact_path") or row.get("artifact_field"))

def tests_executed_successfully(gate):
    if not isinstance(gate, dict):
        return False
    observed = gate.get("observed") if isinstance(gate.get("observed"), dict) else {{}}
    for key in ("tests_passed_count",):
        value = observed.get(key) or gate.get(key)
        if isinstance(value, int) and value > 0:
            return True
    return False

adversarial, adversarial_error = load(ADVERSARIAL)
moderator, moderator_error = load(MODERATOR)
test_gate, test_gate_error = load(TEST_GATE)
materialization, materialization_error = load(MATERIALIZATION)
malformed = [err for err in (
    adversarial_error,
    moderator_error,
    test_gate_error,
    materialization_error,
) if err]
if isinstance(materialization, dict) and materialization.get("status") != "passed":
    malformed.extend(as_list(materialization.get("errors")))
if not tests_executed_successfully(test_gate): malformed.append({{"artifact": "test_evidence_gate", "error": "tests_not_executed_successfully", "reason": "Run the changed or claimed tests and record a passing command in validation."}})
rows = as_list(adversarial.get("rows") if isinstance(adversarial, dict) else None)
dispositions = [] if adversarial_error else as_list(moderator.get("dispositions") if isinstance(moderator, dict) else None)
changed_files = {{clean_path(path) for path in as_list(test_gate.get("changed_files") if isinstance(test_gate, dict) else None)}}
settings_ref = subprocess.run(["git", "diff", "--", "docs/ref/settings.txt"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True).stdout
if ("``OPTIONS``" in settings_ref and "+Default: ``0o644``" in settings_ref and "Extra parameters to pass to the cache backend" in settings_ref) or settings_ref.count("+The numeric mode (i.e. ``0o644``) to set newly uploaded files to.") > 1: malformed.append({{"artifact": "patch", "path": "docs/ref/settings.txt", "error": "docs_settings_corruption", "check": "cache OPTIONS default changed or FILE_UPLOAD_PERMISSIONS text duplicated", "offending_diff": settings_ref[:1200]}})
if isinstance(adversarial, dict) and not isinstance(adversarial.get("rows", []), list):
    malformed.append({{"artifact": "adversarial_review", "field": "rows", "error": "expected_list"}})
if isinstance(moderator, dict) and not isinstance(moderator.get("dispositions", []), list):
    malformed.append({{"artifact": "moderator_filter", "field": "dispositions", "error": "expected_list"}})

row_by_id = {{}}
for row in rows:
    rid = row_id(row)
    if rid:
        row_by_id[rid] = row
    else:
        malformed.append({{"artifact": "adversarial_review", "field": "rows.id", "error": "missing_id"}})

seen = set()
duplicate_disposition_ids = []
orphan_dispositions = []
open_rows = []
closed_rows = []
downgraded_rows = []
rejected_rows = []
invalid_dispositions = []
closure_check_failures = []

for disposition in dispositions:
    if not isinstance(disposition, dict):
        malformed.append({{"artifact": "moderator_filter", "field": "dispositions", "error": "disposition_not_object"}})
        continue
    did = row_id(disposition)
    state = str(disposition.get("state") or disposition.get("disposition") or "").lower()
    if not did:
        invalid_dispositions.append(disposition)
        malformed.append({{"artifact": "moderator_filter", "field": "dispositions.id", "error": "missing_id"}})
        continue
    if did in seen:
        duplicate_disposition_ids.append(did)
    seen.add(did)
    if did not in row_by_id:
        orphan_dispositions.append(disposition)
    if state not in STATES:
        invalid_dispositions.append(disposition)
        continue
    severe = str(row_by_id.get(did, {{}}).get("severity", disposition.get("severity", ""))).lower() in MAJOR
    closure_check = str(disposition.get("closure_check", "")).strip()
    if severe and state in {{"closed_by_evidence", "downgraded", "rejected"}} and not closure_check and (state == "rejected" or not has_evidence(disposition) or not str(disposition.get("reason", "")).strip()):
        closure_check_failures.append(disposition)
    if severe and did in row_by_id and str(disposition.get("category", "")).lower() != str(row_by_id[did].get("category", "")).lower():
        disposition["category_mismatch"] = {{"row": row_by_id[did].get("category"), "disposition": disposition.get("category")}}
    missing_required = [path for path in (clean_path(path) for path in as_list(row_by_id.get(did, {{}}).get("required_files"))) if path not in changed_files]
    if severe and state in {{"closed_by_evidence", "downgraded", "rejected"}} and missing_required:
        disposition["missing_required_files"] = missing_required
        closure_check_failures.append(disposition)
    if severe and state in {{"closed_by_evidence", "downgraded", "rejected"}} and str(row_by_id.get(did, {{}}).get("closure_requires", "")).lower() == "runtime_tests" and not tests_executed_successfully(test_gate):
        disposition["missing_closure_requirement"] = "runtime_tests"
        closure_check_failures.append(disposition)
    if state == "open":
        open_rows.append(disposition)
    elif state == "closed_by_evidence":
        if not has_evidence(disposition):
            invalid_dispositions.append(disposition)
        closed_rows.append(disposition)
    elif state == "downgraded":
        if not has_evidence(disposition):
            invalid_dispositions.append(disposition)
        downgraded_rows.append(disposition)
    elif state == "rejected":
        rejected_rows.append(disposition)

unaccounted_rows = [row for rid, row in row_by_id.items() if rid not in seen]
unaccounted_major_rows = [
    row for row in unaccounted_rows
    if str(row.get("severity", "")).lower() in MAJOR
]
blocking_rows = [
    row for row in open_rows
    if str(row_by_id.get(row_id(row), row).get("severity", "")).lower() in MAJOR
]

process_failures = []
if malformed:
    process_failures.append("review_artifact_missing_or_malformed")
if any(isinstance(item, dict) and item.get("error") == "tests_not_executed_successfully" for item in malformed):
    process_failures.append("tests_not_executed_successfully")
if rows and not dispositions:
    process_failures.append("adversarial_rows_without_moderator_dispositions")
if unaccounted_major_rows:
    process_failures.append("unaccounted_major_adversarial_rows")
elif unaccounted_rows:
    process_failures.append("unaccounted_adversarial_rows")
if duplicate_disposition_ids:
    process_failures.append("duplicate_moderator_dispositions")
if invalid_dispositions:
    process_failures.append("invalid_moderator_dispositions")
if closure_check_failures:
    process_failures.append("invalid_severe_closure_checks")
if blocking_rows:
    process_failures.append("open_blocker_or_major_rows")
failed = bool(process_failures)
fixup_required_rows = (blocking_rows or closure_check_failures or unaccounted_major_rows or unaccounted_rows or invalid_dispositions) + malformed
readiness = "process_failed" if failed else (
    "ready_verified" if tests_executed_successfully(test_gate) else "ready_unverified"
)
report = {{"schema_version": 1, "stage": "review_accountability_gate", "status": "failed" if failed else "passed", "process_status": "process_failed" if failed else "passed", "preferred_next_label": "Fix" if failed else "Approve", "route_decision": "fixup" if failed else "export", "readiness_tier": readiness, "failure_reason": "; ".join(process_failures), "process_failures": process_failures, "adversarial_row_count": len(rows), "moderator_disposition_count": len(dispositions), "open_rows": open_rows, "closed_rows": closed_rows, "downgraded_rows": downgraded_rows, "rejected_rows": rejected_rows, "blocking_rows": blocking_rows, "fixup_required_rows": fixup_required_rows, "unaccounted_adversarial_rows": unaccounted_rows, "unaccounted_major_rows": unaccounted_major_rows, "duplicate_disposition_ids": duplicate_disposition_ids, "orphan_dispositions": orphan_dispositions, "invalid_dispositions": invalid_dispositions, "closure_check_failures": closure_check_failures, "tests_executed_successfully": tests_executed_successfully(test_gate), "materialization": materialization or {{}}, "malformed_artifacts": malformed, "do_not_repeat": ["Do not export until every blocker/major objection is open or closed with cited evidence."] if failed else [], "next_agent_guidance": "; ".join([str((item if isinstance(item, dict) else {{}}).get("falsifiable_check") or (item if isinstance(item, dict) else {{}}).get("check") or (item if isinstance(item, dict) else {{}}).get("reason") or item) for item in fixup_required_rows[:3]]) if failed else "Proceed to patch extraction."}}
OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n")
print(json.dumps(report, sort_keys=True))
raise SystemExit(1 if failed else 0)
PY
"""
