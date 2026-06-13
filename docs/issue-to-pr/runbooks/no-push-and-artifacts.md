# No-push and Artifact Lessons

The Docker/Codex smoke produced a few lessons that future agents should keep in
mind before running live workflows.

## Avoid Accidental Remote Refs

A raw tutorial smoke inherited default run/meta branch behavior and pushed
generated `fabro/run/*` and `fabro/meta/*` refs to the repository remote. For
evals and local smoke tests, use wrapper TOML that disables side effects:

```toml
_version = 1

[workflow]
graph = "../../../internal/demo/01-hello.fabro"

[run.pull_request]
enabled = false

[run.run_branch]
enabled = false

[run.meta_branch]
enabled = false

[run.environment]
id = "docker"
```

For SWE-bench generation, also disable Fabro clone behavior because the workflow
setup stage is responsible for cloning the benchmark repository:

```toml
[run.clone]
enabled = false
```

If debug refs are desired, push only to a scratch fork or explicitly configured
remote, never the product repo by accident.

## Inspect Dumps, Not Just Top-level Status

`fabro dump` is the postmortem source. A useful inspection checklist:

- `run.json` for selected environment, model, workflow, stage statuses, and
  run settings;
- `run.log` for sandbox setup, provider activity, branch pushes, and warnings;
- `events.jsonl` for canonical chronology;
- `stages/*/status.json` or `nodes/*/status.json` for per-stage outcomes;
- stage `response.md` files for review routing JSON and agent answers;
- provider files when model/API issues are suspected;
- `parallel_results` when a graph has fan-out/fan-in behavior.

Top-level success can hide route-level failures if a later node selects a
successful branch. Always inspect stage-level artifacts before using a run as a
quality example.

## Preserve a Run for Later Study

`tmp/` is gitignored, so do not rely on it as the durable record. For a run that
should be studied later:

1. Keep the canonical run bundle under `runs/<run_id>/` intact.
2. Copy or export a redacted study bundle outside git, for example under an
   artifact bucket or `tmp/issue-to-pr-studies/<study_id>/`.
3. Include a manifest with `study_id`, `workflow_version_id`, `task_panel_id`,
   run IDs, patch hashes, redaction status, and raw archive checksum.
4. Keep small redacted examples or summaries in git under
   `docs/issue-to-pr/experiments/`.
5. Do not commit full dumps, full trajectories, provider payloads, checkpoints,
   `.env`, or generated SWE-bench output trees.

For local archives, prefer:

```bash
tar --zstd -cf issue-to-pr-artifacts-<date>-<study_id>.tar.zst <study_dir>
sha256sum issue-to-pr-artifacts-<date>-<study_id>.tar.zst > checksums.sha256
```

Use `tar -czf ...tar.gz` if `zstd` is unavailable. Use zip only for consumers
who need a desktop-friendly archive.

## Trajectory Caveat

The current trajectory export is a derived projection from durable run events.
It usually contains agent input, assistant messages, tool-call starts, and
tool-call completions. It is good enough for debugging SWE-bench attempts and
workflow improvement, but full replay-quality transcripts still require richer
canonical fields in durable events.

Preserve both:

```text
fabro/dump/events.jsonl      # canonical event source
output/trajectory.jsonl      # issue-to-pr friendly projection
```

## Failed Patch Is a Useful Artifact

A failed reviewed patch should not be silently discarded. It should be marked
as unsafe to merge while preserving enough context for another agent:

```json
{
  "state": "failed_with_patch",
  "reuse": "continuation_candidate",
  "warning": "Do not merge as-is; use this patch as a starting point with the review lesson.",
  "failure_class": "test_blocking",
  "failure_reason": "missing regression test",
  "do_not_repeat": ["Do not claim a regression test exists when it is absent from the diff"],
  "next_agent_guidance": "Add the focused regression test first, then rerun verification."
}
```

Only `candidate.state = "ready"` should be publishable. Failed continuation
patches belong in run artifacts, not in SWE-bench `predictions.jsonl`.

## Do Not Commit Scratch State

Do not copy these from scratch directories into tracked files:

- real `.env` values;
- OAuth access or refresh tokens;
- host `~/.codex/auth.json`;
- account IDs;
- run IDs as normative examples;
- container IDs;
- branch names from accidental pushes;
- full dumped prompts/responses/tool payloads;
- absolute local paths;
- fake benchmark checkout SHAs as real examples.

Distill the procedure and the lesson instead.
