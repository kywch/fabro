# 2026-06 Auto-Research V3

This cycle disciplined the V2 lessons into a more repeatable issue-to-PR
refinement loop. It preserved the useful fail-closed behavior while adding
resource preflight, stricter artifact materialization, and a lightweight eval
ladder so SWE-bench mixed panels could become promotion checks rather than the
daily workbench.

## Artifact Snapshot

Raw scratch artifacts currently live outside git at:

- `tmp/iter-workflow-v3`

The local workspace is unredacted and should not be committed as-is. It contains
round plans, evaluations, preflight notes, live logs, output summaries,
predictions, results, manifests, and mini-SWE/replay/synthetic evaluation
material.

Artifact measurements:

- raw workspace: approximately `952M`
- compressed archive: `24M` as `/tmp/iter-workflow-v3.tar.zst`
- compressed SHA-256:
  `41bc9c3e9f4c6c465997926b71fd58b07a58452135251d9eacb2963469f6c043`
- filtered archive:
  `docs/issue-to-pr/experiments/artifacts/2026-06-auto-research-v3-filtered.tar.zst`
- filtered archive size: `2.3M`
- filtered file count: `1,672`
- filtered SHA-256:
  `4538fb5b2e3d5bd457132d48d37e114d1b4c634dc264ea7d9658477aa140b9cc`

The full compressed archive above is a local measurement artifact, not a tracked
bundle.

The filtered archive keeps round Markdown, summary/manifest/prediction/result
files, run/task metadata, input goals, workflow configs, sidecar JSON outputs,
stage-artifact JSONs, review artifacts, compact per-run
`output/trajectory.jsonl`, and patch diffs. It drops full Fabro dumps,
`run_dump/`, checkpoints, raw dump trajectory copies, events, and raw logs.

## Objective

Make the issue-to-PR workflow safer, cheaper to iterate, and easier to audit
without losing the V2 invariants:

- blank unsafe root predictions;
- preserve failed patches as continuation material;
- require deterministic gate evidence for export eligibility;
- require machine-observed runtime proof for `ready_verified`;
- block unresolved review rows;
- treat `request_user_input` as a process failure;
- keep harness complexity under active control.

## Headline Outcome

V3 improved materially as a fail-closed workflow harness, but it did not yet
become a trustworthy exporter.

The strongest improvement was process safety: preflight discipline, Docker/disk
cleanup, prediction blanking, continuation patch preservation, trajectory
exports, test-evidence checks, and review artifact materialization all became
more reliable.

The weakest remaining area was export-time work quality. The recurring failure
mode moved from "open rows are ignored" to "rows are closed too easily."

## What Changed

Operational discipline:

- added standard disk/Docker preflight before live evaluation;
- treated stale containers, output collisions, and provider/config failures as
  process findings;
- kept predictions blank for failed, blocked, verify-failed, or under-reviewed
  attempts;
- preserved per-run bundles and trajectories for exported and
  failed-with-patch runs.

Review and gate discipline:

- made review artifact materialization stricter;
- made malformed or missing review artifacts fail closed;
- added open-row export blocking, including minor rows once that failure became
  visible;
- required nonempty closure checks for resolved rows after weak moderator
  closure caused bad exports;
- kept Django settings/docs corruption and scikit scope-expansion cases as
  canaries for broader review quality.

Evaluation discipline:

- documented a tiered ladder from deterministic replay to SWE-bench promotion;
- implemented replay, synthetic repo checks, scripted mini-SWE, workflow-slice
  mini-SWE, and model mini-SWE;
- shifted the inner loop toward cheap model mini-SWE after lower tiers passed.

## Round Ledger

| Rounds | Focus | Result |
| --- | --- | --- |
| 01-07 | Establish preflight, output, artifact materialization, and fail-closed handoff discipline. | Process safety improved, but review handoff drift and bad exports still appeared. |
| 08-10 | Duplicate-test detection and review/materialization failure handling. | Some gates overblocked, but missing/malformed artifacts increasingly failed closed. |
| 11-13 | Adversarial artifact gating and failure taxonomy. | Diagnostic precision improved. |
| 14-16 | Django settings/docs corruption escape and detector validation. | Round 14 exported a bad docs-corruption patch; later rounds caught that class and Round 16 was safe with zero exports. |
| 17-20 | Scikit controls and mixed-panel transfer. | Focused controls improved, but mixed panels still found bad scikit exports through open rows or weak closure. |
| 21 | Lightweight eval ladder. | Replay, synthetic, scripted mini-SWE, and workflow-slice mini-SWE ran cleanly enough to become the new refinement path. |
| 22-24 | Model mini-SWE promotion probes. | Provider/plumbing blocks were surfaced, then Round 24 produced 5 model attempts with 4 B2-eligible exports, 0 false exports, and 1 process block. |
| 25 | Review-artifact persistence refinement. | The targeted overblocking canary and full 5-case model mini-SWE dev suite completed with 0 process blocks, 0 false exports, 0 false blanks, and 5 B2-model-eligible exports. |

