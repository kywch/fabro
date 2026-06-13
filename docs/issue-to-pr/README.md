# Issue-to-PR Operations Notes

This directory collects the tracked runbooks for turning issue-to-PR experiments
into repeatable Fabro workflows. The first focus is local Docker SWE-bench runs
using OpenAI/Codex auth, because that path exercises the same pieces a VPS
factory needs: a Fabro server, Docker sandboxes, provider credentials, run
artifacts, patches, and trajectories.

## Operator Path

Use this order when bringing up or debugging the pipeline:

1. Start a Fabro server with the Docker sandbox provider enabled.
2. Log the CLI into that server.
3. Configure OpenAI/Codex auth in the server vault.
4. Prove the provider with `fabro model test`.
5. Run a no-push tutorial smoke.
6. Run one SWE-bench task through local Docker with `--max-workers 1`.
7. Inspect the output bundle: `run.json`, `patch.diff`, `verify.json`,
   `audit.json`, `review` metadata, `fabro/dump/events.jsonl`, and
   `trajectory.jsonl`.
8. Only then scale the task count or change workflow prompts.

Useful docs:

- [Docker + Codex SWE-bench](swebench-docker-codex.md)
- [No-push and Artifact Lessons](no-push-and-artifacts.md)

## Product Boundary

Fabro should own the staged attempt:

```text
setup -> research -> implement -> verify -> snapshot -> audit -> review -> fixup -> candidate
```

The surrounding harness or VPS service should own intake, queueing, credentials
policy, Docker daemon policy, grading, and artifact retention.

## Safety Defaults

For smoke tests and evals, disable repository side effects unless a scratch fork
is intentionally configured:

```toml
[run.pull_request]
enabled = false

[run.clone]
enabled = false

[run.run_branch]
enabled = false

[run.meta_branch]
enabled = false
```

`run.clone.enabled = false` is especially important for SWE-bench local Docker:
the generated workflow clones the target repository inside the sandbox setup
stage, and Fabro should not also clone or push branches for the product repo.

## Artifact Rule

Treat `runs/<run_id>/` as the product-facing shape. SWE-bench root JSONL files
are compatibility exports. A failed patch can be useful, but only
`candidate.state = "ready"` should be considered publishable.
