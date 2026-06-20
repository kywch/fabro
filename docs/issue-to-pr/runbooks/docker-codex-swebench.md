# Docker + Codex SWE-Bench Runbook

This runbook is a self-contained path for test-driving Fabro, SWE-bench, and
Codex/OpenAI through Docker. It intentionally avoids copying scratch secrets,
run IDs, local absolute paths, or dumped transcripts.

## Preconditions

- Docker daemon is available to the Fabro server.
- Docker Compose v2 is available.
- `cargo`, `uv`, `openssl`, and Python are available on the host.
- Network access can reach GitHub, PyPI, Hugging Face datasets, Docker image
  registries, and OpenAI auth/model endpoints.
- The host can build or pull the Fabro server image used by this smoke.

Run the repository preflight first:

```bash
./docker/preflight.sh
```

Build a local CLI before running the rest of this page:

```bash
cargo build -p fabro-cli
export FABRO_BIN="$PWD/target/debug/fabro"
"$FABRO_BIN" version
```

## Start a Docker-backed Fabro Server

Choose the server image first. To test the current checkout, build a local
server image and use the tracked local-image compose override:

```bash
bun install --cwd apps/fabro-web
cargo dev docker-build --tag fabro-smoke-local --arch amd64
```

If you intentionally want to test the published nightly server image, omit
`docs/issue-to-pr/examples/server/docker-compose.local-image.override.yaml` from
the compose commands below.

For local smoke testing, use a throwaway compose project and a minimal server
configuration. The scratch run showed two important traps:

- `docker compose --env-file` is for Compose interpolation; it does not replace
  every service-level env-file behavior.
- `FABRO_DEV_TOKEN` must be `fabro_dev_` plus 64 hex characters.

Create local smoke files under `tmp/issue-to-pr-smoke/`:

```bash
mkdir -p tmp/issue-to-pr-smoke/environments
export FABRO_PORT=32276
export SESSION_SECRET="$(openssl rand -hex 32)"
export FABRO_DEV_TOKEN="fabro_dev_$(openssl rand -hex 32)"
cp docs/issue-to-pr/examples/server/docker-settings.toml \
  tmp/issue-to-pr-smoke/settings.toml
cp docs/issue-to-pr/examples/server/docker-environment.toml \
  tmp/issue-to-pr-smoke/environments/docker.toml

cat > tmp/issue-to-pr-smoke/.env <<EOF
FABRO_PORT=$FABRO_PORT
SESSION_SECRET=$SESSION_SECRET
FABRO_DEV_TOKEN=$FABRO_DEV_TOKEN
EOF
```

The tracked examples copied above are intentionally small. The settings file
enables dev-token auth and Docker sandboxes:

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

The Docker environment file selects the default Docker image:

```toml
provider = "docker"

[image]
docker = "buildpack-deps:noble"
```

Start an isolated compose project, copy the settings into Fabro storage, and
restart:

```bash
docker compose \
  --env-file tmp/issue-to-pr-smoke/.env \
  -p fabro-issue-to-pr-smoke \
  -f docker-compose.yaml \
  -f docs/issue-to-pr/examples/server/docker-compose.local-image.override.yaml \
  -f docs/issue-to-pr/examples/server/docker-compose.dev-token.override.yaml \
  up -d

docker cp tmp/issue-to-pr-smoke/settings.toml \
  fabro-issue-to-pr-smoke-fabro-1:/storage/.home/settings.toml
docker exec fabro-issue-to-pr-smoke-fabro-1 \
  mkdir -p /storage/.home/environments
docker cp tmp/issue-to-pr-smoke/environments/docker.toml \
  fabro-issue-to-pr-smoke-fabro-1:/storage/.home/environments/docker.toml

docker compose \
  --env-file tmp/issue-to-pr-smoke/.env \
  -p fabro-issue-to-pr-smoke \
  -f docker-compose.yaml \
  -f docs/issue-to-pr/examples/server/docker-compose.local-image.override.yaml \
  -f docs/issue-to-pr/examples/server/docker-compose.dev-token.override.yaml \
  up -d --force-recreate
```

The override is required because the root `docker-compose.yaml` has
`env_file: .env` for the service. Compose's `--env-file` supplies interpolation
values, but it does not make the service read `tmp/issue-to-pr-smoke/.env` as
its own env file.

Verify server health:

```bash
docker inspect --format '{{.State.Health.Status}}' \
  fabro-issue-to-pr-smoke-fabro-1
curl -fsS http://127.0.0.1:32276/health
```

