# Docker + Codex SWE-Bench Runbook

This runbook distills the live smoke artifacts from `tmp/fabro-docker-smoke`
into a repeatable path. It intentionally avoids copying scratch secrets, run
IDs, local absolute paths, or dumped transcripts.

## Preconditions

- Docker daemon is available to the Fabro server.
- The Fabro CLI has been built or installed.
- The server has a Docker environment enabled.
- OpenAI/Codex auth is configured in the Fabro server vault.
- Python dependencies for `evals/swe-bench` are installed.

Run the repository preflight first:

```bash
./docker/preflight.sh
```

## Start a Docker-backed Fabro Server

For local smoke testing, use a throwaway compose project and a minimal server
configuration. The scratch run showed two important traps:

- `docker compose --env-file` is for Compose interpolation; it does not replace
  every service-level env-file behavior.
- `FABRO_DEV_TOKEN` must be `fabro_dev_` plus 64 hex characters.

Minimal server settings:

```toml
_version = 1

[server.listen]
type = "tcp"
address = "0.0.0.0:32276"

[server.api]
url = "http://127.0.0.1:32276/api/v1"

[server.web]
enabled = true
url = "http://127.0.0.1:32276"

[server.auth]
methods = ["dev-token"]

[server.storage]
root = "/storage"

[server.sandbox.providers.local]
enabled = false

[server.sandbox.providers.docker]
enabled = true

[server.sandbox.providers.daytona]
enabled = false

[run.environment]
id = "docker"
```

Minimal Docker environment file:

```toml
provider = "docker"

[image]
docker = "buildpack-deps:noble"
```

For trusted local smoke tests, it is acceptable to copy those files into the
server storage directory and restart the container. Do not commit `.env`,
OAuth tokens, refresh tokens, or host `~/.codex/auth.json` content.

## Authenticate Codex/OpenAI

Preferred path:

```bash
fabro auth login \
  --server http://127.0.0.1:32276/api/v1 \
  --dev-token "$FABRO_DEV_TOKEN"

fabro provider login --provider openai
fabro model test --provider openai --model gpt-5.4-mini
```

The provider login uses device-code auth and stores an OAuth credential in the
server vault. In a local trusted smoke, importing host Codex auth into the vault
can avoid repeating the browser flow, but treat that as an operator convenience,
not a committed script with real tokens.

## Python Environment

Create a local scratch environment:

```bash
uv venv tmp/swebench-venv
uv pip install --python tmp/swebench-venv/bin/python \
  -r evals/swe-bench/requirements.txt
uv pip install --python tmp/swebench-venv/bin/python 'modal<1'
```

The `modal<1` pin is needed with `swebench==2.1.8`; newer Modal releases removed
APIs imported by the SWE-bench harness.

## One-task Docker/Codex Smoke

Run one instance first:

```bash
cd evals/swe-bench
../../tmp/swebench-venv/bin/python run_eval.py \
  --model gpt-5.4-mini \
  --provider openai \
  --sandbox-provider docker \
  --fabro-bin ../../target/debug/fabro \
  --max-workers 1 \
  --workflow-profile structured \
  --verify-mode diff-check \
  --output-layout both \
  --instance-ids django__django-11099 \
  --output-dir ../../tmp/swebench-results/docker-codex-smoke
```

The Docker path builds one local SWE-bench image per `(repo, version)` using
`evals/swe-bench/gen_dockerfile.py`. The first run for a repo/version can be
slow; later runs reuse the image.

Each generated workflow should:

- disable Fabro PR creation;
- disable Fabro clone, run branch, and meta branch behavior;
- use a Docker environment with the SWE-bench image;
- clone the target SWE-bench repository in the setup stage;
- extract the candidate patch with `git diff`.

## Expected Outputs

For server-backed runs, inspect:

```text
<output>/
  manifest.json
  exports/swebench/
  runs/<instance_id>--001/
    run.json
    task.json
    input/goal.md
    config/workflow.fabro
    config/workflow.toml
    output/patch.diff
    output/prediction.json
    output/verify.json
    output/audit.json
    output/trajectory.jsonl
    fabro/dump/events.jsonl
```

`trajectory.jsonl` is derived from `fabro/dump/events.jsonl`. It is useful for
debugging agent behavior, but it is not yet a full replay-quality transcript.
When in doubt, treat `events.jsonl` as the source of truth.

## What Counts as Success

For a smoke:

- Fabro run reaches a terminal state without hanging for input.
- The Docker sandbox provider is used.
- `patch.diff` is present when the agent made a change.
- `run.json` and `manifest.json` point to the same run bundle.
- `fabro/dump/events.jsonl` exists.
- `trajectory.jsonl` exists when dump conversion succeeded.
- Review-rejected patches remain available as continuation artifacts but are
  not exported as nonempty SWE-bench predictions.

For benchmark claims, smoke success is not enough. Run SWE-bench grading or
record why grading was skipped.
