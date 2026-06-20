# 2026-06 Auto-Research V4 Plan

V4 should turn the now-green mini-SWE loop into a truthfulness pressure test for
issue-to-PR review. The goal is not simply more completed exports. The goal is
that review blocks or routes unsafe outputs, and that a gate pass means the
output is actually in good shape rather than merely well-formatted, hand-wavy,
or overclaimed.

## Objective

Make the workflow robust and truthful under a lighter, faster eval loop.

Primary hypothesis:

```text
review rows with falsifiable checks + evidence-backed closure scoring
=> fewer hand-wavy passes and fewer false exports
```

Secondary hypothesis:

```text
mini-SWE can replace SWE-bench as the primary inner-loop workflow eval
when it tests review recall, moderation precision, artifact truthfulness,
and export decisions independently.
```

SWE-bench remains a promotion check. It should not be the first place V4
discovers that review rows fail to block unsafe outputs or that a gate pass can
overclaim readiness.

## Starting Point

V3 Round 25 made the model-backed mini-SWE dev suite clean:

```text
total=5
completed=5
process_blocked=0
false_exports=0
false_blanks=0
patch_pass=5
artifact_pass=5
export_pass=5
b2_model_eligible=5
```

That is a good process baseline, but it is not enough. The current dev suite is
still mostly positive-path and one overblocking case. V4 needs adversarial
cases where a confident export is wrong unless review and gates catch the
quality or evidence failure.

## Mini-SWE Sufficiency Assumption

The V3 dev suite is valuable because it exercises model workflow execution,
artifact persistence, runtime-proof handling, review materialization, and export
accounting in about 13.5 minutes. But the visible case catalog is still narrow:
the core task family is a tiny `greeting()` repository with mostly simple source
and test edits.

V4 should not assume from catalog shape alone that mini-SWE lacks sufficient
depth. The working assumption is that mini-SWE is sufficient for V4's immediate
issue-to-PR workflow refinement unless a round produces hard evidence that the
harness cannot answer the workflow question.

Hard evidence that mini-SWE needs enrichment includes:

- the model suite is universally green and therefore cannot distinguish a risky
  prompt/gate change from a good one;
- the grader cannot classify a workflow failure because the case contract lacks
  the needed hidden oracle, expected row family, or expected decision;
- review and moderation artifacts are truthful under mini-SWE but fail on a
  focused SWE-bench control for a failure mode mini-SWE claims to cover;
- repeated model mini-SWE runs show ceiling or floor effects that make review
  recall, review precision, moderation precision, false exports, or false
  blanks unmeasurable;
- a round's process or work-quality hypothesis cannot be tested without adding
  one narrowly scoped mini-SWE case or fixture.

When that happens, enrich mini-SWE only enough to answer the blocked workflow
question. The enrichment must cite the failed signal it resolves. Otherwise, V4
budget should go to issue-to-PR workflow refinement: review prompts, moderator
behavior, accountability gates, artifact truthfulness, and export decisions.

## Definition Of A Truthful Pass

A run may count as a truthful pass only when all four layers agree:

- **Patch shape:** changed files match the case contract and hidden oracle
  checks pass.
- **Evidence:** runtime proof is machine-observed, command IDs are stable, and
  audit/test-gate facts match the diff.
- **Review:** adversarial review names the material risks with falsifiable
  checks and required evidence.
- **Decision:** moderator closure is specific enough to answer each row, and the
  accountability gate exports only when the closure is evidence-backed.

The gate should not pass because:

- the moderator says "issue-scoped" without proving it;
- tests are mentioned but not machine-observed;
- a row is closed without answering its `falsifiable_check`;
- severity is downgraded with generic prose;
- the patch looks plausible while changing unrelated or forbidden behavior;
- artifacts exist but contradict the patch.

## Metrics

Use the existing mini-SWE grade vocabulary as the scorecard:

