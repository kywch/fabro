# Issue-to-PR Glossary

This is the normative vocabulary for issue-to-PR docs, run artifacts, eval
reports, and workflow notes. When another issue-to-PR document uses one of
these terms, use the meaning here. If code behavior changes, update this file in
the same change.

The executable source of truth still lives in code, primarily
`fabro_kits.issue_to_pr` and `evals/swe-bench/run_eval.py`; this glossary is the
human-facing contract that keeps docs, examples, and reviews aligned with that
code.

## Naming

| Term | Meaning |
| --- | --- |
| issue-to-PR | Human-facing name for the workflow area that turns an issue or benchmark task into a patch/PR candidate. |
| `fabro_kits.issue_to_pr` | Python package name for reusable issue-to-PR helpers. Use underscores in code imports. |
| SWE-bench task | A benchmark instance used as an issue source. Its external id is recorded as `task_id` and `instance_id` in compatibility exports. |
| run bundle | Canonical product-facing output under `runs/<run_id>/`. It contains `run.json`, `task.json`, inputs, generated workflow config, output artifacts, and optional Fabro dumps. |
| root SWE-bench exports | Compatibility files at the output root: `predictions.jsonl`, `results.jsonl`, and `summary.json`. They are not the canonical product state. |
| live workspace artifact | Internal artifact written while the workflow runs, usually under `.fabro/issue-to-pr/` with hyphenated filenames. |
| bundled artifact | Copy or projection of a live artifact inside a run bundle, usually under `runs/<run_id>/output/` with underscore-based stage names. |

## Workflow Profiles

| Profile | Meaning |
| --- | --- |
| `simple` | Compatibility profile that keeps the compact solve-and-export path. |
| `structured` | Splits the attempt into setup, research, implement, verify, patch snapshot, audit, review, fixup, and extract stages. |
| `structured-gated` | `structured` plus `test_evidence_gate` between `audit` and `review`. |
| `structured-moderated` | `structured-gated` with adversarial review, moderator filtering, materialized review artifacts, and final review accountability before export. |

## Stages and Gates

| Stage or gate | Meaning | Main durable evidence |
| --- | --- | --- |
| `setup` | Prepare the target repository, dependencies, and task context. | setup logs and stage status |
| `solve` | Compact compatibility stage used by the `simple` profile to research, edit, and produce a patch in one lane. | stage response and final diff |
| `research` | Read-only analysis of the issue, codebase, acceptance criteria, and test plan. | research notes and stage response |
| `implement` | Make the minimal patch and write validation metadata. | git diff and validation contract |
| `verify` | Run configured machine checks. | bundled `output/verify.json` |
| `snapshot_patch` | Capture the diff before review or gates can reject it. | `output/patch.diff` |
| `audit` | Derive mechanical facts from the diff. | bundled `output/audit.json` |
| `test_evidence_gate` | Mechanical gate that checks diff facts, validation claims, and safe test execution evidence. It routes onward or to `fixup`. | live `.fabro/issue-to-pr/test-evidence-gate.json`; bundled `output/test_evidence_gate.json` |
| `review` | Legacy/structured reviewer that approves or routes to fixup. In moderated profiles this is replaced by the adversarial/moderator path. | review phase metadata |
| `adversarial_review` | Finds falsifiable reasons the patch may be unsafe or incomplete. Each finding is a review row. | live `.fabro/issue-to-pr/adversarial-review.json`; bundled `output/adversarial_review.json` |
| `adversarial_artifact_gate` | Verifies the adversarial review artifact exists and is shaped enough for moderation. | gate status and malformed artifact details |
| `moderator_filter` | Disposes each adversarial row by closing, downgrading, rejecting, or keeping it open. | live `.fabro/issue-to-pr/moderator-filter.json`; bundled `output/moderator_filter.json` |
| `materialize_review_artifacts` | Joins adversarial rows and moderator dispositions into a compact artifact for the final gate. | live `.fabro/issue-to-pr/review-materialization.json`; bundled `output/review_materialization.json` |
| `review_accountability_gate` | Final moderated export gate. It checks malformed artifacts, row/disposition accounting, closure checks, and test proof before deciding export vs fixup. | live `.fabro/issue-to-pr/review-accountability-gate.json`; bundled `output/review_accountability_gate.json` |
| `fixup` | Addresses verification, gate, or review failures and returns to verification. | updated diff and validation metadata |
| `extract_patch` | Exports the accepted final patch. | `output/patch.diff` and `output/prediction.json` |

## Review Vocabulary

| Term | Meaning |
| --- | --- |
| review row | One adversarial finding with a stable `id`, category, severity, evidence, and closure requirements. |
| row severity | Risk level assigned by adversarial review. Major rows must be accounted for before export. |
| moderator disposition | Same-id response to a review row. Valid states are `open`, `closed_by_evidence`, `downgraded`, and `rejected`. |
| open row | A row whose disposition remains `open`, or a row that is otherwise unaccounted for. Open rows block export. |
| closure check | Evidence-bound explanation/check required when a row is closed, downgraded, or rejected. Generic reassurance is not enough. |
| orphan disposition | A moderator disposition whose `id` does not match any adversarial row. Orphans are process failures. |
| duplicate row or disposition | Reused ids in adversarial rows or moderator dispositions. Duplicates are process failures because accountability becomes ambiguous. |

