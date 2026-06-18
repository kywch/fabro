import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fabro_kits.issue_to_pr.run_attempt import (
    dump_run,
    fetch_run_diff,
    parse_json_object,
    parse_run_id_json,
    write_events_jsonl,
    write_trajectory_from_events,
)


class RunAttemptFabroJsonTest(unittest.TestCase):
    def test_parse_run_id_json(self):
        self.assertEqual(parse_run_id_json('{"run_id":"01ABC"}\n'), "01ABC")

    def test_parse_json_object_falls_back_to_last_object(self):
        self.assertEqual(
            parse_json_object('noise\n{"status":"failed"}\n'),
            {"status": "failed"},
        )

    @patch("fabro_kits.issue_to_pr.run_attempt.subprocess.run")
    def test_dump_run_uses_json_output_dir(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "actual-dump"
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps(
                {"run_id": "01ABC", "output_dir": str(output_dir), "file_count": 3}
            )

            self.assertEqual(dump_run("fabro", "01ABC", Path(tmp)), output_dir)
            self.assertEqual(
                run.call_args.args[0],
                ["fabro", "--json", "dump", "--output", str(Path(tmp) / "run_dump"), "01ABC"],
            )

    @patch("fabro_kits.issue_to_pr.run_attempt.subprocess.run")
    def test_fetch_run_diff_returns_diff_from_json(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps({"run_id": "01ABC", "node": None, "diff": "diff --git\n"})

        self.assertEqual(fetch_run_diff("fabro", "01ABC"), "diff --git\n")
        self.assertEqual(run.call_args.args[0], ["fabro", "--json", "diff", "01ABC"])

    @patch("fabro_kits.issue_to_pr.run_attempt.subprocess.run")
    def test_write_events_jsonl_uses_run_events_json(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"event":"agent.input","properties":{"text":"fix it"}}\n'

            events_path = write_events_jsonl("fabro", "01ABC", Path(tmp))

            self.assertEqual(events_path, Path(tmp) / "events.jsonl")
            self.assertEqual(events_path.read_text(), run.return_value.stdout)
            self.assertEqual(run.call_args.args[0], ["fabro", "--json", "events", "01ABC"])

    def test_write_trajectory_from_events_accepts_event_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            events_path.write_text(
                json.dumps(
                    {
                        "seq": 1,
                        "event": "agent.input",
                        "properties": {"text": "Fix this issue"},
                    }
                )
                + "\n"
            )

            trajectory_path = write_trajectory_from_events(events_path)

            self.assertEqual(trajectory_path, Path(tmp) / "trajectory.jsonl")
            entry = json.loads(trajectory_path.read_text())
            self.assertEqual(entry["role"], "user")
            self.assertEqual(entry["text"], "Fix this issue")


if __name__ == "__main__":
    unittest.main()
