# Artifacts

This lane documents durable output contracts for issue-to-PR runs.
Use [../glossary.md](../glossary.md) as the normative vocabulary for candidate,
export, prediction, and review artifact terms.

Current contract anchors:

- `runs/<run_id>/` is the product-facing run layout.
- SWE-bench root JSONL files are compatibility exports, not the canonical
  product state.
- `candidate.state = "ready"` means the patch can be treated as a merge or PR
  candidate.
- `candidate.state = "failed_with_patch"` means the patch is preserved for
  continuation, but must not be merged or exported as a successful SWE-bench
  prediction.
- `fabro/dump/events.jsonl` is the canonical event source.
- `output/trajectory.jsonl` is a derived projection for easier issue-to-PR
  inspection.

Future contract pages should live here when the schema becomes stable enough to
split out, for example `runs-and-candidates.md`, `trajectory.md`, `audit.md`,
`verification.md`, and `review.md`.

See [../examples/artifacts](../examples/artifacts) for a tiny tracked run bundle
and SWE-bench compatibility export that do not depend on ignored `tmp/` state.

## Minimal Study Contract

When preserving a run for later study, keep these fields easy to find:

```json
{
  "run_id": "django__django-11099--001",
  "task_id": "django__django-11099",
  "attempt_id": "001",
  "status": "completed|no_patch|verify_failed|failed|timeout|error",
  "candidate": {
    "state": "ready|failed_with_patch|absent",
    "reuse": "merge_candidate|continuation_candidate|none",
    "patch_sha256": "...",
    "failure_class": "test_blocking",
    "failure_reason": "..."
  },
  "phases": {
    "verify": {
      "status": "completed",
      "artifact_path": "output/verify.json"
    },
    "test_evidence_gate": {
      "status": "completed|failed|not_run",
      "artifact_path": "output/test_evidence_gate.json"
    },
    "review_accountability_gate": {
      "status": "completed|failed|not_run",
      "artifact_path": "output/review_accountability_gate.json"
    }
  },
  "exports": {
    "swebench_prediction": "output/prediction.json",
    "trajectory": "output/trajectory.jsonl"
  }
}
```

For tracked docs, prefer summaries and hashes. Keep full dumps, logs, provider
payloads, and trajectories in an external artifact store or `tmp/` study
directory.

## Track vs Archive

Track in git:

- compact schemas and examples;
- `run.json`, `candidate` examples, `verify.json`, `audit.json`, and short
  redacted trajectory snippets;
- summary metrics, candidate counts, patch hashes, and decisions;
- redacted examples that are small enough to review in diffs.

Archive outside git:

- full `fabro dump` directories;
- full `events.jsonl` and `trajectory.jsonl`;
- stage prompts/responses and provider payloads;
- checkpoints and run logs;
- generated SWE-bench output trees.

Prefer `tar.zst` or `tar.gz` for external archives of run trees. Use zip only
when the consumer specifically needs a desktop-friendly archive. Do not commit
archives to this repository; commit the archive name, checksum, redaction
status, and a short summary instead.

Recommended archive layout:

```text
issue-to-pr-artifacts-<date>-<study-id>/
  README.md
  MANIFEST.json
  summaries/
    experiment-summary.md
    metrics.json
  runs/
    <run_id>/
      run.json
      task.json
      config/
      input/
      output/
      fabro/dump/
  predictions.jsonl
  results.jsonl
  summary.json
  redaction-report.md
  checksums.sha256
```

Redact or drop secrets, OAuth/API tokens, auth headers, account IDs, host paths,
remote URLs with credentials, container IDs, accidental branch names, and full
private prompts unless the archive is restricted and explicitly meant to retain
them.
