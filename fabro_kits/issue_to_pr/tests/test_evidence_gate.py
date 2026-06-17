import json
import subprocess
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
            contract={"tests_added": ["tests/test_a.py"], "commands_run": [{"status": "passed"}]},
        )
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["judgment"]["hard_failures"], [])
        self.assertEqual(
            record["derived"]["claimed_test_paths_normalized"],
            ["tests/test_a.py"],
        )
        self.assertEqual(record["observed"]["commands_reported_passed_count"], 1)
    def test_verified_command_pass_count_requires_real_exit_zero(self):
        record = evaluate_evidence_gate(
            audit={"patch_nonempty": True, "changed_files": [], "test_files_changed": []},
            contract={"commands_run": [{"command": "python3 -m unittest fabro_kits.issue_to_pr.tests.test_artifacts.RunBundleArtifactsTest.test_candidate_patch_bytes_use_utf8_bytes", "status": "passed"}]},
            verify_commands=True,
        )
        self.assertEqual(record["observed"]["tests_passed_count"], 1)

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
                "tests_added": [{"path": "tests/test_a.py"}],
                "commands_run": [],
            }))
            script = build_embedded_gate_script(
                audit_path=str(audit),
                contract_path=str(contract),
                output_path=str(out),
            )
            proc = subprocess.run(
                script,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(out.read_text())["status"], "passed")
