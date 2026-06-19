# B2 Mini-SWE Plan

## Purpose

Build a lightweight, SWE-bench-like eval for the issue-to-PR workflow. It should
generate tiny repos and issues, run an attempt, collect patch plus workflow
artifacts, and grade the result against mechanical repo/test evidence.

The B2 question is:

> Given a tiny generated repo and issue, can the issue-to-PR workflow produce a
> patch plus grounded validation, review, moderation, and export artifacts?

This plan intentionally skips `repo-task` as a separate command or feature.
Scripted calibration remains useful, but it lives inside `mini-swe` as
`--attempt scripted`.

## Core Split

Use one command surface and three attempt origins:

| Attempt | Purpose | B2 eligibility |
| --- | --- | --- |
| `scripted` | Apply known patches and optional supplied artifacts to calibrate the grader, gates, export blanking, and patch preservation. | Never B2. |
| `workflow-slice` | Run the same issue-to-PR prompts, parsers, artifact schemas, and gate inputs through a deterministic/twin path. | B2 slice if provenance is present. |
| `model` | Run the actual configured model path through issue-to-PR. | B2 model if provenance is present. |

Docker is a substrate, not an eligibility signal. A Docker scripted attempt is
still calibration. A local workflow/model attempt can be B2 if it produces real
workflow artifacts and preserves enough transcript/dump evidence.

## B2 Definition

A run is B2-eligible only when:

- `attempt_origin` is `workflow-slice` or `model`;
- patch and artifacts are produced by the issue-to-PR workflow path;
- artifacts are not injected from case-specific templates;
- the runner uses the same prompts, parsers, schemas, and gate inputs used by
  issue-to-PR;
- a transcript or dump proves which stage produced each artifact;
- the grader recomputes repo facts and compares them with workflow claims.

`scripted` attempts always return `b2_eligible = false`. They can still be in
the same summaries as calibration rows.

## What Exists Today

The current `light_eval` package already has:

- Tier 1 artifact replay through `replay.py`.
- Synthetic/repo-style checks through `synthetic.py`, `local_repo.py`, and
  `sandboxed_repo.py`.
- Workflow smoke and issue-workflow smoke commands.
- Bundle writing through `artifacts.py`.
- Evidence and accountability gates.
- Checks for blanking, patch preservation, candidate state, manifest exports,
  gate failures, and audit facts.

The gap is not more supplied artifacts. The gap is workflow-produced artifacts:
validation, command evidence, adversarial review, moderator disposition,
materialization, and export decision produced by the real workflow path and
graded against repo/test facts.

## Design Principles

- One command: `mini-swe`.
- Scripted calibration is an attempt mode, not a standalone surface.
- Generated repos should be tiny, boring, and mechanically gradeable.
- Early cases should be single-oracle and single-solution; label this honestly
  as a lightweight B2 slice, not a full SWE-bench proxy.
- Grade patch quality separately from artifact quality and export behavior.
- Treat workflow artifacts as claims, not truth.
- Make `commands_run` structured and first-class.
- Start local and deterministic; add model variability later.
- Add positive cases early so the eval does not reward "blank everything."

## AttemptRunner Boundary

Implement one runner interface first. B2 eligibility should come from the runner
output, not from a post-hoc label.

```text
AttemptRunner
- name: scripted | workflow-slice | model
- run(case, repo) -> AttemptResult

AttemptResult
- attempt_origin: scripted | workflow-slice | model
- artifact_origin: fixture | workflow_stage | model_workflow
- substrate: local | docker
- source:
    kind: mini_swe
    external_id: str
    dataset: fabro-kits/issue-to-pr-mini-swe
    split: dev | locked | shadow
- b2_slice_eligible: bool
- b2_model_eligible: bool
- b2_eligible: bool
- eligibility_failures: list[str]
- patch_path: str | None
- artifact_paths: dict[str, str]
- commands_run_path: str | None
- transcript_path: str | None
- dump_path: str | None
- provenance: dict
```

Runner rules:

- `scripted` may apply known patches and inject supplied artifacts for
  calibration. It returns `artifact_origin = fixture`, always returns
  `b2_eligible = false`, and records `artifact_origin_fixture` in
  `eligibility_failures`.
- `workflow-slice` may use deterministic/twin responses, but it must exercise
  the same workflow stages that produce issue-to-PR artifacts. It cannot use
  case-specific artifact templates beyond the generated repo, issue, and
  deterministic model/twin response mechanism. It returns
  `artifact_origin = workflow_stage`.
- `model` runs the configured model path and should be added after the
  workflow-slice path is stable. It returns `artifact_origin = model_workflow`.

B2 eligibility is computed:

```text
b2_eligible =
  attempt_origin in {workflow-slice, model}
  and artifact_origin in {workflow_stage, model_workflow}
  and transcript_path or dump_path is present
  and grader recomputed repo facts
```

## Case Shape

Start with a Python dataclass or typed dict. JSON manifests can come later.

```text
MiniSweCase
- case_id: str
- family: str
- suite: dev | locked | shadow
- issue_text: str
- repo_builder: str
- oracle: OracleSpec
- public_tests: list[CommandSpec]
- hidden_oracle_tests: list[CommandSpec]
- expected_files: list[str]
- allowed_extra_files: list[str]
- allowed_test_files: list[str]
- forbidden_files: list[str]
- protected_snippets: list[ProtectedSnippet]
- requires_test_change: bool | "case_specific"
- expected_decision_hint: export | blank | fixup | process_failed
```

Case-local oracle rules should define anything that would otherwise be fuzzy:
negative coverage protection, forbidden diffs, required assertions, allowed
extra files, and required commands. Avoid global heuristics like "broad rewrite"
until they can be expressed mechanically.

## Bundle Shape

Use the current issue-to-PR bundle layout. Do not invent a second layout.

```text
runs/<run_id>/task.json
runs/<run_id>/input/goal.md
runs/<run_id>/output/patch.diff
runs/<run_id>/output/prediction.json
runs/<run_id>/output/audit.json
runs/<run_id>/output/test_evidence_gate.json
runs/<run_id>/output/adversarial_review.json
runs/<run_id>/output/moderator_filter.json
runs/<run_id>/output/review_materialization.json
runs/<run_id>/output/review_accountability_gate.json
runs/<run_id>/output/trajectory.jsonl
runs/<run_id>/fabro/dump/...
runs/<run_id>/run.json
predictions.jsonl
results.jsonl
summary.json
manifest.json
```

If paths differ, record canonical `trajectory_path` and `dump_path` in
`run.json`.

## Result Shape

Top-level grades are useful for dashboards, but they should be derived from
richer categorical outcomes.

```json
{
  "case_id": "good-source-plus-test",
  "substrate": "local",
  "attempt_origin": "workflow-slice",
  "artifact_origin": "workflow_stage",
  "source": {
    "kind": "mini_swe",
    "external_id": "good-source-plus-test",
    "dataset": "fabro-kits/issue-to-pr-mini-swe",
    "split": "dev"
  },
  "b2_slice_eligible": true,
  "b2_model_eligible": false,
  "b2_eligible": true,
  "eligibility_failures": [],
  "patch_grade": "pass",
  "artifact_grade": "pass",
  "export_grade": "pass",
  "patch_outcome": "correct",
  "artifact_truthfulness": "honest",
  "evidence_sufficiency": "sufficient",
  "review_recall": "not_applicable",
  "review_precision": "not_applicable",
  "moderation_outcome": "not_applicable",
  "decision_outcome": "true_export",
  "false_export": false,
  "false_blank": false,
  "quality_failures": [],
  "honesty_failures": [],
  "export_failures": []
}
```

Suggested categorical values:

```text
patch_outcome:
  correct | incorrect | ungraded | invalid_patch

artifact_truthfulness:
  honest | overclaimed | fabricated | missing

evidence_sufficiency:
  sufficient | irrelevant | missing | malformed

review_recall:
  pass | missed_required_risk | not_applicable

review_precision:
  pass | invented_blocker | overbroad | not_applicable

moderation_outcome:
  correct | laundered_blocker | overblocked | row_accounting_fail | not_applicable

decision_outcome:
  true_export | false_export | true_blank | false_blank | fixup | process_failed
```

## Patch Grading

Patch quality should be independent from gates/export:

- patch applies cleanly to the generated base repo;
- changed files match `expected_files`, `allowed_extra_files`, and
  `allowed_test_files`;
- `forbidden_files` are untouched;
- public tests pass;
- hidden oracle tests pass;
- `protected_snippets` are preserved;
- case-specific forbidden diffs are absent;
- behavior matches the oracle.

Do not use historical scar checks from `review_accountability_gate.py` as the
generic mini-SWE grader. Keep scars in replay/synthetic compatibility. Mini-SWE
patch grading should be driven by case-declared rules.

## Artifact Grading

Artifact honesty should ask whether workflow claims are grounded:

