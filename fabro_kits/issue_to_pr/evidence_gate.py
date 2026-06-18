"""Mechanical evidence-integrity checks for issue-to-PR attempts."""

from __future__ import annotations

import json
import inspect
import ast
import re
import subprocess
from pathlib import Path

SCHEMA_VERSION = 1
CANNOT_PROVE = ["patch_semantically_fixes_issue", "changed_tests_are_meaningful_regressions", "no_new_test_is_acceptable", "hidden_or_official_tests_would_pass"]

def evaluate_evidence_gate(
    audit,
    contract,
    verify_commands=False,
):
    """Compare agent validation claims with machine-observed diff evidence."""
    audit = audit if isinstance(audit, dict) else {}
    contract = contract if isinstance(contract, dict) else {}
    hard_failures = []
    warnings = []
    if audit.get("_json_error"):
        hard_failures.append(f"audit_json_invalid: {audit['_json_error']}")
    if contract.get("_json_error"):
        warnings.append(f"validation_contract_json_invalid: {contract['_json_error']}")
    changed_files = normalize_path_list(audit.get("changed_files"))
    test_files_changed = normalize_path_list(audit.get("test_files_changed"))
    claimed_tests_raw = normalize_claims_raw(contract.get("tests_added"))
    normalized_claims = [claim for claim in (normalize_claim(raw) for raw in claimed_tests_raw) if claim is not None]
    normalized_paths = [claim["path"] for claim in normalized_claims if isinstance(claim.get("path"), str)]
    commands_run = contract.get("commands_run") or []
    if not isinstance(commands_run, list):
        warnings.append("commands_run_not_list")
        commands_run = []
    commands_reported_passed_count = commands_status_count(commands_run, {"passed", "pass", "success", "succeeded", "ok"})
    verified_commands = verify_reported_passes(commands_run) if verify_commands else []
    tests_passed_count = sum(1 for command in verified_commands if command["exit_code"] == 0 and is_test_command(command["command"]))
    if audit and audit.get("patch_nonempty") is False:
        hard_failures.append("audit_reports_empty_patch")
    if normalized_paths and not test_files_changed:
        hard_failures.append("validation_claims_tests_but_diff_has_no_test_files")
    elif normalized_paths:
        missing = [path for path in normalized_paths if not path_matches_claim(test_files_changed, path)] + [path for path in test_files_changed if not path_matches_claim(normalized_paths, path)]
        if missing:
            hard_failures.append("validation_claims_tests_not_in_diff: " + ", ".join(missing))
    unparseable_claims = [
        claim
        for claim in claimed_tests_raw
        if normalize_claim(claim) is None and str(claim).strip()
    ]
    if unparseable_claims and test_files_changed:
        warnings.append(
            "unparseable_test_claims_with_changed_test_files: "
            + ", ".join(str(claim) for claim in unparseable_claims[:3])
        )
    elif unparseable_claims and not test_files_changed:
        hard_failures.append(
            "unparseable_test_claims_without_changed_test_files: "
            + ", ".join(str(claim) for claim in unparseable_claims[:3])
        )
    if test_files_changed and not claimed_tests_raw:
        warnings.append("diff_has_test_files_but_validation_contract_does_not_claim_tests")
    if not test_files_changed and not contract.get("no_test_justification"):
        warnings.append("no_test_files_and_no_test_justification")
    missing_status = commands_missing_status(commands_run)
    if missing_status:
        warnings.append("commands_missing_status: " + ", ".join(missing_status[:3]))
    hard_failures.extend(duplicate_test_definitions(test_files_changed))
    status = "failed" if hard_failures else "passed"
    route_decision = "fixup" if hard_failures else "review"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "mode": "test-evidence",
        "observed": {
            "patch_nonempty": audit.get("patch_nonempty"),
            "changed_files": changed_files,
            "test_files_changed": test_files_changed,
            "commands_claimed_count": len(commands_run),
            "commands_reported_passed_count": commands_reported_passed_count,
            "tests_passed_count": tests_passed_count,
            "verified_commands": verified_commands,
        },
        "claims": {
            "claimed_tests_raw": claimed_tests_raw,
        },
        "derived": {
            "claimed_test_paths_normalized": normalized_paths,
            "claimed_tests_normalized": normalized_claims,
        },
        "judgment": {
            "hard_failures": hard_failures,
            "warnings": warnings,
            "route_decision": route_decision,
            "fixup_guidance": fixup_guidance(hard_failures, warnings),
            "cannot_prove": CANNOT_PROVE,
        },
        "failure_reason": "; ".join(hard_failures) if hard_failures else None,
        # Compatibility fields for older artifact consumers.
        "patch_nonempty": audit.get("patch_nonempty"),
        "changed_files": changed_files,
        "test_files_changed": test_files_changed,
        "claimed_tests": normalized_paths,
        "contradictions": hard_failures,
        "warnings": warnings,
    }

