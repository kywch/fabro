# 2026-06 Auto-Research V2

This cycle continued from
[2026-06 Auto-Research V1](2026-06-auto-research-v1.md). It started as a
controlled follow-up to add deterministic test-evidence gating, then expanded
into a longer exploratory cycle around export safety, review accountability,
runtime-proof honesty, and harness complexity.

## Artifact Snapshot

Raw scratch artifacts currently live outside git at:

- `tmp/iter-workflow-v2`

The local workspace is unredacted and should not be committed as-is. It contains
round plans, evaluations, live logs, preflight logs, scorecard material, output
bundles, predictions, results, manifests, and side investigations.

Artifact measurements:

- raw workspace: approximately `4.2G`
- compressed archive: `162M` as `/tmp/iter-workflow-v2.tar.zst`
- compressed SHA-256:
  `d7e56c3e9e83cfae47c2ea3a8f8cd1c836afd2411d279b40cbc269539f19209f`
- filtered archive:
  `docs/issue-to-pr/experiments/artifacts/2026-06-auto-research-v2-filtered.tar.zst`
- filtered archive size: `13M`
- filtered file count: `5,229`
- filtered SHA-256:
  `6d4b83dcf894bdc142a08dc6497353192f86e625f375c70a973308c7ae4fa1af`

The full compressed archive above is a local measurement artifact, not a tracked
bundle.

The filtered archive keeps round Markdown, summary/manifest/prediction/result
files, run/task metadata, input goals, workflow configs, sidecar JSON outputs,
stage-artifact JSONs when present, review artifacts, compact per-run
`output/trajectory.jsonl`, and patch diffs. It drops full Fabro dumps,
`run_dump/`, checkpoints, raw dump trajectory copies, events, and raw logs.

## Objective

Improve issue-to-PR workflow correctness under controlled measurement, not just
artifact completeness.

The initial V2 hypothesis was:

```text
audit -> test_evidence_gate -> review
```

The new deterministic gate should compare actual diff files, changed test files,
observed command evidence, validation claims, and repeated review failure
classes before semantic review gets a vote.

## Headline Outcome

V2 made export safety and continuation artifacts much more honest, but it also
proved that deterministic gates can only protect facts the workflow has made
machine-visible.

The useful V2 lesson is not "add more artifacts." It is:

```text
machine gates enforce export eligibility;
review judges semantic risk;
unsafe patches are blanked at the benchmark surface but preserved for continuation.
```

The cycle repeatedly found that `ready_verified`, benchmark export, official
grading status, and merge-quality correctness are separate conclusions. Treating
them as one conclusion produced misleading success.

## What Changed

Evidence and export safety:

- added `test_evidence_gate` to compare validation claims with changed tests and
  observed command evidence;
- made failed or under-reviewed patches blank in root `predictions.jsonl`;
- preserved failed patches as `failed_with_patch` continuation candidates;
- separated patch preservation from benchmark export eligibility;
- made runtime proof stricter by distinguishing model-reported test success from
  machine-observed passing command evidence.

Review and accountability:

- introduced adversarial review and moderator/accountability artifacts;
- made missing or malformed review artifacts fail closed;
- tightened review row accounting so unresolved serious objections block export;
- exposed moderator over-closure as the main semantic leak.

Harness simplification:

- identified duplicated authority between workflow gates and export-time
  synthesis;
- rejected broad artifact growth that made the harness harder to audit;
- pushed toward a smaller model where workflow gates decide and export code
  copies, indexes, and blanks unsafe predictions.

## Phase Ledger

V2 is too large for a useful public per-round ledger. The durable read is by
phase, with representative rounds as anchors.

| Phase | Rounds | Main question | Durable lesson |
| --- | --- | --- | --- |
| Evidence gate shakedown | 1-8 | Can claimed tests be checked against actual diffs and run evidence? | Path normalization and artifact routing matter as much as the gate logic. A false block is safer than a false export, but false blocks must become diagnosable. |
| Review accountability | 9-30 | Can adversarial review and moderator artifacts constrain bad patches? | Review artifacts need row-level accounting. Flat "approve" decisions hide uncertainty and make later export logic guess. |
| Runtime-proof honesty | 31-70 | Can `ready_verified` require machine-observed passing tests? | Self-reported command success should not grant verified readiness. Missing runtime proof should preserve the patch but blank the prediction. |
| False block and transfer pressure | 71-99 | Can stricter gates still allow good focused controls and mixed panels? | Focused controls can pass while mixed panels still find bad exports. Relaxing a noisy gate often reveals the next semantic leak. |
| Late panel hardening | 100-119 | Does the fail-closed shape survive broader panels and GitHub/API-adjacent gates? | The system became safer at blanking unsafe outputs, but the harness was too large and SWE-bench remained too expensive as the daily workbench. |

## Representative Findings

Round 08 showed that first-class gate artifacts made contradictions visible, but
review could still approve patches with weak or failed runtime evidence.

Round 60 shifted the workflow away from trusting self-reported command passes as
`ready_verified`. That separation became one of the main V2 principles.

Round 77 and Round 80 explored the tradeoff between false blocks and false
exports. The result was not to weaken safety broadly, but to make the failure
reason more precise and keep continuation patches useful.

Round 90 through Round 99 repeatedly tested Django transfer cases. They showed
that preserved failed patches can be valuable while completed exports can still
contain obvious quality failures when moderator closure is weak.

Round 116-style late panel work showed better fail-closed behavior on open rows,
but also confirmed that process leaks such as user-input attempts and missing
artifacts must be treated as workflow failures, not model-quality findings.

## Durable Lessons

- Deterministic gates are strongest when they enforce observable facts:
  changed files, changed tests, command IDs, exit status, artifact presence, row
  accounting, and prediction blanking.
- Deterministic gates cannot prove semantic merge quality by themselves.
- A failed run can be high-quality process output if it preserves the patch,
  names the missing evidence, blanks the benchmark prediction, and gives clear
  next-agent guidance.
- Review should not repair patches. Review should name risks; fixup must revisit
  the whole issue contract.
- Export-time normalization must not become a second workflow authority.
- Official grading and manual merge-quality inspection are different signals.
- SWE-bench is valuable as a promotion gate, but too expensive and ambiguous as
  the default inner loop for every gate or prompt tweak.
- Harness LOC and artifact count are real product constraints. Extra sidecars
  are only worth keeping when an operator or deterministic gate consumes them.

## What Not To Promote

Do not promote the entire V2 harness shape. It carried too much duplicated
review synthesis, export-time mutation, and artifact surface.

Do not treat V2 completion/export counts as solve-quality scores. Many V2 notes
explicitly separate process success from true correctness.

Do not make task-specific scars the promotion standard. Django and scikit
detectors were useful canaries, but they need generic paired cases before they
become durable workflow policy.

## Decision

Decision: `promote lessons`

Promote:

- deterministic test-evidence gating;
- fail-closed review artifact handling;
- row-level review accountability;
- root prediction blanking for unsafe or under-reviewed runs;
- failed-with-patch continuation preservation;
- runtime-proof honesty;
- the principle that SWE-bench mixed panels are promotion checks, not the daily
  refinement loop.

Reject:

- the full V2 artifact surface;
- export-time review synthesis as a second authority;
- broad artifact growth without a deterministic consumer;
- using exported predictions as a proxy for merge quality.

Follow-up:

- carry the useful V2 invariants into V3;
- replace daily SWE-bench iteration with replay, synthetic repos, and mini-SWE
  before returning to SWE-bench mixed panels.
