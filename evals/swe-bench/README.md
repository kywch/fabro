# SWE-Bench-Lite Evaluation

Evaluates Fabro's agent on [SWE-Bench-Lite](https://www.swebench.com/) (300 Python bug-fix tasks across 12 repos). Two phases: generate patches, then evaluate them. Generation supports Daytona cloud sandboxes and local Docker sandboxes.

## Setup

```bash
cd evals/swe-bench
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Step 1: Generate patches

Runs Fabro's agent on each SWE-bench instance to produce a fix.

```bash
python run_eval.py \
    --model claude-haiku-4-5 \
    --provider anthropic \
    --output-dir results/haiku-baseline \
    2>&1 | tee results/haiku-baseline/console.log
```

**Options:**
- `--model` — LLM model (default: `claude-haiku-4-5`)
- `--provider` — LLM provider (default: `anthropic`)
- `--sandbox-provider` — sandbox provider, `daytona` or `docker` (default: `daytona`)
- `--fabro-bin` — Fabro CLI binary to execute (default: `fabro`)
- `--max-workers` — max concurrent sandboxes (default: 75)
- `--timeout` — per-instance timeout in seconds (default: 1200)
- `--continuation-timeout` — optional second wait for runs that timed out
  while a workflow stage was still incomplete
- `--min-free-gb` — minimum free GiB required on the output filesystem before
  starting (default: 20)
- `--credential-bridge openai-codex` — copy an existing `OPENAI_CODEX` vault
  credential into isolated SWE-bench Fabro storage
- `--auth-storage-dir` — source Fabro storage root for the credential bridge
- `--credential-preflight` — run `fabro model test` before launching tasks
- `--instance-ids` — run only specific instances (e.g. `--instance-ids django__django-11099`)

### Local Docker + Codex smoke

For a one-instance local smoke using OpenAI/Codex credentials already stored in
Fabro's server vault:

```bash
../../tmp/swebench-venv/bin/python run_eval.py \
    --model gpt-5.4-mini \
    --provider openai \
    --sandbox-provider docker \
    --fabro-bin ../../target/debug/fabro \
    --max-workers 1 \
    --instance-ids django__django-11099 \
    --credential-bridge openai-codex \
    --auth-storage-dir ../../tmp/issue-to-pr-auth-storage \
    --credential-preflight \
    --output-dir ../../tmp/swebench-results/gpt54-mini-django-11099