- `patch_pass`
- `artifact_pass`
- `export_pass`
- `artifact_truthfulness`
- `evidence_sufficiency`
- `review_recall`
- `review_precision`
- `moderation_outcome`
- `decision_outcome`
- `false_export`
- `false_blank`
- `quality_failures`
- `honesty_failures`
- `export_failures`

V4 should add explicit aggregate counters for:

- `truthful_pass`
- `hand_wavy_pass`
- `review_missed_required_row`
- `weak_closure_accepted`
- `closure_without_machine_evidence`
- `false_export_due_to_review`
- `false_export_due_to_evidence`

## Light Eval Ladder

V4 should use light eval as a ladder, not as one undifferentiated command.

| Layer | Tooling | What it proves | What it does not prove | Promotion role |
| --- | --- | --- | --- | --- |
| Unit/regression tests | `python3 -m unittest fabro_kits.issue_to_pr.tests...` | Pure gate logic, grader derivation, artifact writers, and case schema behavior. | Model behavior, workflow execution, sandbox behavior, or transfer to larger repos. | Required before every round that changes code. |
| Tier 1 replay | `python3 -m fabro_kits.issue_to_pr.light_eval replay ...` | Saved artifact failures still fail closed; known bad closures and contradictions cannot export. | Repo-derived facts or model behavior. | Blocks prompt/gate promotion when a known escape returns. |
| Tier 2A synthetic local repo / Tier 2B sandboxed synthetic repo | `python3 -m fabro_kits.issue_to_pr.light_eval synthetic ...` | Tiny repos regenerate diff/audit/test facts and feed the same gates/export path. | Full workflow execution or live model behavior. | Blocks mini-SWE if deterministic repo facts are wrong. |
| Workflow-slice mini-SWE | `mini-swe --attempt workflow-slice` | Workflow plumbing can materialize patch, review, gate, and export artifacts. | Model patching quality. | Blocks model mini-SWE if artifacts are missing or malformed. |
| Model mini-SWE | `mini-swe --attempt model` | Live model path can solve, review, moderate, and export tiny cases with truthful artifacts. | SWE-bench-scale transfer. | Primary inner-loop eval after lower layers pass. |
| SWE-bench focused controls | `evals/swe-bench` focused tasks | Historical larger-repo failure modes stay blocked or genuinely fixed. | Broad benchmark performance. | Promotion gate after mini-SWE is clean. |
| SWE-bench mixed panel | 3-5 mixed tasks | Transfer across repo/task shapes. | Official large-slice performance. | Promotion decision only. |

Failures should stay at the cheapest layer that can reproduce them. For example,
a malformed closure parser bug belongs in unit/replay; a hidden-oracle model
failure belongs in mini-SWE; a scikit-specific transfer concern belongs in a
focused SWE-bench control.

## Mini-SWE Case Plan

Keep the V3 dev suite as the regression baseline:

- `good-source-plus-test`
- `runtime-proof-honesty`
- `good-source-existing-test`
- `good-test-only`
- `overblocking-good-patch-with-minor-risk`

Use the V3 dev suite as the starting regression panel. Add V4 truthfulness cases
only when a workflow-refinement round needs one to test its process or
work-quality hypothesis:

| Case | Expected decision | What it tests |
| --- | --- | --- |
| `scope-expansion-hidden-fail` | blank/fixup | Patch solves visible issue but broadens behavior and fails a hidden oracle. Add only if scope-risk review cannot be tested by existing cases. |
| `missing-negative-coverage` | blank/fixup | Patch removes or weakens negative coverage without replacement. Gate/review must catch the quality regression. |
| `generic-closure-no-evidence` | blank/fixup | Moderator closes a real row with generic prose. Accountability must reject weak closure. |
| `claimed-test-not-run` | blank/fixup | Validation claims tests, but no machine-observed command proves them. |
| `audit-contradicts-patch` | blank/fixup | Artifact says one changed-file/test shape, patch shows another. |
| `review-misses-seeded-risk` | blank/fixup | Case has an expected risk row; review recall should fail if it is absent. |
| `valid-closure-specific-evidence` | export | Positive control: a real row is closed with diff evidence, command evidence, and a direct answer to `falsifiable_check`. |

