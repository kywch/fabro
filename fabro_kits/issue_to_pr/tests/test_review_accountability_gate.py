import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from fabro_kits.issue_to_pr.review_accountability_gate import (
    build_embedded_accountability_gate_script,
    evaluate_review_accountability,
)


class ReviewAccountabilityGateTest(unittest.TestCase):
    def test_open_minor_row_fails_export(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A2",
                        "category": "tests",
                        "severity": "minor",
                        "required_files": ["tests/test_label.py"],
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A2",
                        "state": "open",
                        "category": "tests",
                        "severity": "minor",
                        "reason": "Still unresolved.",
                    }
                ]
            ),
            test_gate=_test_gate(),
            materialization=_materialization(["A2"], ["A2"]),
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["route_decision"], "fixup")
        self.assertIn("open_review_rows", report["process_failures"])
        self.assertEqual(report["blocking_rows"][0]["id"], "A2")

    def test_empty_closure_checks_fail(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {"id": "A1", "category": "code", "severity": "minor"},
                    {"id": "A2", "category": "tests", "severity": "minor"},
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "code",
                        "severity": "minor",
                        "evidence": ["diff hunk"],
                        "closure_check": "",
                    },
                    {
                        "id": "A2",
                        "state": "closed_by_evidence",
                        "category": "tests",
                        "severity": "minor",
                        "evidence": ["test hunk"],
                        "closure_check": "",
                    },
                ]
            ),
            test_gate=_test_gate(),
            materialization=_materialization(["A1", "A2"], ["A1", "A2"]),
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn("invalid_closure_checks", report["process_failures"])
        self.assertEqual(
            [row["id"] for row in report["closure_check_failures"]],
            ["A1", "A2"],
        )

    def test_missing_required_file_fails_severe_closure(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "code",
                        "severity": "major",
                        "required_files": ["src/required.py"],
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "code",
                        "severity": "major",
                        "evidence": ["src/other.py"],
                        "closure_check": "Checked diff.",
                    }
                ]
            ),
            test_gate=_test_gate(changed_files=["src/other.py"]),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertIn("invalid_closure_checks", report["process_failures"])
        self.assertEqual(
            report["closure_check_failures"][0]["missing_required_files"],
            ["src/required.py"],
        )

    def test_runtime_closure_requires_machine_observed_test_execution(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "tests",
                        "severity": "major",
                        "closure_requires": "runtime_tests",
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "tests",
                        "severity": "major",
                        "evidence": ["commands_reported_passed_count=1"],
                        "closure_check": "Reported test command passed.",
                    }
                ]
            ),
            test_gate=_test_gate(tests_passed_count=0, commands_reported_passed_count=1),
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertIn("tests_not_executed_successfully", report["process_failures"])
        self.assertIn("invalid_closure_checks", report["process_failures"])
        self.assertEqual(
            report["closure_check_failures"][0]["missing_closure_requirement"],
            "runtime_tests",
        )

    def test_settings_ref_diff_is_injected(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(),
            materialization=_materialization([], []),
            settings_ref_diff=(
                "``OPTIONS``\n"
                "Extra parameters to pass to the cache backend\n"
                "+Default: ``0o644``\n"
            ),
        )

        self.assertIn("patch_malformed_or_scope_drift", report["process_failures"])
        self.assertEqual(report["malformed_artifacts"][0]["error"], "docs_settings_corruption")

    def test_failed_materialization_fails_even_without_structured_errors(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(dispositions=[]),
            test_gate=_test_gate(),
            materialization={
                "schema_version": 1,
                "stage": "review_materialization",
                "status": "failed",
                "errors": [],
            },
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn("review_artifact_missing_or_malformed", report["process_failures"])
        self.assertEqual(
            report["malformed_artifacts"][0]["error"],
            "materialization_status_not_passed",
        )

    def test_orphan_moderator_disposition_fails(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(rows=[]),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A99",
                        "state": "rejected",
                        "category": "code",
                        "severity": "minor",
                        "closure_check": "Unsupported row.",
                    }
                ]
            ),
            test_gate=_test_gate(),
            materialization=_materialization([], ["A99"]),
        )

        self.assertIn("orphan_moderator_dispositions", report["process_failures"])
        self.assertEqual(report["orphan_dispositions"][0]["id"], "A99")
        self.assertEqual(report["fixup_required_rows"][0]["id"], "A99")

    def test_duplicate_adversarial_row_ids_fail(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {"id": "A1", "category": "code", "severity": "minor"},
                    {"id": "A1", "category": "tests", "severity": "minor"},
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "rejected",
                        "category": "code",
                        "severity": "minor",
                        "closure_check": "Duplicate row rejected.",
                    }
                ]
            ),
            test_gate=_test_gate(),
            materialization=_materialization(["A1", "A1"], ["A1"]),
        )

        self.assertIn("duplicate_adversarial_rows", report["process_failures"])
        self.assertEqual(report["duplicate_adversarial_row_ids"], ["A1"])

    def test_boolean_test_count_does_not_satisfy_runtime_proof(self):
        report = evaluate_review_accountability(
            adversarial=_adversarial(
                rows=[
                    {
                        "id": "A1",
                        "category": "tests",
                        "severity": "major",
                        "closure_requires": "runtime_tests",
                    }
                ]
            ),
            moderator=_moderator(
                dispositions=[
                    {
                        "id": "A1",
                        "state": "closed_by_evidence",
                        "category": "tests",
                        "severity": "major",
                        "evidence": ["tests_passed_count=true"],
                        "closure_check": "Boolean count claimed.",
                    }
                ]
            ),
            test_gate={
                "schema_version": 1,
                "status": "passed",
                "changed_files": ["tests/test_fix.py"],
                "observed": {"tests_passed_count": True},
            },
            materialization=_materialization(["A1"], ["A1"]),
        )

        self.assertIn("tests_not_executed_successfully", report["process_failures"])
        self.assertIn("invalid_closure_checks", report["process_failures"])

    def test_embedded_script_matches_pure_failure_report(self):
        adversarial = _adversarial(
            rows=[{"id": "A2", "category": "tests", "severity": "minor"}]
        )
        moderator = _moderator(
            dispositions=[
                {"id": "A2", "state": "open", "category": "tests", "severity": "minor"}
            ]
        )
        test_gate = _test_gate()
        materialization = _materialization(["A2"], ["A2"])
        expected = evaluate_review_accountability(
            adversarial=deepcopy(adversarial),
            moderator=deepcopy(moderator),
            test_gate=deepcopy(test_gate),
            materialization=deepcopy(materialization),
        )
        actual = _run_embedded(adversarial, moderator, test_gate, materialization)
        self.assertEqual(actual, expected)

    def test_embedded_script_matches_pure_pass_report(self):
        adversarial = _adversarial(
            rows=[{"id": "A1", "category": "code", "severity": "major"}]
        )
        moderator = _moderator(
            dispositions=[
                {
                    "id": "A1",
                    "state": "closed_by_evidence",
                    "category": "code",
                    "severity": "major",
                    "evidence": ["src/fix.py"],
                    "closure_check": "Verified src/fix.py.",
                }
            ]
        )
        test_gate = _test_gate(changed_files=["src/fix.py"], tests_passed_count=1)
        materialization = _materialization(["A1"], ["A1"])
        expected = evaluate_review_accountability(
            adversarial=deepcopy(adversarial),
            moderator=deepcopy(moderator),
            test_gate=deepcopy(test_gate),
            materialization=deepcopy(materialization),
        )
        actual = _run_embedded(adversarial, moderator, test_gate, materialization)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["route_decision"], "export")