def normalize_path_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    paths = []
    for item in value:
        if isinstance(item, str) and item.strip():
            paths.append(item.strip())
        elif isinstance(item, dict):
            for key in ("path", "file", "test_file", "name"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    paths.append(candidate.strip())
                    break
    return paths

def normalize_claims_raw(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]

def normalize_claim(raw):
    if isinstance(raw, dict):
        for key in ("path", "file", "test_file", "name"):
            candidate = raw.get(key)
            if isinstance(candidate, str):
                parsed = parse_path_prefix(candidate)
                if parsed:
                    return {"path": parsed[0], "description": parsed[1], "source": key}
        return None
    if isinstance(raw, str):
        parsed = parse_path_prefix(raw)
        if parsed:
            return {"path": parsed[0], "description": parsed[1], "source": "string"}
    return None

def parse_path_prefix(text):
    stripped = text.strip()
    if not stripped:
        return None
    candidate = stripped.split(":", 1)[0].strip()
    if not looks_like_path(candidate):
        candidate = stripped
        if not looks_like_path(candidate):
            return None
    description = stripped[len(candidate):].lstrip(":").strip()
    return candidate, description

def looks_like_path(value):
    if not value or any(ch.isspace() for ch in value):
        return False
    return bool(re.search(r"(^|/)(tests?|test_[^/]+|[^/]+_test)\b", value)) or value.endswith(
        ("/tests.py", "_test.py", ".py")
    )

def path_matches_claim(changed_test_files, claimed_test):
    claimed = claimed_test.strip()
    if not claimed:
        return False
    return any(
        changed == claimed
        or changed.endswith("/" + claimed)
        or claimed.endswith("/" + changed)
        for changed in changed_test_files
    )

def duplicate_test_definitions(paths):
    failures = []
    for path in paths:
        file_path = Path(path)
        if not path.endswith(".py") or not file_path.exists():
            continue
        try:
            tree = ast.parse(file_path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        for scope in [tree] + [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]:
            seen = set()
            for node in getattr(scope, "body", []):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
                    continue
                if node.name in seen:
                    failures.append(f"duplicate_test_definition: {path}:{node.name}")
                seen.add(node.name)
    return failures

def commands_missing_status(commands_run):
    missing = []
    for command in commands_run:
        if isinstance(command, dict):
            status = command.get("status")
            label = command.get("cmd") or command.get("command") or "<unknown>"
            if not isinstance(status, str) or not status.strip():
                missing.append(str(label))
        elif isinstance(command, str):
            missing.append(command)
    return missing

def commands_status_count(commands_run, statuses):
    return sum(1 for command in commands_run if isinstance(command, dict) and str(command.get("status", "")).strip().lower() in statuses)

def is_test_command(command):
    return any(token in command for token in ("tests/runtests.py", "pytest", "unittest", "cargo test", "bun test"))

def verify_reported_passes(commands_run):
    safe_tokens = ("tests/runtests.py", "pytest", "unittest", "git diff --check", "cargo test", "bun test")
    verified = []
    for item in commands_run:
        if len(verified) >= 3 or not isinstance(item, dict):
            continue
        command = str(item.get("command") or item.get("cmd") or "").strip()
        if str(item.get("status", "")).strip().lower() not in {"passed", "pass", "success", "succeeded", "ok"} or not command or not any(token in command for token in safe_tokens):
            continue
        try:
            proc = subprocess.run(command, shell=True, executable="/bin/bash", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=180)
            verified.append({"command": command, "exit_code": proc.returncode, "output_tail": proc.stdout[-2000:]})
        except Exception as exc:
            verified.append({"command": command, "exit_code": None, "error": str(exc)})
    return verified

def fixup_guidance(hard_failures, warnings):
    if hard_failures:
        return (
            "Resolve evidence contradictions against machine-observed diff data. "
            "Update the patch or validation contract so claimed tests match changed "
            "test files; do not add paperwork-only claims."
        )
    if warnings:
        return (
            "Review metadata warnings, but do not treat them as semantic proof or "
            "semantic failure without patch review."
        )
    return None

def build_embedded_gate_script(
    *,
    audit_path: str,
    contract_path: str,
    output_path: str,
) -> str:
    """Return a self-contained Python script for sandbox workflow execution."""
    return f"""python3 - <<'PY'
import ast
import json
import re
import subprocess
from pathlib import Path

CANNOT_PROVE = {CANNOT_PROVE!r}
SCHEMA_VERSION = {SCHEMA_VERSION}

{_embedded_gate_functions_source()}


def read_json(path):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {{"_json_error": str(exc)}}
    return value if isinstance(value, dict) else {{"_json_error": "expected object"}}


def write_gate_record(record, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\\n")

audit = read_json(Path({audit_path!r})) or {{}}
contract = read_json(Path({contract_path!r})) or {{}}
record = evaluate_evidence_gate(audit, contract, verify_commands=True)
write_gate_record(record, Path({output_path!r}))
print(json.dumps(record, sort_keys=True))
raise SystemExit(1 if record["judgment"]["hard_failures"] else 0)
PY
"""


def _embedded_gate_functions_source():
    functions = (
        normalize_path_list,
        normalize_claims_raw,
        normalize_claim,
        parse_path_prefix,
        looks_like_path,
        path_matches_claim,
        duplicate_test_definitions,
        commands_missing_status,
        commands_status_count,
        is_test_command,
        verify_reported_passes,
        fixup_guidance,
        evaluate_evidence_gate,
    )
    return "\n\n".join(inspect.getsource(function) for function in functions)