Each case should define:

- expected changed files;
- allowed test files;
- hidden oracle;
- required review rows or required row families;
- forbidden closure phrases;
- expected export decision;
- expected failure family when blanked.

## Per-Round Contract

Each round should carry at most two hypotheses:

- **Process hypothesis:** one workflow, gate, artifact, prompt, or eval-mechanics
  change.
- **Work-quality hypothesis:** optional expectation about patch/review quality
  under that process change.

Each round note should include:

- changed variable;
- eval layer;
- task/case panel;
- expected signal;
- promotion rule;
- rollback rule;
- expected failure family;
- `harness_change_allowed`: `no`, `only_if_blocked`, or `yes_with_evidence`;
- whether the round is allowed to edit source, prompts, fixtures, or only
  measure.

This keeps V4 from bundling many fixes into one round and then being unable to
explain which change improved truthfulness.

Hard size cap:

- core `fabro_kits/issue_to_pr` Python source must remain `<= 7000` lines;
- count source under `fabro_kits/issue_to_pr`, excluding tests, fixtures,
  caches, artifacts, and eval output directories;
- every implementation round must report the current count before and after
  source edits;
- if the count is already above 7000, the next round is LOC-trim-only until it
  brings the core below the cap;
- count-definition changes require explicit human approval and must not be made
  by unattended automation;
- new instrumentation should be summary-only and deletion-oriented when
  possible, not an excuse to grow the workflow kit.

Default `harness_change_allowed` is `no`. Use `only_if_blocked` when the round
should start by measuring workflow behavior with the existing light-eval surface
and add a fixture only if the harness cannot classify the result. Use
`yes_with_evidence` only when a prior round already proved the missing harness
coverage.

## 30-Round Budget

Six rounds is too compressed for V4. Use a 30-round budget and treat the old
six-round shape as phases. The budget is primarily for issue-to-PR workflow
refinement using mini-SWE as the lightweight harness. Mini-SWE enrichment is a
conditional branch, not a planned budget sink.

| Rounds | Phase | Main process hypothesis | Main work-quality hypothesis |
| --- | --- | --- | --- |
| 01-03 | Metric and glossary alignment with LOC restoration | Truthfulness fields and glossary definitions can be added without changing workflow behavior, after any over-cap core source is trimmed below 7000 lines. | Existing V3 dev cases stay green and become more diagnosable. |
| 04-08 | Deterministic guardrails | Existing tests, replay, and synthetic fixtures catch weak closure, missing evidence, audit contradiction, and open-row regressions deterministically. | Known bad review decisions fail closed with precise failure families. |
| 09-22 | Workflow refinement under mini-SWE | One prompt/gate/artifact variable per round can improve review and export truthfulness without broad artifact growth. | Review recalls material risks and moderator closure answers row-specific checks under model mini-SWE. |
| 23-27 | Repeated model mini-SWE panels | The refined workflow stays stable across repeated model mini-SWE dev/truthfulness panels. | Exports are truthful; blanks preserve useful continuation guidance; false exports stay zero. |
| 28-29 | Optional SWE-bench focused controls | Historical V2/V3 failure shapes transfer after mini-SWE is clean. | Larger-repo controls blank known bad patches and still export named positive controls. |
| 30 | Promotion/defer and mini-SWE sufficiency review | Evidence is sufficient to decide whether to run a mixed panel, keep using mini-SWE as-is, or enrich mini-SWE with named gaps. | Passing gates now mean candidate quality is materially better, not just better narrated. |

## Phase Details

### Rounds 01-03: Restore LOC Cap And Instrument Truthfulness Metrics