Do not commit `.env`, OAuth tokens, refresh tokens, or host
`~/.codex/auth.json` content.

## Authenticate Codex/OpenAI

Preferred path:

```bash
"$FABRO_BIN" auth login \
  --server http://127.0.0.1:32276/api/v1 \
  --dev-token "$FABRO_DEV_TOKEN"

"$FABRO_BIN" provider login --provider openai \
  --server http://127.0.0.1:32276/api/v1
"$FABRO_BIN" model test \
  --server http://127.0.0.1:32276/api/v1 \
  --provider openai \
  --model gpt-5.4-mini
```

The provider login uses device-code auth and stores an OAuth credential in the
server vault. In a local trusted smoke, importing host Codex auth into the vault
can avoid repeating the browser flow, but treat that as an operator convenience,
not a committed script with real tokens.

For Codex subscription-style auth, use the device-code OAuth flow above. API-key
login via `--api-key-stdin` is a different credential path; use it only when the
provider/model you are testing is expected to work with an API key.

The model probe is the auth check. If it fails, fix provider auth before running
SWE-bench; generation will otherwise fail inside the workflow.

### Mini-SWE Codex/ChatGPT Auth Bridge

The mini-SWE model runner starts throwaway Fabro storage for each attempt. That
storage does not automatically inherit the OAuth credential from the already
authenticated Fabro server. To use the same Codex/ChatGPT path that the
SWE-bench smoke uses, copy the server vault into an ignored scratch auth storage
root and pass it with `--credential-bridge openai-codex`.

For a Docker-backed local server, first identify the running Fabro container and
confirm that the server has an `OPENAI_CODEX` OAuth secret:

```bash
docker ps --format '{{.Names}} {{.Status}}' | grep fabro
"$FABRO_BIN" secret list --json
"$FABRO_BIN" model test --provider openai --model gpt-5.4-mini
```

Do not print or commit secret values. The secret list should show the secret
name and type only:

```json
{ "name": "OPENAI_CODEX", "type": "oauth" }
```

Create an ignored scratch auth storage root, copy only the vault file from the
server, and restrict local permissions:

```bash
export MINI_SWE_AUTH_STORAGE=tmp/issue-to-pr-mini-swe-auth
export FABRO_CONTAINER=fabro-smoke-fabro-1

mkdir -p "$MINI_SWE_AUTH_STORAGE/vaults/default"
docker cp \
  "$FABRO_CONTAINER:/storage/vaults/default/secrets.json" \
  "$MINI_SWE_AUTH_STORAGE/vaults/default/secrets.json"
chmod 600 "$MINI_SWE_AUTH_STORAGE/vaults/default/secrets.json"
```

If the container name differs, set `FABRO_CONTAINER` to the name from
`docker ps`. If the server is not Docker-backed, set `MINI_SWE_AUTH_STORAGE` to
the storage root whose
`vaults/default/secrets.json` contains `OPENAI_CODEX` as an OAuth entry. The
root passed to mini-SWE is the directory that contains `vaults/`, not the
`secrets.json` file itself. Keep this storage under ignored scratch space such
as `tmp/`.

Run a one-case canary before the full dev panel:

```bash
export MINI_SWE_OUTPUT_ROOT=tmp/issue-to-pr-mini-swe

python3 -m fabro_kits.issue_to_pr.light_eval mini-swe \
  --case good-test-only \
  --suite dev \
  --attempt model \
  --provider openai \
  --model gpt-5.4-mini \
  --credential-bridge openai-codex \
  --auth-storage-dir "$MINI_SWE_AUTH_STORAGE" \
  --credential-preflight \
  --output-dir "$MINI_SWE_OUTPUT_ROOT/codex-bridge-canary-good-test-only" \
  --fabro-bin target/debug/fabro \
  --format json
```

Expected canary signal:

- `credential_bridge.status = copied`
- `credential_preflight.status = passed`
- `process_blocked = 0`
- `completed = 1`
- `false_exports = 0`

Then run the dev panel with the same bridge flags and a round-specific output
directory:

```bash
python3 -m fabro_kits.issue_to_pr.light_eval mini-swe \
  --suite dev \
  --attempt model \
  --provider openai \
  --model gpt-5.4-mini \
  --credential-bridge openai-codex \
  --auth-storage-dir "$MINI_SWE_AUTH_STORAGE" \
  --credential-preflight \
  --output-dir "$MINI_SWE_OUTPUT_ROOT/output-mini-swe-model-dev" \
  --fabro-bin target/debug/fabro \
  --format json
```

