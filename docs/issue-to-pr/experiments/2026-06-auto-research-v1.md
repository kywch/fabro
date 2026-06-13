# 2026-06 Auto-Research V1

This cycle refined the SWE-bench issue-to-PR workflow over 10 rounds. The raw
scratch artifacts lived under a gitignored workspace during the experiment; this
note is the tracked summary for future workflow work.

## Artifact Snapshot

A redacted snapshot of the 10-round workspace is tracked at:

- `docs/issue-to-pr/experiments/artifacts/2026-06-auto-research-v1-iter-workflow.tar.zst`
- `docs/issue-to-pr/experiments/artifacts/2026-06-auto-research-v1-iter-workflow.tar.zst.sha256`

Checksum:

```text
8c67a02169bb2f672ca980f00b30e2cda2c1e2fe45556c8d802e104b4e5e90c7  docs/issue-to-pr/experiments/artifacts/2026-06-auto-research-v1-iter-workflow.tar.zst
```

The source workspace was `tmp/iter-workflow` before redaction. Local host paths
were replaced with placeholders such as `<repo>` and `<home>`. The archive root
is `iter-workflow/` and contains one directory per round. The compact top-level
material includes each round's `hypothesis.md`, `process_review.md`,
`synthesis.md`, `workflow_generator.py`, and `output/summary.json`. The larger
run material includes per-task goals, generated `workflow.toml`/`workflow.fabro`
files, `attempt.json`, `prediction.json`, `patch.diff`, `verify.json`,
`audit.json` when available, `trajectory.jsonl`, and `run_dump/` stage logs,
prompts, responses, checkpoints, and `events.jsonl`.

Use the archive for historical analysis only. The tracked summary below is the
canonical quick read; the archive is the evidence bundle for rechecking claims,
replaying artifact parsers, or mining trajectories.

## Objective

Build toward a VPS-hosted issue-to-PR workflow engine whose artifacts make it
possible to keep improving the workflow over time.

## Headline Outcome

The cycle turned the runner from a simple Docker/Codex SWE-bench patch generator
into a staged attempt instrument:

```text
research -> implement -> verify -> snapshot -> audit -> review -> fixup -> candidate
```

The key product insight is that Fabro's value is not just running Codex in
Docker. A lighter harness can do that. Fabro earns its weight when an attempt is
durable, inspectable, resumable, and able to preserve both merge candidates and
failed-but-useful continuation candidates.

## What Changed

Operational hardening:

- batch runs became non-interactive;
- goal text was made template-safe for issue bodies containing Django/MiniJinja
  examples;
- local Docker runs were kept sequential for reliable smoke testing;
- generated workflows were preflighted before launch.

Structured workflow:

- added separate `research`, `implement`, `verify`, `snapshot_patch`, `audit`,
  `review`, and `fixup` stages;
- increased review/fix loop visit budgets after early loops exhausted too soon;
- added a machine `audit.json` before review;
- taught review to trust machine audit over stale agent-authored validation
  claims.

Artifact contract:

- promoted run bundles under `runs/<run_id>/`;
- exported `verify.json`, `audit.json`, `trajectory.jsonl`, and candidate
  metadata;
- preserved rejected patches as `failed_with_patch` continuation candidates;
- kept review-rejected patches out of successful SWE-bench `predictions.jsonl`;
- added summary counters for `failed_with_patch` and
  `continuation_candidates`.

## Durable Lessons

- `diff-check` is artifact hygiene, not semantic verification.
- Completed/exported is not the same as correct.
- A failed patch can be a good finish if it is clearly unsafe to merge and
  carries `failure_reason`, `do_not_repeat`, and `next_agent_guidance`.
- Review/fix loops have teeth, but too much still depends on LLM judgment.
- Machine evidence should be first-class: changed files, test files, patch hash,
  commands run, exit codes, and reviewer decisions should be queryable without
  reading full trajectories.
- Raw trajectories are useful for diagnosis, but `events.jsonl` remains the
  canonical source.
- Auto-research rounds should track separate scorecards for solve quality,
  process quality, and artifact quality.

## Per-Round Ledger

Each round used five fresh Django SWE-bench tasks, `gpt-5.4-mini`, the structured
workflow profile, and `diff-check` as the mechanical verifier. After round 1,
task execution was intentionally sequential with `--max-workers 1`.