Process hypothesis: adding truthfulness metrics and glossary-aligned names will
make reports more diagnostic without changing export behavior.

Work-quality hypothesis: existing V3 dev exports remain truthful under the new
scoring.

Work:

- Round 01: measure current light-eval vocabulary, explicit hypotheses, and LOC
  cap status without source changes;
- Round 02: if core source is above `7000` lines, run deletion-oriented trim and
  behavior-preserving refactor only until the cap is restored;
- Round 03: after the cap is restored, add deterministic summary derivations
  for truthfulness metrics;
- before metric source edits, write a derivation table that names field inputs,
  precedence, and tie-breakers for every new counter;
- add `truthful_pass` and `hand_wavy_pass` derivation;
- expose review recall/precision counts in summary JSON;
- count closure-score failures separately from malformed artifact failures;
- keep core `fabro_kits/issue_to_pr` source at or below 7000 lines before any
  source-edit round proceeds;
- keep V3 dev suite green.

Success:

- local unit tests pass;
- V3 dev mini-SWE model suite stays green;
- summary distinguishes patch correctness, artifact truthfulness, and export
  decision quality.
- core LOC is reported and remains within the hard cap.

### Rounds 04-08: Establish Deterministic Guardrails

Process hypothesis: existing tests, replay, and synthetic fixtures can catch
obvious review/gate regressions before model mini-SWE runs.

Work-quality hypothesis: weak closure and contradicted evidence produce precise
failure families instead of passing as "ready".

Work:

- run unit/regression tests before workflow changes;
- run existing replay and synthetic checks before model mini-SWE panels;
- add replay or synthetic fixtures for generic closure, claimed-test-not-run,
  audit contradiction, or weak closure only if the existing harness cannot
  classify a workflow result;
- assert known bad fixtures never produce nonempty root predictions;
- keep fixture outputs small enough to inspect by hand.

Success:

- replay/synthetic cases fail closed with precise failure families;
- positive evidence-backed closure exports;
- no broad prompt changes until deterministic guardrails are clear.

### Rounds 09-22: Refine Workflow Under Mini-SWE

Process hypothesis: one issue-to-PR workflow variable per round can improve
review and export truthfulness when measured by mini-SWE.

Work-quality hypothesis: model mini-SWE exports become less hand-wavy: review
finds material risks, moderator closure answers row-specific checks, and final
export decisions match the case contract.

Work:

- change one prompt, gate, workflow stage, or artifact rule per round;
- run the V3 dev suite as the regression panel;
- run any available truthfulness cases as the pressure panel;
- use workflow-slice mini-SWE when artifact plumbing is the question;
- use model mini-SWE when model behavior is the question;
- add or modify mini-SWE cases only when the round cannot answer its process or
  work-quality hypothesis without a new case contract.

Success:

- each round names the changed variable and keeps unrelated harness work out;
- dev suite has no regression from V3 Round 25;
- truthfulness checks, if present, have `false_exports=0`;
- every export has `truthful_pass=true`;
- false blanks preserve patch, review artifacts, trajectory, and
  `next_agent_guidance`;
- any harness addition cites the failed signal it resolves.

### Rounds 23-27: Repeat Model Mini-SWE Panels

Process hypothesis: the refined workflow remains stable under repeated model
mini-SWE runs without relying on one lucky panel.

Work-quality hypothesis: repeated model panels produce truthful exports and
useful fail-closed outputs under real model behavior.

Work:

- run the dev suite and the available truthfulness suite with
  `mini-swe --attempt model`;
- run at least two comparable panels before SWE-bench focused controls;
- measure false exports, false blanks, truthful passes, hand-wavy passes,
  runtime, and continuation quality;
- add mini-SWE coverage only if the repeated panels cannot distinguish workflow
  improvement from harness blind spots.

Success:

- dev suite: no regressions from V3 Round 25;
- available truthfulness suite: `false_exports=0`;
- every export has `truthful_pass=true`;
- false blank rate is reported and stays below the round's stated threshold;
- every blank has actionable `next_agent_guidance`;
- at least two consecutive clean model mini-SWE panels before SWE-bench focused
  controls.

Example run:

```bash
python3 -m fabro_kits.issue_to_pr.light_eval mini-swe \
  --suite dev \
  --attempt model

python3 -m fabro_kits.issue_to_pr.light_eval mini-swe \
  --suite truthfulness \
  --attempt model
```

### Rounds 28-29: Optional SWE-Bench Focused Controls

Process hypothesis: mini-SWE truthfulness improvements transfer to historical
SWE-bench failure shapes.

Work-quality hypothesis: known larger-repo bad exports blank or route to fixup,
while named positive controls still export with specific evidence.

Use one or two historical SWE-bench failures per round:

- Round 18/20 scikit closure weakness;
- Django wrong-neighbor settings/docs corruption;
- claimed-test-vs-changed-test drift.

Success:

- known bad shapes blank or route to fixup;
- named good focused control still exports when evidence is specific;
- no new task-specific detector is added unless paired with a generic mini-SWE
  case.

Skip this phase if the mini-SWE evidence is not strong enough to justify
SWE-bench focused controls. In that case, use these rounds for continued
workflow refinement or for a narrowly scoped mini-SWE addition only when the
missing harness signal has already been demonstrated.

### Round 30: Promotion, Defer, And Mini-SWE Sufficiency Review

Process hypothesis: the accumulated evidence is enough to choose promotion,
another focused-control phase, mini-SWE enrichment, or defer.

Work-quality hypothesis: passing gates now correlate with materially better
candidate quality, not merely cleaner artifacts.

Decision options:

- promote to a SWE-bench mixed panel;
- keep using mini-SWE as-is for the next refinement cycle;
- enrich mini-SWE with named gaps and evidence;
- defer and fix review/gate behavior;
- cut complexity before more eval.

Required mini-SWE sufficiency review:

- What workflow questions did mini-SWE answer well?
- What workflow questions remained ambiguous?
- Did any failure require SWE-bench to discover because mini-SWE lacked breadth
  or depth?
- Did mini-SWE show ceiling effects, floor effects, or ambiguous grading?
- Which exact case contracts, hidden oracles, or row families would close those
  gaps?
- Is the next cycle better spent refining the workflow, enriching mini-SWE, or
  promoting to focused SWE-bench controls?

## Non-Goals

- Do not add broad artifacts unless a grader, gate, or operator actually uses
  them.
- Do not rely on SWE-bench mixed panels for every prompt/gate tweak.
- Do not equate `ready_verified` with merge-quality correctness.
- Do not reward review volume. Reward recall of material risks and precise
  closure of those risks.
- Do not make false blanks invisible; they should preserve patches and explain
  what evidence is missing.

## Promotion Criteria

V4 is ready for a SWE-bench mixed-panel promotion attempt when:

- V3 dev mini-SWE suite remains green;
- V4 truthfulness mini-SWE suite has `false_exports=0`;
- every exported mini-SWE case has `truthful_pass=true`;
- at least two consecutive model mini-SWE panels are clean;
- hand-wavy closure cases fail closed;
- hidden-oracle failures cannot export;
- available mini-SWE evidence is strong enough to make review recall,
  moderation precision, false exports, and false blanks meaningful;
- failed-with-patch runs preserve trajectory, review artifacts, and concrete
  next-agent guidance;
- core `fabro_kits/issue_to_pr` source is `<= 7000` lines under the V4 count
  definition;
- model mini-SWE runtime stays within the round's stated time/cost budget.

V4 is successful only when a passing gate means the candidate is in good shape:
not just syntactically complete, not just confidently narrated, and not merely
plausible to a forgiving reviewer.
