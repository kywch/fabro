# Issue-to-PR

This directory is the tracked home for the issue-to-PR workflow area: how to run
it, how the staged process is expected to behave, what the output artifacts
mean, and what experiment cycles have taught us.

Raw run outputs, trajectories, prompts, logs, checkpoints, provider payloads,
and generated SWE-bench result trees do not belong in this directory. Keep those
in `tmp/`, CI artifacts, or another artifact store; track distilled summaries,
contracts, decisions, and redacted examples here.

This directory should remain self-sufficient even though `tmp/` is gitignored:
tracked examples show the artifact shape, while full raw evidence is preserved
as external archives with checksums and redaction notes.

The reusable Python code for this area lives under `fabro_kits.issue_to_pr`.
This docs directory keeps the human-facing `issue-to-PR` spelling; Python uses
underscores for importable package names.

## Start Here

- Run one Docker/Codex SWE-bench task:
  [runbooks/docker-codex-swebench.md](runbooks/docker-codex-swebench.md)
- Avoid accidental pushes and inspect dumps:
  [runbooks/no-push-and-artifacts.md](runbooks/no-push-and-artifacts.md)
- Understand the current staged workflow lanes:
  [process/README.md](process/README.md) and
  [process/staged-workflow.md](process/staged-workflow.md)
- Interpret run bundles and candidate states:
  [artifacts/README.md](artifacts/README.md)
- Review curated workflow-improvement experiments:
  [experiments/README.md](experiments/README.md) and
  [experiments/template.md](experiments/template.md)
- Copy sanitized example config:
  [examples/server](examples/server) and [examples/workflows](examples/workflows)
- Inspect sanitized artifact examples:
  [examples/artifacts](examples/artifacts)

## Directory Map

```text
docs/issue-to-pr/
  runbooks/     # executable operator procedures
  process/      # staged workflow and auto-research mechanics
  artifacts/    # output layout and candidate/trajectory contracts
  experiments/  # curated historical improvement cycles
  examples/     # sanitized config and workflow snippets
```

The short version: runbooks tell you how to operate the system; process docs
tell you how it should behave; artifacts docs tell consumers what the output
means; experiments docs preserve what changed future behavior.