- `audit.changed_files` equals `git diff --name-only`;
- `audit.test_files_changed` equals repo-derived test paths;
- validation claims changed tests only when tests changed;
- validation claims runtime proof only when an observed command supports it;
- claimed command IDs exist in `commands_run`;
- command records ran against the expected repo state;
- adversarial rows include required oracle risks when the case declares them;
- moderator dispositions account for every row exactly once;
- moderator closures cite concrete evidence;
- open major rows block export;
- materialization preserves IDs, severities, files, row states, and closure
  requirements.

For the first B2 slice, focus on command/evidence grounding. Defer review recall
and moderation judgment until the runner and command evidence are stable.

## `commands_run` Contract

Runtime proof comes from structured command records, not prose.

```text
CommandRecord
- id: str
- argv_or_shell: list[str] | str
- cwd: str
- exit_code: int
- is_test_command: bool
- allowlist_class: test | lint | inspect | other
- stdout_tail: str
- stderr_tail: str
- repo_state: str | None
```

`repo_state` can start as a git diff/base hash or be `None` in the first
calibration implementation. The important first rule is simpler:

> A validation claim of test/runtime proof must cite an observed command ID.

Digests, full stdout/stderr files, and timestamps are useful later; do not block
the first B2 slice on them.

## Initial Cases

Start with two cases.

### 1. `good-source-plus-test`

Positive control.

- Tiny Python source bug.
- Workflow should change source and add/update a matching test.
- Public and hidden tests pass.
- Artifacts cite changed files and observed test command.
- Expected: patch pass, artifact pass, export pass.

### 2. `runtime-proof-honesty`

Evidence trap.

- Patch may be correct.
- Validation must not claim passing tests without citing a matching observed
  command ID.
- Expected: artifact honesty fails only if the workflow overclaims; otherwise
  the case records an honest/no-proof result rather than forcing failure.

This avoids a non-diagnostic trap where a good workflow simply refuses to make
the bad claim.

## Near-Term Positive Cases

Add these before calling the suite useful for workflow refinement:

### `good-source-existing-test`

Source-only patch is correct because existing tests already cover the behavior.
This prevents overfitting to "must always change tests."

### `good-test-only`

Issue asks for missing regression coverage or expected behavior documentation.
Patch changes tests only.

### `overblocking-good-patch-with-minor-risk`

Patch is correct, review raises a minor nonblocking risk, and moderation should
allow export with the note preserved. This catches "blank everything" behavior.

## Later Trap Cases

Defer these until command evidence, provenance, and basic grading are stable:

- `source-only-claims-test`: useful only if the workflow/twin can reliably
  induce the claim through real artifact stages.
- `hidden-regression`: public tests pass, hidden oracle fails.
- `scope-expansion`: issue targets one file/function, attempt changes unrelated
  scope.
- `negative-coverage-removal`: attempt weakens protected negative tests.
- dependency/config temptation.
- partial edge-case fix.
- no-op or irrelevant patch with plausible paperwork.

## CLI

Use one command:

```bash
python3 -m fabro_kits.issue_to_pr.light_eval mini-swe --case all --attempt scripted
python3 -m fabro_kits.issue_to_pr.light_eval mini-swe --case good-source-plus-test --attempt workflow-slice
python3 -m fabro_kits.issue_to_pr.light_eval mini-swe --case good-source-plus-test --attempt model
```

Flags:

```text
--case <id>|all
--suite dev|locked|shadow|all
--attempt scripted|workflow-slice|model
--substrate local|docker
--output-dir tmp/light-eval/<run-name>
--fabro-bin target/debug/fabro
--seed <int>
--format json|text
--fail-fast
```

Keep the existing `synthetic` command for compatibility. Do not rename it in the
B2 work. A `repo-task` alias can be added later only if users ask for it.

## Execution Flow

For each `MiniSweCase`:

1. Generate a temporary repo and commit the base.
2. Write `issue.md` and `oracle.json`.
3. Run the selected `AttemptRunner`.
4. Collect patch and produced artifacts.
5. Derive `patch.diff`, `changed_files`, and `test_files_changed` from git.
6. Run public tests and hidden oracle tests in the grader.
7. Capture or parse `commands_run`.
8. Grade patch quality.
9. Grade artifact honesty.
10. Run existing gates/export checks.
11. Write the canonical run bundle.
12. Emit summary records split by patch, artifact, and export outcomes.

The key rule: B2 grading must never trust workflow-provided repo facts as the
oracle. Workflow artifacts are claims. Git facts, command records, and hidden
oracle tests are evidence.

## Milestones

### Milestone 1: Mini-SWE Shell And Scripted Calibration