```

The Docker path builds and reuses one local image per `(repo, version)`. It
generates workflow configs with cloning, PRs, and managed run/meta branches
disabled, so it does not push `fabro/run/*` or `fabro/meta/*` refs to the
current origin.

For server-backed Fabro runs, each result row also records:

- `fabro_run_id` — the Fabro run id
- `fabro_dump_dir` — the local `fabro dump` directory for that instance
- `events_path` — the dumped `events.jsonl` event stream
- `trajectory_path` — a derived `trajectory.jsonl` with agent input, assistant
  message, tool call, and tool result events extracted from the dump

**Monitor:**
```bash
python status.py results/haiku-baseline    # quick summary
tail -f results/haiku-baseline/eval.log    # live per-instance results
fabro ps                                   # active sandboxes
fabro logs <RUN_ID>                        # stream a specific run
```

## Step 2: Evaluate patches

Applies each patch, runs the held-out test suite, and grades pass/fail using swebench's log parsers.

```bash
python evaluate_daytona.py \
    --predictions results/haiku-baseline/predictions.jsonl \
    --output-dir results/haiku-baseline/eval \
    2>&1 | tee results/haiku-baseline/eval/console.log
```

**Options:**
- `--max-workers` — max concurrent eval sandboxes (default: 100)
- `--timeout` — per-instance timeout in seconds (default: 600)
- `--instance-ids` — evaluate only specific instances

**Monitor:**
```bash
python status.py results/haiku-baseline/eval   # quick summary
tail -f results/haiku-baseline/eval/eval_grade.log
```

## Step 3: Record results

Saves results to the git-tracked `scoreboard/` directory for permanent record-keeping.

```bash
python record_results.py \
    --run-name haiku-baseline-20260316 \
    --gen-dir results/haiku-baseline \
    --eval-dir results/haiku-baseline/eval \
    --description "Haiku 4.5 baseline, default prompt, 2 CPU / 4 GB, 10min timeout"
```

Then commit the scoreboard:
```bash
git add scoreboard/
git commit -m "Record haiku-baseline-20260316: XX.X% on SWE-Bench-Lite"
```

## Scoreboard

Results are stored in `scoreboard/`:

```
scoreboard/
├── leaderboard.json                    # all runs ranked by resolve rate
└── haiku-baseline-20260316/
    ├── README.md                       # human-readable summary
    ├── meta.json                       # run metadata, costs, per-repo stats
    └── instances.jsonl                 # per-instance: has_patch, resolved, duration, cost
```

View the leaderboard:
```bash
cat scoreboard/leaderboard.json | python3 -m json.tool
```

## Generation outputs

`run_eval.py` keeps the root SWE-bench exports for compatibility:

```text
<output>/
├── manifest.json
├── predictions.jsonl
├── results.jsonl
├── summary.json
└── configs/<instance_id>/
    ├── task.json
    ├── attempt.json
    ├── patch.diff
    ├── prediction.json
    ├── verify.json          # only when a verify stage ran
    ├── trajectory.jsonl     # promoted agent trajectory when dump is available
    ├── goal.txt
    ├── workflow.fabro
    ├── workflow.toml
    └── run_dump/
```

When `--output-layout both` or `--output-layout runs-v1` is used, `run_eval.py`
also writes a runs-first mirror:

```text
<output>/
├── exports/swebench/
│   ├── predictions.jsonl
│   ├── results.jsonl
│   └── summary.json
└── runs/<instance_id>--001/
    ├── run.json
    ├── task.json
    ├── input/
    │   ├── goal.md
    │   └── envelope.json
    ├── config/
    │   ├── workflow.fabro
    │   └── workflow.toml
    ├── fabro/dump/
    └── output/
        ├── patch.diff
        ├── prediction.json
        ├── verify.json
        ├── trajectory.jsonl
        └── envelope.json
```

`predictions.jsonl`, `results.jsonl`, and `summary.json` are SWE-bench export
files. New tooling should prefer `manifest.json` and the per-instance
`task.json` / `attempt.json` / `patch.diff` sidecars, which are shaped as a
general issue-to-PR solve-attempt record.

By default, generation uses the compatibility workflow:

```text
setup -> solve -> extract_patch
```

For a more structured issue-to-PR loop, use:

```bash
python run_eval.py \
  --workflow-profile structured \
  --verify-mode diff-check \
  --output-layout both \
  --sandbox-provider docker \
  --instance-ids django__django-11099
```

The structured profile runs:

```text
setup -> research -> implement -> verify
verify -> extract_patch  # on success
verify -> fixup -> verify  # on failure, capped at one fixup visit
```

`diff-check` verification is intentionally light: it fails empty patches and
`git diff --check` whitespace errors. It does not run SWE-bench held-out grading
tests during generation. Grading remains a separate evaluation step.

Agent trajectory is derived from Fabro `events.jsonl` after `fabro dump`. The
raw dump remains under `fabro/dump/`; a promoted `trajectory.jsonl` is also
written beside generation outputs so issue-to-PR tooling can find it without
knowing Fabro dump internals.

## File inventory

| File | Purpose |
|------|---------|
| `status.py` | Check progress of a running or completed generation/evaluation |
| `run_eval.py` | Generate patches (step 1) using the reusable `fabro_kits.issue_to_pr` kit |
| `../../fabro_kits/issue_to_pr/` | Reusable issue-to-PR runner, workflow generator, artifacts, and tests |
| `evaluate_daytona.py` | Evaluate patches on Daytona (step 2) |
| `evaluate.py` | Evaluate patches via official swebench Docker harness (alternative to step 2) |
| `record_results.py` | Record results to scoreboard (step 3) |
| `gen_dockerfile.py` | Generate per-(repo, version) Dockerfiles from swebench specs |
| `workflow.fabro` | DOT workflow template (unused — per-instance .fabro files are generated) |
| `requirements.txt` | Python dependencies: `swebench`, `datasets` |
| `scoreboard/` | Git-tracked results (committed) |
| `results/` | Raw run data — predictions, logs, patches (gitignored) |

## Running a new model

Full end-to-end for a new model:

```bash
# 1. Generate
python run_eval.py \
    --model claude-opus-4-6 --provider anthropic \
    --output-dir results/opus-baseline \
    2>&1 | tee results/opus-baseline/console.log

# 2. Evaluate
python evaluate_daytona.py \
    --predictions results/opus-baseline/predictions.jsonl \
    --output-dir results/opus-baseline/eval \
    2>&1 | tee results/opus-baseline/eval/console.log

# 3. Record
python record_results.py \
    --run-name opus-baseline-20260316 \
    --gen-dir results/opus-baseline \
    --eval-dir results/opus-baseline/eval \
    --description "Opus 4.6 baseline, default prompt, 2 CPU / 4 GB, 10min timeout"

# 4. Commit
git add scoreboard/
git commit -m "Record opus-baseline-20260316"
```

## Sandbox resources

Each Daytona sandbox uses 2 CPU / 4 GB RAM / 10 GB disk. Snapshots are cached by name — first build is slow (~2 min), subsequent uses are instant.
