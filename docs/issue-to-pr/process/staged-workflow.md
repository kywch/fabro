# Staged Workflow

The structured issue-to-PR workflow is intentionally split into stages so each
attempt leaves inspectable evidence.

```text
setup -> research -> implement -> verify -> snapshot_patch -> audit -> review
review -> extract_patch  # Approve
review -> fixup -> verify  # Fix
```

## Stage Contract

| Stage | Responsibility | Durable evidence |
| --- | --- | --- |
| `setup` | Prepare the target repository and dependencies. | stage status, setup logs |
| `research` | Read the issue and codebase without mutating files; identify acceptance criteria and test plan. | research notes, stage response |
| `implement` | Make the minimal patch and write validation metadata. | git diff, validation contract |
| `verify` | Run machine checks configured by the workflow. | `verify.json` |
| `snapshot_patch` | Capture the current diff before review can reject it. | patch text |
| `audit` | Derive machine facts from the diff. | `audit.json` |
| `review` | Decide whether to approve or route to fixup. | review routing JSON and phase metadata |
| `fixup` | Address review or verification failures. | updated diff and validation metadata |
| `extract_patch` | Export the approved final patch. | `patch.diff` |

The current `diff-check` verifier is intentionally light. It is not a benchmark
grader and does not prove correctness. Review and future deterministic gates
must fill that gap.

## Candidate Outcomes

- `ready`: patch passed the workflow gates and may be used as a merge/PR
  candidate.
- `failed_with_patch`: patch is preserved for continuation, but must not be
  merged or exported as successful SWE-bench output.
- `absent`: no useful patch was produced.

Future workflow changes should preserve this distinction. It is better to end
with a useful failed patch plus lesson than to lose the patch or mark it
successful by accident.