If the run reports `provider_not_configured`, `credential_bridge_failed`, or
`credential_preflight_failed`, treat it as an auth/process setup failure rather
than model quality evidence. Re-check the source auth storage root, the copied
vault metadata, and the model probe:

```bash
"$FABRO_BIN" model test --provider openai --model gpt-5.4-mini
```

### Fresh Local Server Smoke

When testing the current checkout with a throwaway local server, authenticate
that fresh server separately. A clean `HOME`/storage directory will not inherit
the host server's `OPENAI_CODEX` vault secret, even if the default Fabro server
already passes `model test`.

Use an explicit HTTP target for the smoke:

```bash
export FABRO_SERVER=http://127.0.0.1:$FABRO_PORT/api/v1
export HOME=/path/to/fresh/fabro-home

"$FABRO_BIN" auth login \
  --server "$FABRO_SERVER" \
  --dev-token "$FABRO_DEV_TOKEN"
"$FABRO_BIN" provider login --provider openai \
  --server "$FABRO_SERVER"
"$FABRO_BIN" model test \
  --server "$FABRO_SERVER" \
  --provider openai \
  --model gpt-5.4-mini
```

Avoid Unix-socket targets for this smoke unless you have also written complete
server settings into the active `HOME`. Some CLI paths may attempt local
auto-start for socket targets and fail before reaching the already-running
server. An HTTP target plus `FABRO_SERVER` keeps subprocesses, including
`run_eval.py`, pointed at the intended server.

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
  --fabro-bin "$FABRO_BIN" \
  --max-workers 1 \
  --workflow-profile structured \
  --verify-mode diff-check \
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

The command above uses the structured workflow profile:

```text
setup -> research -> implement -> verify
verify -> snapshot_patch -> audit -> review   # pass
verify -> fixup -> verify                     # fail/fallback
review -> extract_patch                      # Approve
review -> fixup -> verify                    # Fix
```

The structured profile currently gives `fixup` fewer visits than
`verify`/`audit`/`review`, so repeated review/fix cycles are expected but capped.

`--verify-mode diff-check` only proves that the patch is nonempty and
whitespace-clean. Correctness still needs review and, for benchmark claims,
grading.

## Expected Outputs

For server-backed runs, inspect:

```text
<output>/
  manifest.json
  predictions.jsonl
  results.jsonl
  summary.json
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
    output/test_evidence_gate.json              # gated/moderated profiles
    output/adversarial_review.json              # moderated profile
    output/moderator_filter.json                # moderated profile
    output/review_materialization.json          # moderated profile
    output/review_accountability_gate.json      # moderated profile
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

## Grading

For a local official-harness grading pass on the one-task smoke:

```bash
cd evals/swe-bench
../../tmp/swebench-venv/bin/python evaluate.py \
  --predictions ../../tmp/swebench-results/docker-codex-smoke/predictions.jsonl \
  --instance-ids django__django-11099 \
  --max-workers 1 \
  --run-id docker-codex-smoke-grade
```

This writes SWE-bench evaluation output under the predictions directory using
the `--run-id`. If grading is skipped, record the skip reason in the experiment
summary; do not treat smoke success as a resolved SWE-bench task.

## Quick Failure Map

| Symptom | Likely cause | Check |
| --- | --- | --- |
| Server stays in install mode | settings not copied into `/storage/.home` | `docker exec ... ls /storage/.home` |
| Server rejects dev token | token is not `fabro_dev_` plus 64 hex chars | inspect `tmp/issue-to-pr-smoke/.env` |
| Docker sandbox never starts | Fabro container cannot reach Docker socket | `docker logs fabro-issue-to-pr-smoke-fabro-1` |
| `model test` fails | OpenAI/Codex auth missing or expired | rerun `"$FABRO_BIN" provider login --provider openai --server http://127.0.0.1:32276/api/v1` |
| Fresh checkout server says no LLM providers configured | `OPENAI_CODEX` exists only in another server vault | run `auth login`, `provider login --provider openai`, and `model test` against the fresh server target |
| Fresh socket smoke tries to start another server | CLI socket target auto-start used incomplete active settings | prefer an explicit HTTP target and export `FABRO_SERVER` for the eval subprocess |
| Run waits for input | workflow is interactive or missing auto approval | use `--auto-approve` and noninteractive prompts |
| No patch | agent made no diff or review rejected before publish | inspect `run.json`, `candidate`, and `fabro/dump/events.jsonl` |
| Prediction patch is blank | candidate was failed/rejected | inspect `output/patch.diff` and `candidate.state` |
