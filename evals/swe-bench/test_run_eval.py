from pathlib import Path
import json
import shutil
import tempfile
import unittest
from unittest import mock

import run_eval


PATCH = "diff --git a/file.py b/file.py\n+changed\n"


def ready_result(**overrides):
    result = {
        "instance_id": "task-ready",
        "model_name_or_path": "model",
        "model_patch": PATCH,
        "status": "completed",
        "review_accountability_gate": {
            "status": "passed",
            "route_decision": "export",
            "process_status": "passed",
            "readiness_tier": "ready_verified",
            "adversarial_row_count": 0,
            "moderator_disposition_count": 0,
        },
        "test_evidence_gate": {
            "status": "passed",
            "observed": {"tests_passed_count": 1},
        },
    }
    result.update(overrides)
    return result


class SweBenchSummaryMetricsTest(unittest.TestCase):
    def test_ready_export_is_consistent(self):
        summary = run_eval.summarize_process_metrics([ready_result()])

        self.assertEqual(summary["bad_export_count"], 0)
        self.assertEqual(summary["false_export_count"], 0)
        self.assertEqual(summary["nonempty_exports_with_failed_gates"], 0)
        self.assertEqual(summary["prediction_patch_consistency"], 1.0)
        self.assertEqual(summary["artifact_parse_success_rate"], 1.0)

    def test_current_open_review_block_is_counted(self):
        blocked = ready_result(
            status="failed",
            error="open_review_rows",
            review_accountability_gate={
                "status": "failed",
                "failure_reason": "open_review_rows",
                "open_rows": [{"id": "A1", "state": "open"}],
                "adversarial_row_count": 1,
                "moderator_disposition_count": 1,
            },
            adversarial_review={"rows": [{"id": "A1"}]},
            moderator_filter={"dispositions": [{"id": "A1", "state": "open"}]},
        )

        summary = run_eval.summarize_process_metrics([blocked])

        self.assertEqual(summary["current_open_review_block_count"], 1)
        self.assertEqual(summary["bad_export_count"], 0)
        self.assertEqual(summary["false_export_count"], 0)
        self.assertEqual(summary["prediction_patch_consistency"], 1.0)

    def test_incomplete_review_and_wait_timeout_are_separate_metrics(self):
        result = ready_result(
            status="failed",
            error="review_incomplete_after_fixup",
            review_incomplete_after_fixup=True,
            review_incomplete_stage={"node_id": "adversarial_review", "visit": 3},
            latest_incomplete_stage={"node_id": "adversarial_review", "visit": 3},
            wait_timeout_incomplete_stage={"node_id": "adversarial_review", "visit": 3},
            fabro_wait_timed_out=True,
            review_accountability_gate=None,
        )

        summary = run_eval.summarize_process_metrics([result])

        self.assertEqual(summary["review_incomplete_after_fixup_count"], 1)
        self.assertEqual(
            summary["review_incomplete_stage_counts"],
            {"adversarial_review": 1},
        )
        self.assertEqual(summary["workflow_continued_after_wait_timeout_count"], 1)
        self.assertEqual(summary["wait_timeout_with_incomplete_stage_count"], 1)
        self.assertEqual(
            summary["latest_incomplete_stage_counts"],
            {"adversarial_review": 1},
        )
        self.assertEqual(summary["current_open_review_block_count"], 0)
        self.assertEqual(summary["bad_export_count"], 0)

    def test_continuation_wait_metrics_distinguish_cleared_and_persisting_review(self):
        cleared = ready_result(
            continuation_attempted=True,
            continuation_wait_timed_out=False,
            review_incomplete_after_fixup=False,
        )
        still_incomplete = ready_result(
            status="failed",
            error="review_incomplete_after_fixup",
            continuation_attempted=True,
            continuation_wait_timed_out=True,
            review_incomplete_after_fixup=True,
            review_incomplete_stage={"node_id": "moderator_filter", "visit": 3},
            review_accountability_gate=None,
        )

        summary = run_eval.summarize_process_metrics([cleared, still_incomplete])

        self.assertEqual(summary["continuation_wait_attempt_count"], 2)
        self.assertEqual(summary["continuation_wait_completed_count"], 1)
        self.assertEqual(summary["continuation_wait_timeout_count"], 1)
        self.assertEqual(summary["continuation_cleared_incomplete_review_count"], 1)
        self.assertEqual(summary["continuation_recovered_count"], 1)
        self.assertEqual(summary["review_incomplete_after_continuation_count"], 1)

    def test_raw_patch_blocked_from_export_is_counted(self):
        blocked = ready_result(
            status="failed",
            error="review_incomplete_after_fixup",
            review_incomplete_after_fixup=True,
            review_accountability_gate=None,
        )

        summary = run_eval.summarize_process_metrics([blocked])

        self.assertEqual(summary["raw_patch_blocked_from_export_count"], 1)
        self.assertEqual(summary["bad_export_count"], 0)
        self.assertEqual(summary["false_export_count"], 0)

    def test_post_continuation_dump_failure_preserves_incomplete_review(self):
        result = ready_result(
            review_incomplete_after_fixup=True,
            review_incomplete_stage={"node_id": "adversarial_review", "visit": 3},
        )

        run_eval.collect_workflow_records(
            result,
            workflow_profile=run_eval.STRUCTURED_MODERATED_PROFILE,
            fabro_run_dir=None,
            dumped=None,
            preserve_incomplete_review=True,
        )

        self.assertTrue(result["review_incomplete_after_fixup"])
        self.assertEqual(
            result["review_incomplete_stage"],
            {"node_id": "adversarial_review", "visit": 3},
        )

    def test_stale_review_artifact_pairing_is_derived_from_result_shape(self):
        result = ready_result(
            status="failed",
            error="review_incomplete_after_fixup",
            review_incomplete_after_fixup=True,
            review_incomplete_stage={"node_id": "adversarial_review", "visit": 3},
            adversarial_review={"rows": [{"id": "old"}]},
            review_accountability_gate=None,
        )

        summary = run_eval.summarize_process_metrics([result])

        self.assertEqual(summary["stale_review_artifact_pairing_count"], 1)

    def test_artifact_error_lowers_parse_success_rate(self):
        summary = run_eval.summarize_process_metrics(
            [ready_result(), ready_result(artifact_error="could not write bundle")]
        )

        self.assertEqual(summary["artifact_parse_success_rate"], 0.5)

    def test_latest_incomplete_review_stage_names_visit(self):
        with tempfile.TemporaryDirectory() as tmp:
            stages = Path(tmp) / "stages"
            (stages / "020-materialize_review_artifacts@2").mkdir(parents=True)
            (stages / "020-materialize_review_artifacts@2" / "status.json").write_text("{}")
            (stages / "021-review_accountability_gate@2").mkdir()
            (stages / "021-review_accountability_gate@2" / "output.log").write_text("{}")
            incomplete = stages / "027-adversarial_review@3"
            incomplete.mkdir()
            (incomplete / "prompt.md").write_text("review")

            self.assertEqual(
                run_eval.latest_incomplete_review_stage(Path(tmp)),
                {
                    "node_id": "adversarial_review",
                    "stage_dir": "027-adversarial_review@3",
                    "visit": 3,
                },
            )

    def test_latest_incomplete_stage_includes_non_review_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            stages = Path(tmp) / "stages"
            (stages / "021-review_accountability_gate@2").mkdir(parents=True)
            (stages / "021-review_accountability_gate@2" / "status.json").write_text("{}")
            incomplete = stages / "023-verify@3"
            incomplete.mkdir()
            (incomplete / "prompt.md").write_text("verify")

            self.assertEqual(
                run_eval.latest_incomplete_stage(Path(tmp)),
                {
                    "node_id": "verify",
                    "stage_dir": "023-verify@3",
                    "visit": 3,
                },
            )

    def test_timeout_with_non_review_incomplete_stage_is_counted(self):
        result = ready_result(
            status="failed",
            error="review_artifact_missing_or_malformed",
            fabro_wait_timed_out=True,
            latest_incomplete_stage={"node_id": "verify", "visit": 3},
            wait_timeout_incomplete_stage={"node_id": "verify", "visit": 3},
            review_accountability_gate=None,
        )

        summary = run_eval.summarize_process_metrics([result])

        self.assertEqual(summary["wait_timeout_with_incomplete_stage_count"], 1)
        self.assertEqual(summary["latest_incomplete_stage_counts"], {"verify": 1})
        self.assertEqual(summary["review_incomplete_after_fixup_count"], 0)

    def test_successful_continuation_preserves_initial_timeout_stage_metric(self):
        result = ready_result(
            status="completed",
            fabro_wait_timed_out=True,
            continuation_attempted=True,
            continuation_reason="latest_incomplete_stage",
            latest_incomplete_stage=None,
            wait_timeout_incomplete_stage={"node_id": "verify", "visit": 3},
        )

        summary = run_eval.summarize_process_metrics([result])

        self.assertEqual(summary["wait_timeout_with_incomplete_stage_count"], 1)
        self.assertEqual(summary["latest_incomplete_stage_counts"], {"verify": 1})
        self.assertEqual(summary["continuation_recovered_count"], 1)

    def test_prepare_fabro_env_bridges_codex_vault_into_isolated_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source-storage"
            source_vault = source / "vaults" / "default" / "secrets.json"
            source_vault.parent.mkdir(parents=True)
            source_vault.write_text(
                json.dumps({"OPENAI_CODEX": {"type": "oauth", "token": "fake"}})
            )
            output_dir = root / "out"
            output_dir.mkdir()

            env = run_eval.prepare_fabro_env(
                output_dir=output_dir,
                fabro_bin="/bin/true",
                credential_bridge="openai-codex",
                auth_storage_dir=source,
                credential_preflight=False,
                provider="openai",
                model="gpt-5.5",
            )

            self.assertIsNotNone(env)
            assert env is not None
            self.assertEqual(env["FABRO_STORAGE_DIR"], str((output_dir / "fabro-storage").resolve()))
            self.assertEqual(env["FABRO_SERVER"], str((output_dir / "fabro-storage" / "fabro.sock").resolve()))
            target_vault = output_dir / "fabro-storage" / "vaults" / "default" / "secrets.json"
            self.assertEqual(
                json.loads(target_vault.read_text())["OPENAI_CODEX"]["type"],
                "oauth",
            )
            bridge = json.loads((output_dir / "credential_bridge.json").read_text())
            self.assertEqual(bridge["status"], "copied")
            self.assertTrue((output_dir / "fabro-settings.toml").exists())

    def test_preflight_disk_space_fails_before_eval_when_output_fs_is_full(self):
        usage = shutil._ntuple_diskusage(total=100, used=99, free=1)

        with mock.patch("run_eval.shutil.disk_usage", return_value=usage):
            with self.assertRaises(SystemExit) as raised:
                run_eval.preflight_disk_space(Path("/tmp/out"), min_free_gb=2 / (1024 ** 3))

        self.assertEqual(raised.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