def _run_embedded(adversarial, moderator, test_gate, materialization):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = {
            "adversarial": root / "adversarial.json",
            "moderator": root / "moderator.json",
            "test_gate": root / "test-gate.json",
            "materialization": root / "materialization.json",
            "output": root / "gate.json",
        }
        paths["adversarial"].write_text(json.dumps(adversarial))
        paths["moderator"].write_text(json.dumps(moderator))
        paths["test_gate"].write_text(json.dumps(test_gate))
        paths["materialization"].write_text(json.dumps(materialization))
        script = build_embedded_accountability_gate_script(
            adversarial_path=str(paths["adversarial"]),
            moderator_path=str(paths["moderator"]),
            test_gate_path=str(paths["test_gate"]),
            materialization_path=str(paths["materialization"]),
            output_path=str(paths["output"]),
        )
        proc = subprocess.run(
            script,
            shell=True,
            executable="/bin/bash",
            cwd=root,
            capture_output=True,
            text=True,
        )
        if not paths["output"].exists():
            raise AssertionError(proc.stdout + proc.stderr)
        self_status = json.loads(paths["output"].read_text())["status"]
        expected_returncode = 1 if self_status == "failed" else 0
        if proc.returncode != expected_returncode:
            raise AssertionError(proc.stdout + proc.stderr)
        return json.loads(paths["output"].read_text())


def _adversarial(rows):
    return {
        "schema_version": 1,
        "stage": "adversarial_review",
        "summary": "review",
        "rows": rows,
        "overall_risk": "medium",
    }


def _moderator(dispositions):
    return {
        "schema_version": 1,
        "stage": "moderator_filter",
        "dispositions": dispositions,
        "readiness_tier": "ready_verified",
        "next_agent_guidance": "Proceed.",
    }


def _test_gate(
    *,
    changed_files=None,
    tests_passed_count=1,
    commands_reported_passed_count=0,
):
    changed_files = changed_files or ["src/fix.py", "tests/test_fix.py"]
    return {
        "schema_version": 1,
        "status": "passed",
        "changed_files": changed_files,
        "observed": {
            "changed_files": changed_files,
            "tests_passed_count": tests_passed_count,
            "commands_reported_passed_count": commands_reported_passed_count,
        },
    }


def _materialization(row_ids, disposition_ids):
    return {
        "schema_version": 1,
        "stage": "review_materialization",
        "status": "passed",
        "errors": [],
        "adversarial_row_ids": row_ids,
        "moderator_disposition_ids": disposition_ids,
    }


if __name__ == "__main__":
    unittest.main()