## Gates and Decisions

| Term | Meaning |
| --- | --- |
| artifact `status` | Raw stage or gate result in an artifact. For final accountability, `passed` means export conditions were met and `failed` means fixup is required. |
| phase `status` | Normalized `run.json` phase status, such as `completed`, `failed`, or `not_run`. A raw artifact `passed` status appears as phase `completed`. |
| run `status` | Top-level run result, currently including `completed`, `no_patch`, `verify_failed`, `failed`, `timeout`, and `error`. |
| `process_status` | Whether the review process itself was well-formed. `process_failed` means missing, malformed, duplicated, orphaned, or invalid evidence prevented reliable export. |
| `route_decision` | Final accountability routing: `export` or `fixup`. |
| `preferred_next_label` | Human-readable routing label, usually `Approve` or `Fix`. |
| `process_failures` | Machine-readable failure families such as malformed artifacts, unaccounted rows, invalid closure checks, duplicate ids, or missing test proof. |
| `readiness_tier` | Candidate readiness label. The final accountability gate currently emits `ready_verified`, `ready_unverified`, or `process_failed`. Moderator artifacts may also use `needs_fix_code`, `needs_fix_tests`, and `metadata_only_warning` as intermediate vocabulary. |

## Evidence Vocabulary

| Term | Meaning |
| --- | --- |
| validation contract | Metadata written by implementation about intended checks, touched files, and test expectations. |
| diff audit | Mechanical facts derived from the patch, such as changed files and test files changed. |
| `diff-check` | Lightweight verification mode that proves only basic patch properties such as nonempty and whitespace-clean diff. It is not a benchmark grader. |
| `tests_passed_count` | Count of safe re-executed test commands that exited 0. It is stronger than claimed or reported test success. |
| `commands_reported_passed_count` | Count of commands reported as passing by metadata. It does not by itself prove runtime test execution. |
| semantic correctness | Whether the patch actually fixes the issue. Current lightweight gates can increase confidence, but they do not prove semantic correctness. |

## Export and Candidate Vocabulary

| Term | Meaning |
| --- | --- |
| export eligible | A result may write a nonempty root SWE-bench prediction only when its status is `completed` and, if moderated review artifacts exist, `review_accountability_gate.status = "passed"` with `route_decision` absent or `export`. |
| prediction | SWE-bench-shaped record with `instance_id`, `model_name_or_path`, and `model_patch`. |
| prediction blanking | Setting `model_patch` to `""` in root SWE-bench exports when a patch is not export eligible. The patch can still be preserved in the run bundle. |
| candidate | The `run.json` record that describes whether the retained patch is ready, reusable only, or absent. |
| `candidate.state = "ready"` | Patch is export eligible and may be treated as a merge/PR candidate. |
| `candidate.state = "failed_with_patch"` | Patch exists but is not export eligible. Preserve it for continuation only; do not merge it or grade it as a successful prediction. |
| `candidate.state = "absent"` | No useful patch was produced. |
| `merge_candidate` | `candidate.reuse` for a ready patch. |
| `continuation_candidate` | `candidate.reuse` for `failed_with_patch`. |

## Lightweight Eval Vocabulary

| Term | Meaning |
| --- | --- |
| Tier 1 artifact replay | Lightweight replay that runs deterministic gates over saved artifacts to catch workflow regressions against known failure patterns. |
| Tier 2A synthetic local repo | Lightweight synthetic eval that applies a scripted patch to a tiny local git repository, regenerates diff/audit/test-evidence facts from the repository, then runs the same gate and export bundle path. It validates repo-to-artifact derivation, not sandbox execution or live model behavior. |
| Tier 2B sandboxed synthetic repo | Synthetic eval that runs the tiny repo mutation and diff/audit derivation through a real sandbox boundary, then reuses the same host-side gate and export checks. It validates sandboxed repo artifact derivation, not full issue-to-PR workflow execution. |
| sandboxed synthetic workflow | Future eval rung that runs a tiny issue-to-PR workflow through a real sandbox boundary, with scripted model/stage outputs where possible. It validates workflow and sandbox artifact plumbing without full SWE-bench or live-model variance. |
| replay fixture | A saved input/expected-output case under `fabro_kits/issue_to_pr/fixtures/tier1_artifact_replay`. |
| synthetic task | A local or sandboxed scripted eval case that starts from repository files instead of saved artifact JSON. |
| false export | Eval failure where a known bad or unaccounted patch would produce a nonempty root prediction. |
| canary fixture | Small fixture intended to fail quickly when a specific known regression returns. |
