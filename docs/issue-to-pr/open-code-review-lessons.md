# OpenCodeReview Lessons for Issue-to-PR Review

This page distills what OpenCodeReview teaches us about AI code review, and
what Fabro's issue-to-PR workflow should borrow from it. It is intentionally
self-contained: the goal is not to adopt another tool wholesale, but to sharpen
the existing review lane so "review passed" really means "good enough to ship."

## Terms

**OpenCodeReview** is Alibaba's open-source AI code review CLI, usually invoked
as `ocr review`. It reads Git diffs, reviews each changed file with an LLM and
context tools, emits structured line-level comments, then post-processes those
comments with narrow filter and relocation passes.

**Issue-to-PR** is Fabro's workflow area for turning an issue or benchmark task
into a patch or PR candidate. The important current lane is
`structured-moderated`: research, implement, verify, audit, test evidence gate,
adversarial review, moderator filter, materialized review artifacts, review
accountability gate, and finally patch export.

**Adversarial review** is the issue-to-PR stage that writes review rows:
falsifiable objections to the current patch, each with an id, severity,
evidence, closure requirement, and why it matters.

**Moderator filter** is the issue-to-PR stage that writes one same-id
disposition for every adversarial row: `open`, `closed_by_evidence`,
`downgraded`, or `rejected`.

**Review accountability gate** is the final deterministic review gate. It fails
closed on malformed artifacts, duplicate or orphan ids, unaccounted rows, open
rows, weak closure checks, missing runtime proof, and known patch-shape
failures. Export requires more than a raw pass: it must produce
`route_decision = "export"`, `process_status = "passed"`,
`readiness_tier = "ready_verified"`, clean accounting fields, and machine
observed passing tests.

## What OpenCodeReview Teaches

OpenCodeReview is not one large "review this diff" prompt. It is a small review
harness with separate phases and narrow contracts:

- A prompt manifest declares phases, timeouts, token limits, and thresholds.
- Review is scoped per changed file. Other changed files can be inspected for
  context, but comments must target only the current diff.
- Large changes get a separate planning phase. The planner sees tool
  descriptions but cannot call tools.
- Main review output is a structured tool call, not free-form prose. Each
  comment includes the target code snippet and optional replacement.
- Comment anchoring is treated as a real product problem. If the original
  snippet cannot be located, a constrained relocation prompt extracts the
  minimal matching snippet from the diff.
- A conservative filter pass removes only comments that the diff itself proves
  wrong. It does not try to prove every surviving comment correct.
- The outer wrapper separates generation from adoption: run the review, classify
  comments, discard low-confidence noise, and only then decide what to fix.

The most useful lesson is structural: quality comes from review outputs that are
machine-checkable, localized, and hard to hand-wave away.

## Why This Matters

Issue-to-PR review is not just advisory. It sits on the path to exporting a
patch as a candidate result. That changes the standard.

"Reviews have teeth" means a review objection must either be resolved with
evidence or keep the run out of export. A review row is not a suggestion tucked
into a transcript; it is a blocking artifact until the moderator and gate can
account for it.

"Moderator/accountability pass means good enough to ship" means the final pass
must be a shipping signal, not a vibe check. Moderator readiness alone is not a
final pass: the moderator may close, reject, or downgrade rows, but the
accountability gate decides whether that closure is well-formed and
evidence-backed. If the gate passes with `ready_verified`, a consumer should be
able to treat the patch as an exportable merge candidate. If that is too strong
for a given run, the gate should fail or return an unready tier instead of
letting a weak pass through.

## What To Borrow Minimally

Borrow OpenCodeReview's smallest useful review harness patterns, not its whole
CLI:

- **Phase contracts:** Keep adversarial review, moderation, materialization, and
  accountability as separate stages with explicit JSON artifacts. This already
  matches issue-to-PR's strongest design.
- **Scope discipline:** Add OCR-style scope language to adversarial review:
  review only the current issue and current patch; use other files as context,
  not as new targets.
- **Planning without action:** For large or risky diffs, add an optional
  read-only planning/risk sketch before adversarial rows. It should guide row
  generation but not count as durable evidence.
