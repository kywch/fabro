import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fabro_kits.issue_to_pr.evidence_gate import (
    build_embedded_gate_script,
    evaluate_evidence_gate,
)


class EvidenceGateTest(unittest.TestCase):
    def test_exact_path_claim_passes(self):
        record = evaluate_evidence_gate(
            audit={
                "patch_nonempty": True,
                "changed_files": ["tests/test_a.py"],
                "test_files_changed": ["tests/test_a.py"],
            },
            contract={"tests_added": ["tests/test_a.py"], "commands_run": []},
        )

        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["judgment"]["hard_failures"], [])
        self.assertEqual(
            record["derived"]["claimed_test_paths_normalized"],
            ["tests/test_a.py"],
        )

    def test_path_colon_prose_claim_passes_when_path_changed(self):
        record = evaluate_evidence_gate(
            audit={
                "patch_nonempty": True,
                "changed_files": ["tests/auth_tests/test_validators.py"],
                "test_files_changed": ["tests/auth_tests/test_validators.py"],
            },
            contract={
                "tests_added": [
                    "tests/auth_tests/test_validators.py: added newline rejection assertions"
                ],
                "commands_run": [],
            },
        )

        self.assertEqual(record["status"], "passed")
        self.assertEqual(
            record["claims"]["claimed_tests_raw"],
            ["tests/auth_tests/test_validators.py: added newline rejection assertions"],
        )
        self.assertEqual(
            record["derived"]["claimed_test_paths_normalized"],
            ["tests/auth_tests/test_validators.py"],
        )
        self.assertEqual(record["judgment"]["route_decision"], "review")

    def test_dict_path_claim_passes(self):
        record = evaluate_evidence_gate(
            audit={
                "patch_nonempty": True,
                "changed_files": ["pkg/tests.py"],
                "test_files_changed": ["pkg/tests.py"],
            },
            contract={"tests_added": [{"path": "pkg/tests.py"}], "commands_run": []},
        )

        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["derived"]["claimed_test_paths_normalized"], ["pkg/tests.py"])

    def test_claimed_tests_without_changed_test_file_hard_fails(self):
        record = evaluate_evidence_gate(
            audit={
                "patch_nonempty": True,
                "changed_files": ["pkg/code.py"],
                "test_files_changed": [],
            },
            contract={"tests_added": ["tests/test_a.py"], "commands_run": []},
        )

        self.assertEqual(record["status"], "failed")
        self.assertIn(
            "validation_claims_tests_but_diff_has_no_test_files",
            record["judgment"]["hard_failures"],
        )
        self.assertEqual(record["judgment"]["route_decision"], "fixup")

    def test_changed_test_file_without_claim_warns(self):
        record = evaluate_evidence_gate(
            audit={
                "patch_nonempty": True,
                "changed_files": ["tests/test_a.py"],
                "test_files_changed": ["tests/test_a.py"],
            },
            contract={"tests_added": [], "commands_run": []},
        )

        self.assertEqual(record["status"], "passed")
        self.assertIn(
            "diff_has_test_files_but_validation_contract_does_not_claim_tests",
            record["judgment"]["warnings"],
        )

    def test_unparseable_claim_with_changed_test_file_warns(self):
        record = evaluate_evidence_gate(
            audit={
                "patch_nonempty": True,
                "changed_files": ["tests/test_a.py"],
                "test_files_changed": ["tests/test_a.py"],
            },
            contract={"tests_added": ["added a regression"], "commands_run": []},
        )

        self.assertEqual(record["status"], "passed")
        self.assertIn(
            "unparseable_test_claims_with_changed_test_files: added a regression",
            record["judgment"]["warnings"],
        )

    def test_cli_writes_gate_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "audit.json"
            contract = root / "contract.json"
            out = root / "gate.json"
            audit.write_text(json.dumps({
                "patch_nonempty": True,
                "changed_files": ["tests/test_a.py"],
                "test_files_changed": ["tests/test_a.py"],
            }))
            contract.write_text(json.dumps({
                "tests_added": ["tests/test_a.py: regression"],
                "commands_run": [],
            }))

            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "fabro_kits.issue_to_pr.cli",
                    "evidence-gate",
                    "--audit",
                    str(audit),
                    "--contract",
                    str(contract),
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            record = json.loads(out.read_text())
            self.assertEqual(record["status"], "passed")
            self.assertEqual(
                record["derived"]["claimed_test_paths_normalized"],
                ["tests/test_a.py"],
            )

    def test_embedded_script_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "audit.json"
            contract = root / "contract.json"
            out = root / "gate.json"
            audit.write_text(json.dumps({
                "patch_nonempty": True,
                "changed_files": ["tests/test_a.py"],
                "test_files_changed": ["tests/test_a.py"],
            }))
            contract.write_text(json.dumps({
                "tests_added": ["tests/test_a.py: regression"],
                "commands_run": [],
            }))
            script = build_embedded_gate_script(
                audit_path=str(audit),
                contract_path=str(contract),
                output_path=str(out),
            )

            self.assertNotIn("__future__", script)
            self.assertNotIn("list[", script)
            self.assertNotIn("dict[", script)
            proc = subprocess.run(
                script,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            record = json.loads(out.read_text())
            self.assertEqual(record["status"], "passed")


if __name__ == "__main__":
    unittest.main()