## Current State After Round 25

Round 24 made mini-SWE useful as an inner-loop workflow refinement driver.
Round 25 made the full model-backed dev suite clean.

Round 25 fixed the remaining model mini-SWE blocker from Round 24:
`overblocking-good-patch-with-minor-risk` moved from
`model_workflow_failed` / missing or malformed review artifacts to a clean
B2-eligible export.

Round 25 full mini-SWE dev model suite:

```text
total=5
completed=5
failed=0
process_blocked=0
b2_model_eligible=5
patch_pass=5
artifact_pass=5
export_pass=5
false_exports=0
false_blanks=0
provider_not_configured=0
total_duration_s=807.2
```

Targeted canary:

```text
overblocking-good-patch-with-minor-risk
completed=1
process_blocked=0
patch_pass=1
artifact_pass=1
export_pass=1
b2_model_eligible=1
false_exports=0
false_blanks=0
total_duration_s=142.5
```

This is the first clean model mini-SWE dev panel. It is still inner-loop
evidence, not SWE-bench promotion evidence, but it gives V3 a cheap regression
check before returning to SWE-bench controls or mixed panels.

## Respectable-Success Status

Not met.

The first serious V3 success bar required repeated mixed-panel rounds with zero
known bad exports and gate-backed exports. The late SWE-bench sequence did not
meet that bar:

- Round 16 was safe but produced zero exports.
- Round 18 exported a bad scikit patch with an unresolved minor row.
- Round 20 safely blanked some unsafe work, but still exported a bad scikit
  patch after weak moderator closure.

Round 25 cleaned the cheaper mini-SWE path, but that is inner-loop evidence,
not yet SWE-bench promotion evidence.

## Durable Lessons

- V2's safety invariants held up: unsafe predictions should blank, failed
  patches should survive, and export eligibility should be explained by gates.
- Open review rows must block export, including minor rows.
- Empty, generic, or formulaic `closure_check` values are not enough.
- Severity labels are not reliable enough to drive safety alone.
- Focused controls are useful but do not prove mixed-panel robustness.
- Task-specific detectors are valuable canaries, but promotion needs generic
  paired fixtures and broader transfer checks.
- SWE-bench remains valuable, but only after cheaper replay, synthetic, and
  mini-SWE tiers are clean.

## Eval Ladder

The V3 ladder is:

1. Tier 1 artifact replay.
2. Tier 2 synthetic repo checks.
3. Tier 2.5 scripted and workflow-slice mini-SWE calibration.
4. Tier 3 model mini-SWE attempts.
5. Tier 4 SWE-bench focused controls.
6. Tier 5 SWE-bench mixed panels.
7. Tier 6 larger SWE-bench or official grading.

The purpose is to catch known bad exports before spending SWE-bench cycles.
SWE-bench should test transfer and promotion, not rediscover basic gate,
artifact, and blanking regressions.

## Decision

Decision: `defer promotion`

Promote:

- V3 preflight/resource discipline;
- stricter artifact materialization;
- failed-with-patch preservation and prediction blanking;
- open-row export blocking;
- the lightweight eval ladder;
- model mini-SWE as the active inner-loop regression check.

Defer:

- SWE-bench mixed-panel promotion;
- any claim that `ready_verified` means merge-quality clean;
- broad new artifacts until known closure-quality failures are covered by replay
  and synthetic fixtures.

Follow-up:

- reduce `light_eval/mini_swe.py` bloat while preserving the now-green model
  suite as a regression check;
- add or strengthen replay/synthetic fixtures for open rows, empty/generic
  closure checks, wrong-neighbor edits, scope expansion, removed negative
  coverage, and claimed-test mismatch;
- use model mini-SWE before every SWE-bench promotion attempt;
- run SWE-bench focused controls and mixed panels as promotion checks only after
  the cheaper ladder remains clean.
