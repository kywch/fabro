import json
import tempfile
import unittest
from pathlib import Path

from fabro_kits.issue_to_pr.artifacts import (
    DEFAULT_ATTEMPT_ID,
    build_candidate_record,
    build_prediction_record,
    load_or_init_manifest,
    run_id_for_task,
    update_manifest_for_run,
    write_manifest,
    write_run_bundle,
)


class RunBundleArtifactsTest(unittest.TestCase):
    def test_completed_run_writes_canonical_bundle(self):
        result = _result(
            model_patch="diff --git a/a.py b/a.py\n",
            status="completed",
            review_accountability_gate={
                "status": "passed",
                "process_status": "passed",
                "readiness_tier": "ready_unverified",
                "route_decision": "export",
                "adversarial_row_count": 1,
                "moderator_disposition_count": 1,
            },
        )

        with tempfile.TemporaryDirectory() as tmp:
            output_dir, config_dir = _workspace(tmp)
            artifacts = write_run_bundle(
                instance=_instance(),
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider="docker",
            )

            run_dir = output_dir / "runs" / "django__django-11099--001"
            self.assertEqual(artifacts["run"], "runs/django__django-11099--001/run.json")
            self.assertEqual(artifacts["patch"], "runs/django__django-11099--001/output/patch.diff")
            self.assertFalse((run_dir / "manifest.json").exists())
            self.assertFalse((run_dir / "output" / "acceptance_audit.json").exists())
            self.assertFalse((run_dir / "output" / "review_ledger.json").exists())

            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual(run["layout"], "issue-to-pr-runs-v1")
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["candidate"]["state"], "ready")
            self.assertEqual(run["candidate"]["reuse"], "merge_candidate")
            self.assertEqual(run["candidate"]["readiness_tier"], "ready_unverified")
            self.assertEqual(
                run["phases"]["review_accountability_gate"]["status"],
                "completed",
            )

            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            self.assertEqual(prediction["model_patch"], result["model_patch"])

    def test_failed_prediction_is_blank_but_patch_is_retained(self):
        result = _result(
            model_patch="diff --git a/a.py b/a.py\n+bad\n",
            status="failed",
            error="Review accountability gate blocked export",
            review_accountability_gate={
                "status": "failed",
                "process_status": "process_failed",
                "readiness_tier": "process_failed",
                "failure_reason": "open_blocker_or_major_rows",
                "fixup_required_rows": [{"id": "A1", "state": "open"}],
                "do_not_repeat": ["Do not drop adversarial rows."],
                "next_agent_guidance": "Fix A1.",
            },
        )

        with tempfile.TemporaryDirectory() as tmp:
            output_dir, config_dir = _workspace(tmp)
            write_run_bundle(
                instance=_instance(),
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider="docker",
            )

            run_dir = output_dir / "runs" / "django__django-11099--001"
            self.assertEqual(
                (run_dir / "output" / "patch.diff").read_text(),
                result["model_patch"],
            )
            prediction = json.loads((run_dir / "output" / "prediction.json").read_text())
            self.assertEqual(prediction["model_patch"], "")

            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual(run["candidate"]["state"], "failed_with_patch")
            self.assertEqual(run["candidate"]["reuse"], "continuation_candidate")
            self.assertEqual(
                run["candidate"]["failure_reason"],
                "open_blocker_or_major_rows",
            )
            self.assertEqual(run["candidate"]["next_agent_guidance"], "Fix A1.")

    def test_review_artifacts_are_copied_without_synthesis(self):
        result = _result(
            model_patch="diff --git a/a.py b/a.py\n",
            status="completed",
            adversarial_review={"stage": "adversarial_review", "rows": []},
            moderator_filter={"stage": "moderator_filter", "dispositions": []},
            review_materialization={"stage": "review_materialization", "status": "passed"},
            review_accountability_gate={"stage": "review_accountability_gate", "status": "passed"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            output_dir, config_dir = _workspace(tmp)
            artifacts = write_run_bundle(
                instance=_instance(),
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider="docker",
            )

            self.assertIn("adversarial_review", artifacts)
            self.assertIn("moderator_filter", artifacts)
            self.assertIn("review_materialization", artifacts)
            self.assertIn("review_accountability_gate", artifacts)
            self.assertNotIn("review_ledger", artifacts)
            self.assertNotIn("acceptance_audit", artifacts)

    def test_root_manifest_indexes_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            manifest = load_or_init_manifest(output_dir)
            update_manifest_for_run(
                manifest,
                task_id="django__django-11099",
                run_id=run_id_for_task("django__django-11099", DEFAULT_ATTEMPT_ID),
                attempt_id=DEFAULT_ATTEMPT_ID,
                output_dir=output_dir,
            )
            write_manifest(output_dir, manifest)

            saved = json.loads((output_dir / "manifest.json").read_text())
            self.assertEqual(saved["layout"], "issue-to-pr-runs-v1")
            self.assertEqual(
                saved["tasks"]["django__django-11099"]["selected_run"],
                "django__django-11099--001",
            )
            self.assertNotIn("manifest_path", saved["runs"]["django__django-11099--001"])

    def test_candidate_patch_bytes_use_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            patch_path = Path(tmp) / "patch.diff"
            result = _result(model_patch="diff\n+é\n", status="completed")
            candidate = build_candidate_record(result, patch_path, Path(tmp))

            self.assertEqual(candidate["patch_bytes"], len("diff\n+é\n".encode()))

    def test_prediction_blanks_non_completed_statuses(self):
        result = _result(model_patch="diff --git a/a.py b/a.py\n", status="failed")
        self.assertEqual(build_prediction_record(result)["model_patch"], "")

    def test_prediction_blanks_completed_result_when_gate_blocks_export(self):
        result = _result(
            model_patch="diff --git a/a.py b/a.py\n+bad\n",
            status="completed",
            review_accountability_gate={
                "status": "failed",
                "process_status": "process_failed",
                "route_decision": "fixup",
                "readiness_tier": "process_failed",
                "failure_reason": "open_review_rows",
            },
        )

        self.assertEqual(build_prediction_record(result)["model_patch"], "")
        with tempfile.TemporaryDirectory() as tmp:
            output_dir, config_dir = _workspace(tmp)
            write_run_bundle(
                instance=_instance(),
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider="docker",
            )

            run = json.loads(
                (
                    output_dir
                    / "runs"
                    / "django__django-11099--001"
                    / "run.json"
                ).read_text()
            )
            self.assertEqual(run["candidate"]["state"], "failed_with_patch")
            self.assertEqual(run["candidate"]["reuse"], "continuation_candidate")

    def test_prediction_blanks_completed_moderated_result_missing_gate(self):
        result = _result(
            model_patch="diff --git a/a.py b/a.py\n+bad\n",
            status="completed",
            adversarial_review={"stage": "adversarial_review", "rows": []},
            moderator_filter={"stage": "moderator_filter", "dispositions": []},
            review_materialization={"stage": "review_materialization", "status": "passed"},
        )

        self.assertEqual(build_prediction_record(result)["model_patch"], "")
        with tempfile.TemporaryDirectory() as tmp:
            output_dir, config_dir = _workspace(tmp)
            write_run_bundle(
                instance=_instance(),
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider="docker",
            )

            run = json.loads(
                (
                    output_dir
                    / "runs"
                    / "django__django-11099--001"
                    / "run.json"
                ).read_text()
            )
            self.assertEqual(run["candidate"]["state"], "failed_with_patch")

    def test_completed_unmoderated_result_still_exports(self):
        result = _result(model_patch="diff --git a/a.py b/a.py\n+ok\n", status="completed")

        self.assertEqual(build_prediction_record(result)["model_patch"], result["model_patch"])


def _workspace(tmp: str) -> tuple[Path, Path]:
    output_dir = Path(tmp)
    config_dir = output_dir / "configs" / "django__django-11099"
    dump_dir = config_dir / "run_dump"
    dump_dir.mkdir(parents=True)
    (config_dir / "goal.txt").write_text("fix it")
    (config_dir / "workflow.fabro").write_text("digraph G {}")
    (config_dir / "workflow.toml").write_text("[workflow]\n")
    (dump_dir / "events.jsonl").write_text("{}\n")
    (dump_dir / "trajectory.jsonl").write_text("{}\n")
    return output_dir, config_dir


def _instance() -> dict:
    return {
        "instance_id": "django__django-11099",
        "repo": "django/django",
        "version": "3.0",
        "base_commit": "abc123",
    }


def _result(**overrides) -> dict:
    result = {
        "instance_id": "django__django-11099",
        "model_name_or_path": "gpt-5.4-mini",
        "model_patch": "",
        "status": "completed",
        "error": None,
        "duration_s": 12.3,
        "fabro_run_id": "01run",
        "fabro_dump_dir": None,
        "trajectory_path": None,
    }
    result.update(overrides)
    return result


if __name__ == "__main__":
    unittest.main()