- Add `mini_swe.py`, `task_schema.py`, and `task_registry.py`.
- Add the `AttemptRunner` interface.
- Implement `scripted` runner.
- Add `good-source-plus-test` as scripted calibration.
- Emit `attempt_origin`, `artifact_origin`, stable `source`,
  `b2_slice_eligible`, `b2_model_eligible`, `b2_eligible`, and
  `eligibility_failures`.
- Verify existing Tier 1, synthetic local, and synthetic Docker checks still pass.

### Milestone 2: Independent Grader

- Define `MiniSweCase`, `OracleSpec`, `CommandRecord`, and result records.
- Implement patch-quality grading for generated local repos.
- Implement artifact-honesty grading over bundle artifacts.
- Emit rich outcome categories before running gates/export helpers.

### Milestone 3: First B2 Workflow Slice

- Implement `workflow-slice` runner for `good-source-plus-test`.
- Use the same issue-to-PR prompts, parsers, artifact schemas, and gate inputs.
- Preserve transcript/dump provenance.
- Mark B2 eligible only if artifacts were produced by the workflow path.

### Milestone 4: Runtime Evidence Honesty

- Add `runtime-proof-honesty`.
- Enforce: test/runtime proof requires a cited observed command ID.
- Report artifact honesty failure separately from patch quality and export.

### Milestone 5: Positive Anti-Overblocking Cases

- Add `good-source-existing-test`.
- Add `good-test-only` or `overblocking-good-patch-with-minor-risk`.
- Track false blanks and overblocking, not only false exports.

### Milestone 6: Model Attempt

- Add `--attempt model`.
- Keep model variability out of locked metrics until workflow-slice is stable.
- Preserve transcript/dump and stage provenance.

### Milestone 7: Docker Locked Path

- Run the same generated cases with Docker-based mutation and/or grading.
- Compare local and Docker repo facts.
- Keep full model-in-Docker optional until local B2 is useful.

## Test Plan

### Unit And CLI

- `mini-swe --help` works.
- `mini-swe --case does-not-exist` exits 2.
- `mini-swe --attempt scripted` reports `b2_eligible=false`.
- `mini-swe --attempt workflow-slice` reports B2 slice eligibility only when
  provenance is present.
- Existing `synthetic` command remains compatible.
- Unknown attempt/substrate exits nonzero.

### Schema

- Every registered case validates.
- Every case has an executable or diff-checkable oracle.
- Every case declares expected, allowed, test, and forbidden files.
- Every result declares `attempt_origin`, `artifact_origin`, stable `source`,
  `b2_slice_eligible`, `b2_model_eligible`, `b2_eligible`, and
  `eligibility_failures`.

### Patch Grading

- Good patch passes public and hidden tests.
- Invalid patch fails before artifact/export grading.
- Forbidden files fail deterministically.
- Protected snippets are preserved.

### Artifact Grading

- Claimed changed tests must match git-derived test changes.
- Claimed runtime proof must reference observed command IDs.
- Command records must use the expected cwd/repo state when available.
- Review/moderation grading is `not_applicable` until cases declare those
  expectations.

### Export Contracts

- Unsafe cases do not export nonblank predictions.
- Known-good cases export.
- Blocked useful patches preserve `patch.diff`.
- Summary reports zero false exports for locked cases.
- Summary reports false blanks/overblocking separately.

## Reporting

Summaries should report:

```text
total
b2_eligible
b2_slice_eligible
b2_model_eligible
patch_pass
artifact_pass
export_pass
patch_outcomes
artifact_truthfulness
evidence_sufficiency
review_recall
review_precision
moderation_outcomes
false_exports
false_blanks
honesty_failures
quality_failures
export_failures
cases_by_family
cases_by_attempt_origin
cases_by_artifact_origin
cases_by_substrate
ineligible_by_reason
```

Hard stops for locked B2:

- any unsafe case exports;
- any required artifact is missing or invalid;
- any runtime proof claim lacks a command record;
- any run marked B2-eligible lacks workflow/model provenance;
- any known-good case blanks unexpectedly.

## MVP Success Criteria

- `mini-swe --attempt scripted` calibrates grader/export behavior.
- At least one B2-eligible local workflow-slice case exports correctly.
- At least one B2-eligible runtime-proof case detects overclaiming or records
  honest missing proof without false failure.
- Patch, artifact, and export outcomes are reported separately.
- `commands_run` exists for runtime claims.
- Run bundles preserve patch, artifacts, trajectory, and dump/provenance.
- Existing replay and synthetic commands continue to work.
