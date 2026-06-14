# Fabro Kits

`fabro_kits` holds reusable Python tooling for Fabro-powered workflow patterns.
A kit can include workflow graph generators, runners, artifact contracts,
trajectory helpers, prompts, adapters, and small CLIs.

This package is intentionally separate from `.fabro/workflows/` and
`fabro-workflow`: those names refer to runnable workflow definitions and the Rust
workflow engine. Kits are Python-side tooling around repeatable workflow
families.

Current kits:

- `fabro_kits.issue_to_pr` - normalized issue-to-PR attempts, artifact bundles,
  and generated staged workflows.

Benchmark adapters, such as SWE-bench, should stay under `evals/` unless they
become reusable outside evaluation. Eval scripts may import kits, but kits should
not depend on eval directories.
