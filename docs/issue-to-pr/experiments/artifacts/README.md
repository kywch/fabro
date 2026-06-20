# Experiment Artifacts

This directory holds small, redacted archives that are useful enough to keep with
the repository.

Large raw run bundles, provider payloads, checkpoints, and unredacted logs should
stay outside git. If an archive is tracked here, include a matching `.sha256`
file and document what was redacted in the experiment note.

Current archives:

- `2026-06-auto-research-v1-iter-workflow.tar.zst` - redacted snapshot of the
  10-round `tmp/iter-workflow` workspace used by the June 2026 auto-research
  cycle.
- `2026-06-auto-research-v2-filtered.tar.zst` - filtered V2 evidence bundle
  containing round notes, summaries, predictions/results, run metadata, workflow
  configs, sidecar JSONs, review artifacts, compact trajectories, and patches.
- `2026-06-auto-research-v3-filtered.tar.zst` - filtered V3 evidence bundle
  containing round notes, summaries, predictions/results, run metadata, workflow
  configs, stage-artifact JSONs, review artifacts, compact trajectories, and
  patches through Round 25.