| Round | Result | Change under test | Main lesson |
| --- | --- | --- | --- |
| 1 | 4 completed, 1 failed | Prompt-only research discipline: no repository writes, explicit acceptance criteria, validation plan, and shortcut risks. | Research behavior improved, but one task blocked on `request_user_input`; batch mode needed a hard non-interactive contract. |
| 2 | 4 completed, 1 failed | Non-interactive contract for research, implement, and fixup; agents must make defensible assumptions instead of asking questions. | Interactive failures stopped for tasks that reached the agent; a Django template example in the issue body exposed unsafe goal templating. |
| 3 | 5 completed | Template delimiter neutralization for goal text containing `{%`, `{{`, or `{#` examples. | Pre-agent template failures disappeared; `diff-check` still let semantically weak patches through. |
| 4 | 2 completed, 3 failed | Added read-only semantic `review` after mechanical verify, routing `Approve` to export and `Fix` to repair. | Review caught real defects but could over-reject; `review -> fixup` hit visit limits and rejected patches were lost as empty exports. |
| 5 | 4 completed, 1 failed | Increased visit budgets enough for review to enter `fixup`; tightened research/review read-only language. | `Fix` reached `fixup`, but the next `verify` still hit a stale visit cap; generated workflow preflight was needed. |
| 6 | 2 completed, 3 failed | Added generated-workflow preflight and `max_visits=3` for verify/review/fixup. | Stale workflow generation was fixed and repair loops ran; two-fixup paths still needed path-based budgets and failed-loop patch preservation. |
| 7 | 3 completed, 2 failed | Path-based budgets plus `/tmp/fabro-validation.json` as a durable agent-authored evidence contract. | Loop mechanics worked and reviews became more specific, but agent-authored validation could drift from the actual diff and cause false negatives. |
| 8 | 4 completed, 1 failed | Added `snapshot_patch` and machine `audit` before review; failed review paths fall back to snapshot patches. | Rejected patches were preserved, but the audit script used Python features unavailable in Python 3.6 SWE-bench images. |
| 9 | 4 completed, 1 failed | Made audit Python 3.6-compatible and exported `audit.json`; normalized review phase status/failure class. | Machine audit became a real artifact. Failed-with-patch runs had enough raw evidence for continuation, but not yet structured lessons. |
| 10 | 4 completed, 1 failed, 1 continuation candidate | Added first-class `candidate` records: `ready` for merge candidates, `failed_with_patch` for continuation candidates, with review lessons. | Output consumers can now distinguish mergeable patches from preserved partial work without reading full trajectories. |

Aggregate mechanical shape across 50 task attempts: 36 completed, 14 failed, no
timeouts, no empty-patch mechanical failures, and one final
`failed_with_patch` continuation candidate after that state became first-class.
These are process/artifact counts, not SWE-bench correctness scores.

## Next Cycle

The next cycle should shift from "can we export better artifacts?" to "does the
workflow improve PR correctness under controlled measurement?"

Recommended next-loop shape:

- run the current workflow as a baseline;
- change one variable per round;
- keep a fixed smoke panel plus a fresh holdout panel;
- include non-Django tasks to reduce SWE-bench/Django overfitting;
- run grader checks when possible;
- keep correctness, process, and artifact judges separate;
- add deterministic gates for test evidence before relying on review.

High-priority workflow improvement:

```text
audit -> test_evidence_gate -> review
```

The gate should reject or route to fixup when validation claims tests that do
not appear in the diff, when test commands have no observed evidence, or when a
test-blocking review repeats without progress.

Suggested prompt for the next 10-round auto-research agent:

```text
You are running the next 10-round auto-research cycle for Fabro's
issue-to-PR/SWE-bench workflow.

Baseline:
- Start from the workflow summarized in
  docs/issue-to-pr/experiments/2026-06-auto-research-v1.md.
- Treat the archived tarball and checksum in that document as historical
  evidence, not as mutable working state.
- Keep the current staged shape unless a round explicitly tests one change:
  research -> implement -> verify -> snapshot_patch -> audit -> review -> fixup
  -> candidate.

Goal:
- Measure and improve PR correctness under controlled conditions, not just
  artifact completeness.
- Run 10 rounds. Change one primary variable per round. Keep each round small
  enough that a later agent can understand why it happened.

Measurement contract:
- Use a fixed smoke panel for regression plus a fresh holdout panel each round.
- Include some non-Django tasks once the harness supports them, so improvements
  do not overfit Django issue patterns.
- Track three scorecards separately: solve quality, process quality, and
  artifact quality.
- Distinguish process counts from true correctness. Completed/exported means
  only that the workflow produced a candidate.
- Run grader checks when available. When graders are unavailable, record the
  exact proxy evidence and its limits.

High-priority first experiment:
- Add a deterministic test_evidence_gate between audit and review.
- The gate should compare actual diff/test files, observed command outputs, and
  validation claims.
- Route to fixup or fail when a run claims tests that are absent from the diff,
  when test commands have no observed evidence, or when a repeated
  test_blocking review has no new evidence.
- Keep review focused on semantic judgment after machine evidence has been
  normalized.

Per-round procedure:
1. Write round_N/hypothesis.md with the single variable, tasks, expected signal,
   and rollback criteria.
2. Generate/preflight workflow artifacts before launch.
3. Run tasks sequentially unless the round is explicitly testing concurrency.
4. Preserve runs/<run_id>/ bundles, verify.json, audit.json, candidate metadata,
   trajectories, events.jsonl, and failed_with_patch continuation candidates.
5. Write process_review.md and synthesis.md before choosing the next change.
6. Update a compact ledger with result counts, what changed, and the lesson.

Hard rules:
- Do not use interactive user input inside benchmark attempts.
- Do not let research or review mutate repositories.
- Do not put review-rejected patches into successful SWE-bench predictions.
- Do not let agent-authored validation override machine audit evidence.
- Do not accept snapshots or generated summaries without checking for unrelated
  pending changes.
```
