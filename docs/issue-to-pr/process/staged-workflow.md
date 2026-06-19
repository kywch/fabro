# Staged Workflow

The issue-to-PR workflow is split into profiles so experiments can add gates
without breaking the compact compatibility path. Shared terms in this page are
defined in [../glossary.md](../glossary.md).

## Profiles

```text
simple:
  setup -> solve -> extract_patch

structured:
  setup -> research -> implement -> verify
  verify -> snapshot_patch -> audit -> review  # pass
  verify -> fixup -> verify  # fail/fallback
  review -> extract_patch  # Approve
  review -> fixup -> verify  # Fix/fallback

structured-gated:
  setup -> research -> implement -> verify
  verify -> snapshot_patch -> audit  # pass
  verify -> fixup -> verify  # fail/fallback
  audit -> test_evidence_gate
  test_evidence_gate -> review  # pass
  test_evidence_gate -> fixup -> verify  # fail/fallback
  review -> extract_patch  # Approve
  review -> fixup -> verify  # Fix/fallback

structured-moderated:
  setup -> research -> implement -> verify
  verify -> snapshot_patch -> audit  # pass
  verify -> fixup -> verify  # fail/fallback
  audit -> test_evidence_gate
  test_evidence_gate -> adversarial_review  # pass
  test_evidence_gate -> fixup -> verify  # fail/fallback
  adversarial_review -> adversarial_artifact_gate
  adversarial_artifact_gate -> moderator_filter  # pass
  adversarial_artifact_gate -> fixup -> verify  # fail/fallback
  moderator_filter -> materialize_review_artifacts
  materialize_review_artifacts -> review_accountability_gate
  review_accountability_gate -> extract_patch  # export
  review_accountability_gate -> fixup -> verify  # fixup/fallback
```

The `structured-moderated` profile is the accountability lane for catching
workflow regressions that match known review failure patterns. It does not prove
semantic correctness; it prevents known-bad or unaccounted review states from
becoming successful SWE-bench predictions.

## Stage Contract

| Stage | Responsibility | Durable evidence |
| --- | --- | --- |
| `setup` | Prepare the target repository and dependencies. | stage status, setup logs |
| `solve` | Compatibility stage for the `simple` profile. | stage response and final diff |
| `research` | Read the issue and codebase without mutating files; identify acceptance criteria and test plan. | research notes, stage response |
| `implement` | Make the minimal patch and write validation metadata. | git diff, validation contract |
| `verify` | Run machine checks configured by the workflow. | bundled `output/verify.json` |
| `snapshot_patch` | Capture the current diff before review or gates can reject it. | `output/patch.diff` |
| `audit` | Derive machine facts from the diff. | bundled `output/audit.json` |
| `test_evidence_gate` | Check diff facts, validation claims, and safe test execution evidence. | live `.fabro/issue-to-pr/test-evidence-gate.json`; bundled `output/test_evidence_gate.json` |
| `review` | Approve or route to fixup in the structured and structured-gated profiles. | review phase metadata |
| `adversarial_review` | Produce falsifiable risk rows for the current patch. | live `.fabro/issue-to-pr/adversarial-review.json`; bundled `output/adversarial_review.json` |
| `adversarial_artifact_gate` | Ensure adversarial review output is present and parseable before moderation. | gate status and malformed artifact details |
| `moderator_filter` | Account for adversarial rows with same-id dispositions. | live `.fabro/issue-to-pr/moderator-filter.json`; bundled `output/moderator_filter.json` |
| `materialize_review_artifacts` | Join adversarial rows and moderator dispositions into review materialization. | live `.fabro/issue-to-pr/review-materialization.json`; bundled `output/review_materialization.json` |
| `review_accountability_gate` | Fail closed on malformed review artifacts, open rows, missing closure checks, missing test proof, or invalid accounting. | live `.fabro/issue-to-pr/review-accountability-gate.json`; bundled `output/review_accountability_gate.json` |
| `fixup` | Address review, verification, or gate failures. | updated diff and validation metadata |
| `extract_patch` | Export the accepted final patch. | `output/patch.diff`, `output/prediction.json` |

`diff-check` is intentionally light. It proves only basic patch properties, not
benchmark correctness. `test_evidence_gate` and `review_accountability_gate`
raise the floor for process evidence, but official benchmark claims still need
grading or an explicit skip reason.

## Routing and Visit Budgets

Structured profiles route failing gates and reviewer fixes back through
`fixup -> verify`, so new edits always re-enter the evidence path before export.
The generator caps fixup and gate/review visits separately to avoid infinite
review loops while preserving enough room for repeated review/fix cycles.

## Candidate Outcomes

Candidate outcome terms are defined normatively in
[../glossary.md](../glossary.md#export-and-candidate-vocabulary).

- `ready`: patch is export eligible and may be used as a merge/PR candidate.
- `failed_with_patch`: patch is preserved for continuation, but must not be
  merged or exported as a successful SWE-bench prediction.
- `absent`: no useful patch was produced.

Future workflow changes should preserve this distinction. It is better to end
with a useful failed patch plus lesson than to lose the patch or mark it
successful by accident.
