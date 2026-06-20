# Experiments

This lane holds curated experiment cycles that improved the issue-to-PR
workflow. It is intentionally not named `research`, because `research` is also a
workflow stage.

Track distilled material here:

- experiment objective and scope;
- compact metrics and scorecards;
- decisions and lessons that changed future behavior;
- redacted examples when needed;
- pointers or hashes for raw artifacts stored elsewhere.

Do not track raw run bundles, full trajectories, prompts, responses, provider
payloads, run logs, checkpoints, generated SWE-bench output trees, or secrets.

Current cycles:

- [2026-06 Auto-Research V1](2026-06-auto-research-v1.md)
- [2026-06 Auto-Research V2](2026-06-auto-research-v2.md)
- [2026-06 Auto-Research V3](2026-06-auto-research-v3.md)

Templates:

- [Experiment Template](template.md)

## Study Artifact Layout

Use this shape for raw or semi-raw auto-research material outside git. The
tracked experiment summary should point to this bundle by `study_id`, location
or URI, checksum, and redaction status; it should not depend on the local `tmp/`
tree being present.

```text
tmp/issue-to-pr-studies/<study_id>/
  study.json
  hypotheses/
    H001-test-evidence-gate.md
  workflows/
    baseline/
      workflow.fabro
      workflow.toml
    variant-a/
      workflow.fabro
      workflow.toml
  runs/
    <task_id>/
      baseline/
        run-ref.json
        redacted-summary.json
        patch.diff
        verify.json
        audit.json
        candidate.json
        trajectory.redacted.jsonl
        events.redacted.jsonl
        raw.sha256
      variant-a/
        ...
  scorecards/
    solve-quality.jsonl
    process-quality.jsonl
    artifact-quality.jsonl
  decisions/
    D001-promote-test-evidence-gate.md
```

If the study needs to be portable, package that directory outside git:

```bash
tar --zstd -cf issue-to-pr-artifacts-<date>-<study_id>.tar.zst \
  tmp/issue-to-pr-studies/<study_id>
sha256sum issue-to-pr-artifacts-<date>-<study_id>.tar.zst
```

Track only the curated experiment summary in this directory. The summary should
name the `study_id`, task panel, workflow versions, raw artifact location or
URI, aggregate scores, redaction status, and promotion decision.

## Retention Policy

Commit to git:

- experiment summaries;
- schema and artifact contracts;
- redacted examples;
- workflow version diffs;
- aggregate scorecards;
- patch hashes and candidate counts;
- lessons and promotion decisions.

Store outside git with restricted access:

- raw `fabro dump` directories;
- full `events.jsonl`;
- full `trajectory.jsonl`;
- provider request/response payloads;
- checkpoints;
- run logs;
- generated SWE-bench output trees.

Preserve study value in redacted derivatives by keeping stage names, message and
tool chronology, command names, exit codes, changed files, patch hashes, test
commands, verifier outcomes, review decisions, failure classes, and
next-agent guidance.

Redact or drop secrets, OAuth/API tokens, auth headers, account IDs, host paths,
remote URLs with credentials, container IDs, and accidental branch names unless
they are explicitly needed for a redacted postmortem.
