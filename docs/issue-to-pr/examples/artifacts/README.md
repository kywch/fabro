# Artifact Examples

This directory contains a tiny redacted issue-to-PR run bundle. It is not a real
benchmark result and should not be used for scoring. It exists so future agents
can inspect the tracked artifact shape without relying on gitignored `tmp/`
state.

The example demonstrates:

- `manifest.json` as the root index;
- `runs/<run_id>/` as the canonical product-facing layout;
- root `predictions.jsonl`, `results.jsonl`, and `summary.json` as
  compatibility output;
- `candidate.state = "failed_with_patch"` as a continuation-only patch;
- `trajectory.jsonl` as a short projection, not a full replay transcript.

Full production dumps, provider payloads, prompts, logs, and checkpoints should
be archived outside git with checksums and redaction notes.
