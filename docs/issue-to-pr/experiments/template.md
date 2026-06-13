# Experiment Template

Use this template for a tracked experiment summary. Store raw artifacts outside
git and reference them by study ID, path or URI, and hash.

## Metadata

- `study_id`:
- `workflow_version_id`:
- `baseline_workflow_version_id`:
- `hypothesis_id`:
- `task_panel_id`:
- `redaction_version`:
- `raw_artifact_location`:

## Hypothesis

State one process-mechanics hypothesis and, optionally, one work-quality
hypothesis.

## Method

- Baseline:
- Variant:
- Task panel:
- Holdout panel:
- Commands:
- Changed variable:
- Grading plan:

## Metrics

- Solve quality:
- Process quality:
- Artifact quality:
- Failed-with-patch count:
- Continuation-candidate count:
- Grader result or skip reason:

## Artifact References

```json
{
  "study_id": "",
  "runs": [
    {
      "task_id": "",
      "variant": "baseline|variant-a",
      "run_bundle_id": "",
      "raw_sha256": "",
      "patch_sha256": "",
      "candidate_state": "",
      "trajectory_redacted": true
    }
  ]
}
```

## Decision

- Decision: `promote|reject|defer`
- Rationale:
- Risks:
- Follow-up:
