import json
import tempfile
import unittest
from pathlib import Path

from attempt_artifacts import (
    DEFAULT_ATTEMPT_ID,
    load_or_init_manifest,
    run_id_for_task,
    update_manifest_for_attempt,
    update_manifest_for_run,
    write_attempt_sidecars,
    write_manifest,
    write_run_bundle,
)


class AttemptArtifactsTest(unittest.TestCase):
    def test_writes_generic_sidecars_and_manifest(self):
        instance = {
            "instance_id": "django__django-11099",
            "repo": "django/django",
            "version": "3.0",
            "base_commit": "abc123",
        }
        result = {
            "instance_id": "django__django-11099",
            "model_name_or_path": "gpt-5.4-mini",
            "model_patch": "diff --git a/a.py b/a.py\n",
            "status": "completed",
            "error": None,
            "duration_s": 12.3,
            "fabro_run_id": "01run",
            "fabro_run_dir": None,
            "fabro_dump_dir": None,
            "events_path": None,
            "trajectory_path": None,
        }

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            config_dir = output_dir / "configs" / instance["instance_id"]
            config_dir.mkdir(parents=True)
            (config_dir / "goal.txt").write_text("fix it")

            artifacts = write_attempt_sidecars(
                instance=instance,
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider="docker",
            )
            manifest = load_or_init_manifest(output_dir)
            update_manifest_for_attempt(
                manifest,
                task_id=instance["instance_id"],
                attempt_id=DEFAULT_ATTEMPT_ID,
                output_dir=output_dir,
                config_dir=config_dir,
            )
            write_manifest(output_dir, manifest)

            self.assertEqual(
                artifacts,
                {
                    "task": "configs/django__django-11099/task.json",
                    "attempt": "configs/django__django-11099/attempt.json",
                    "patch": "configs/django__django-11099/patch.diff",
                    "prediction": "configs/django__django-11099/prediction.json",
                },
            )
            self.assertEqual(
                (config_dir / "patch.diff").read_text(),
                "diff --git a/a.py b/a.py\n",
            )

            task = json.loads((config_dir / "task.json").read_text())
            self.assertEqual(task["source"]["kind"], "swe_bench")
            self.assertEqual(task["policy"]["mode"], "patch_only")
            self.assertEqual(task["repository"]["owner"], "django")
            self.assertEqual(task["repository"]["name"], "django")
            self.assertEqual(task["repository"]["full_name"], "django/django")

            attempt = json.loads((config_dir / "attempt.json").read_text())
            self.assertEqual(attempt["phases"]["change"]["status"], "completed")
            self.assertEqual(attempt["phases"]["grade"]["status"], "not_run")
            self.assertEqual(attempt["exports"]["swebench_prediction"], "prediction.json")

            prediction = json.loads((config_dir / "prediction.json").read_text())
            self.assertEqual(prediction["model_patch"], result["model_patch"])

            manifest = json.loads((output_dir / "manifest.json").read_text())
            task_entry = manifest["tasks"]["django__django-11099"]
            self.assertEqual(task_entry["selected_attempt"], "001")
            self.assertEqual(
                task_entry["attempts"]["001"]["attempt_path"],
                "configs/django__django-11099/attempt.json",
            )

    def test_no_patch_attempt_still_writes_empty_patch_file(self):
        instance = {
            "instance_id": "django__django-11099",
            "repo": "django/django",
            "version": "3.0",
            "base_commit": "abc123",
        }
        result = {
            "instance_id": "django__django-11099",
            "model_name_or_path": "gpt-5.4-mini",
            "model_patch": "",
            "status": "no_patch",
            "error": "No patch produced",
            "duration_s": 1.0,
        }

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            config_dir = output_dir / "configs" / instance["instance_id"]
            config_dir.mkdir(parents=True)
            (config_dir / "goal.txt").write_text("fix it")

            write_attempt_sidecars(
                instance=instance,
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider="docker",
            )

            self.assertEqual((config_dir / "patch.diff").read_text(), "")
            attempt = json.loads((config_dir / "attempt.json").read_text())
            self.assertEqual(attempt["status"], "no_patch")
            self.assertEqual(attempt["phases"]["solve"]["status"], "completed")
            self.assertEqual(attempt["phases"]["change"]["status"], "failed")

    def test_writes_runs_v1_bundle_and_manifest(self):
        instance = {
            "instance_id": "django__django-11099",
            "repo": "django/django",
            "version": "3.0",
            "base_commit": "abc123",
        }
        result = {
            "instance_id": "django__django-11099",
            "model_name_or_path": "gpt-5.4-mini",
            "model_patch": "diff --git a/a.py b/a.py\n",
            "status": "completed",
            "error": None,
            "duration_s": 12.3,
            "fabro_run_id": "01run",
            "fabro_run_dir": None,
        }

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            config_dir = output_dir / "configs" / instance["instance_id"]
            dump_dir = config_dir / "run_dump"
            dump_dir.mkdir(parents=True)
            (config_dir / "goal.txt").write_text("fix it")
            (config_dir / "workflow.fabro").write_text("digraph G {}")
            (config_dir / "workflow.toml").write_text("[workflow]\n")
            (dump_dir / "run.json").write_text("{}")
            (dump_dir / "events.jsonl").write_text("{}\n")
            (dump_dir / "trajectory.jsonl").write_text("{}\n")

            run_id = run_id_for_task(instance["instance_id"])
            artifacts = write_run_bundle(
                instance=instance,
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider="docker",
                run_id=run_id,
            )
            manifest = load_or_init_manifest(output_dir)
            update_manifest_for_run(
                manifest,
                task_id=instance["instance_id"],
                run_id=run_id,
                attempt_id=DEFAULT_ATTEMPT_ID,
                output_dir=output_dir,
            )
            write_manifest(output_dir, manifest)

            run_dir = output_dir / "runs" / run_id
            self.assertEqual(
                artifacts["run"],
                "runs/django__django-11099--001/run.json",
            )
            self.assertEqual((run_dir / "input" / "goal.md").read_text(), "fix it")
            self.assertEqual(
                (run_dir / "output" / "patch.diff").read_text(),
                result["model_patch"],
            )
            self.assertTrue((run_dir / "fabro" / "dump" / "run.json").exists())

            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual(run["layout"], "issue-to-pr-runs-v1")
            self.assertEqual(run["run_id"], run_id)
            self.assertEqual(run["phases"]["change"]["patch_path"], "output/patch.diff")
            self.assertEqual(run["fabro"]["events_path"], "fabro/dump/events.jsonl")

            task = json.loads((run_dir / "task.json").read_text())
            self.assertEqual(task["goal"]["text_path"], "input/goal.md")

            manifest = json.loads((output_dir / "manifest.json").read_text())
            self.assertEqual(
                manifest["tasks"]["django__django-11099"]["selected_run"],
                run_id,
            )
            self.assertEqual(
                manifest["runs"][run_id]["run_path"],
                "runs/django__django-11099--001/run.json",
            )

    def test_promotes_trajectory_and_verify_artifacts(self):
        instance = {
            "instance_id": "django__django-11099",
            "repo": "django/django",
            "version": "3.0",
            "base_commit": "abc123",
        }
        verify = {
            "schema_version": 1,
            "status": "passed",
            "mode": "diff-check",
            "patch_nonempty": True,
            "failure_reason": None,
        }

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            config_dir = output_dir / "configs" / instance["instance_id"]
            dump_dir = config_dir / "run_dump"
            dump_dir.mkdir(parents=True)
            trajectory = dump_dir / "trajectory.jsonl"
            trajectory.write_text('{"event":"agent.message"}\n')
            (dump_dir / "events.jsonl").write_text("{}\n")
            (config_dir / "goal.txt").write_text("fix it")

            result = {
                "instance_id": "django__django-11099",
                "model_name_or_path": "gpt-5.4-mini",
                "model_patch": "diff --git a/a.py b/a.py\n",
                "status": "completed",
                "error": None,
                "duration_s": 12.3,
                "fabro_run_id": "01run",
                "fabro_dump_dir": str(dump_dir),
                "events_path": str(dump_dir / "events.jsonl"),
                "trajectory_path": str(trajectory),
                "verify": verify,
            }

            artifacts = write_attempt_sidecars(
                instance=instance,
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider="docker",
            )

            self.assertEqual(
                artifacts["trajectory"],
                "configs/django__django-11099/trajectory.jsonl",
            )
            self.assertEqual(
                artifacts["verify"],
                "configs/django__django-11099/verify.json",
            )
            self.assertEqual(
                (config_dir / "trajectory.jsonl").read_text(),
                trajectory.read_text(),
            )
            self.assertEqual(json.loads((config_dir / "verify.json").read_text()), verify)

            attempt = json.loads((config_dir / "attempt.json").read_text())
            self.assertEqual(attempt["phases"]["verify"]["status"], "completed")
            self.assertEqual(attempt["phases"]["verify"]["artifact_path"], "verify.json")
            self.assertEqual(attempt["exports"]["trajectory"], "trajectory.jsonl")
            self.assertEqual(
                attempt["fabro"]["trajectory_path"],
                "run_dump/trajectory.jsonl",
            )

            run_id = run_id_for_task(instance["instance_id"])
            write_run_bundle(
                instance=instance,
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider="docker",
                run_id=run_id,
            )
            run_dir = output_dir / "runs" / run_id
            self.assertEqual(
                (run_dir / "output" / "trajectory.jsonl").read_text(),
                trajectory.read_text(),
            )
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual(run["exports"]["trajectory"], "output/trajectory.jsonl")
            self.assertEqual(run["phases"]["verify"]["status"], "completed")

    def test_verify_failed_with_patch_is_not_completed(self):
        instance = {
            "instance_id": "django__django-11099",
            "repo": "django/django",
            "version": "3.0",
            "base_commit": "abc123",
        }
        result = {
            "instance_id": "django__django-11099",
            "model_name_or_path": "gpt-5.4-mini",
            "model_patch": "diff --git a/a.py b/a.py\n",
            "status": "verify_failed",
            "error": "git diff --check failed",
            "duration_s": 12.3,
            "verify": {
                "schema_version": 1,
                "status": "failed",
                "mode": "diff-check",
                "patch_nonempty": True,
                "failure_reason": "git diff --check failed",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            config_dir = output_dir / "configs" / instance["instance_id"]
            config_dir.mkdir(parents=True)
            (config_dir / "goal.txt").write_text("fix it")

            write_attempt_sidecars(
                instance=instance,
                result=result,
                output_dir=output_dir,
                config_dir=config_dir,
                sandbox_provider="docker",
            )

            attempt = json.loads((config_dir / "attempt.json").read_text())
            self.assertEqual(attempt["status"], "verify_failed")
            self.assertEqual(attempt["phases"]["solve"]["status"], "failed")
            self.assertEqual(attempt["phases"]["verify"]["status"], "failed")
            self.assertEqual(attempt["phases"]["change"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
