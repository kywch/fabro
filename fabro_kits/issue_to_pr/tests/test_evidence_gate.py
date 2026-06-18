import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from fabro_kits.issue_to_pr.evidence_gate import build_embedded_gate_script, evaluate_evidence_gate
def evaluate_with_test_file(name, source):
    with tempfile.TemporaryDirectory() as tmp:
        tests = Path(tmp) / "tests"
        tests.mkdir()
        (tests / name).write_text(source)
        old = os.getcwd()
        try:
            os.chdir(tmp)
            return evaluate_evidence_gate(audit={"patch_nonempty": True, "changed_files": [f"tests/{name}"], "test_files_changed": [f"tests/{name}"]}, contract={"tests_added": [f"tests/{name}"]})
        finally:
            os.chdir(old)
class EvidenceGateTest(unittest.TestCase):
    def test_exact_path_claim_passes(self):
        record = evaluate_evidence_gate(
            audit={"patch_nonempty": True, "changed_files": ["tests/test_a.py"], "test_files_changed": ["tests/test_a.py"]},
            contract={"tests_added": ["tests/test_a.py"], "commands_run": [{"status": "passed"}]},
        )
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["judgment"]["hard_failures"], [])
        self.assertEqual(record["derived"]["claimed_test_paths_normalized"], ["tests/test_a.py"])
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
            audit={"patch_nonempty": True, "changed_files": ["pkg/code.py"], "test_files_changed": []},
            contract={"tests_added": ["tests/test_a.py"], "commands_run": []},
        )
        self.assertEqual(record["status"], "failed")
        self.assertIn("validation_claims_tests_but_diff_has_no_test_files", record["judgment"]["hard_failures"])
        self.assertEqual(record["judgment"]["route_decision"], "fixup")
    def test_duplicate_test_definition_hard_fails(self):
        record = evaluate_with_test_file("test_dup.py", "def test_same(): pass\ndef test_same(): pass\n")
        self.assertEqual(record["status"], "failed")
        self.assertIn("duplicate_test_definition: tests/test_dup.py:test_same", record["judgment"]["hard_failures"])
        record = evaluate_with_test_file("test_ok.py", "class A:\n def test_same(self): pass\nclass B:\n def test_same(self): pass\n")
        self.assertEqual(record["status"], "passed")
    def test_embedded_script_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit, contract, out = root / "audit.json", root / "contract.json", root / "gate.json"
            audit.write_text(json.dumps({"patch_nonempty": True, "changed_files": ["tests/test_a.py"], "test_files_changed": ["tests/test_a.py"]}))
            contract.write_text(json.dumps({"tests_added": [{"path": "tests/test_a.py"}], "commands_run": []}))
            script = build_embedded_gate_script(audit_path=str(audit), contract_path=str(contract), output_path=str(out))
            proc = subprocess.run(script, shell=True, executable="/bin/bash", capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(out.read_text())["status"], "passed")