- **Structured findings:** Keep every objection as a row with stable id,
  severity, category, evidence, required files, closure requirements, and a
  falsifiable check. Free-form "looks good" should never be the final artifact.
- **Conservative false-positive filter:** Make moderation explicitly
  falsification-oriented: close or reject a row only when evidence directly
  answers it. Suspicion without evidence keeps the row open.
- **Anchor evidence to artifacts:** Require closures to cite concrete diff
  hunks, changed files, `.fabro/issue-to-pr` artifacts, or machine-observed test
  proof. OCR's snippet anchoring maps to issue-to-PR's closure scoring.
- **Audience-specific output:** Keep human-readable summaries separate from the
  artifacts that gates consume. The gate should read JSON, not prose.

## What Not To Borrow

Do not import OCR's weaker assumptions where issue-to-PR already needs a higher
bar:

- **Do not treat comment filtering as approval.** OCR's filter removes obvious
  false positives; it does not establish readiness to ship.
- **Do not make line-level comments the core contract.** Issue-to-PR needs
  patch-level accountability: acceptance criteria, tests, artifacts, and export
  eligibility.
- **Do not silently discard low-confidence rows.** In issue-to-PR, a row can be
  downgraded or rejected only with a same-id disposition and closure check.
- **Do not let the reviewer fix code inside review stages.** Review and
  moderation stay read-only; fixup owns edits and must re-enter verification.
- **Do not accept claimed tests as proof.** Exportable readiness requires
  machine-observed passing test commands, not reported intent or metadata alone.
- **Do not collapse moderator and gate.** The moderator is an LLM judgment over
  rows; the accountability gate is deterministic process enforcement. Combining
  them would weaken the shipping signal.
- **Do not optimize only for fewer false positives.** False exports are worse
  than false blanks. Overblocking is an eval problem; exporting known-bad or
  unaccounted patches is a product-safety problem.

## Prioritized Recommendations

1. **Make the final pass contract explicit everywhere.** Document and enforce
   that "passed" means exportable only when `route_decision`, `process_status`,
   `readiness_tier`, row accounting, closure fields, and machine-observed tests
   all agree.

2. **Tighten moderator closure language.** Borrow OCR's "falsify, not verify"
   framing: the moderator should keep rows open unless current evidence directly
   answers the row or proves it unsupported.

3. **Require row-level evidence citations for every non-open disposition.**
   Every `closed_by_evidence`, `downgraded`, and `rejected` row should cite the
   specific file, diff fact, artifact field, or test proof that supports it.

4. **Add optional risk planning for large diffs.** Use a read-only planner to
   identify likely review rows before adversarial review. Do not let planning
   artifacts close rows or satisfy evidence requirements.

5. **Strengthen anchoring between rows and patch facts.** Treat vague rows and
   vague closures as failures. A row should name the patch area, required file,
   or falsifiable check tightly enough that fixup can act without guessing.

6. **Preserve asymmetric safety.** Keep false-export prevention as the top
   priority. Track overblocking in evals, but do not relax export gates just to
   make more patches appear successful.

## Implementation Pointers

The current issue-to-PR workflow already contains the right extension points:

- [process/staged-workflow.md](process/staged-workflow.md) describes the staged
  profiles and export safety policy.
- [glossary.md](glossary.md) defines the normative review and export terms.
- [../../fabro_kits/issue_to_pr/workflow_generator.py](../../fabro_kits/issue_to_pr/workflow_generator.py)
  generates the adversarial review, moderator, fixup, and gate stages.
- [../../fabro_kits/issue_to_pr/review_accountability_gate.py](../../fabro_kits/issue_to_pr/review_accountability_gate.py)
  enforces row accounting, closure scoring, runtime test proof, and export
  routing.
- [../../fabro_kits/issue_to_pr/artifacts.py](../../fabro_kits/issue_to_pr/artifacts.py)
  decides whether a completed run is export eligible and whether a patch becomes
  a ready candidate or a continuation candidate.

The recommended direction is therefore incremental: improve the review prompts,
row shape, moderator criteria, and eval fixtures around the existing
`structured-moderated` lane. Do not replace the deterministic accountability
gate with another LLM review.
