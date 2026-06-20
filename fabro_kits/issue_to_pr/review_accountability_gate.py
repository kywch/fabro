"""Review-accountability gate for issue-to-PR workflows."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any


MAJOR = {"blocker", "critical", "major"}
STATES = {"open", "closed_by_evidence", "rejected", "downgraded"}


def evaluate_review_accountability(
    *,
    adversarial: dict[str, Any] | None,
    moderator: dict[str, Any] | None,
    test_gate: dict[str, Any] | None,
    materialization: dict[str, Any] | None,
    adversarial_error: dict[str, Any] | None = None,
    moderator_error: dict[str, Any] | None = None,
    test_gate_error: dict[str, Any] | None = None,
    materialization_error: dict[str, Any] | None = None,
    patch_diff: str = "",
    settings_ref_diff: str = "",
) -> dict[str, Any]:
    """Evaluate whether adversarial review rows are export-accounted for."""
    malformed = []
    for artifact, err in (
        ("adversarial_review", adversarial_error),
        ("moderator_filter", moderator_error),
        ("test_evidence_gate", test_gate_error),
        ("review_materialization", materialization_error),
    ):
        if err:
            item = dict(err)
            item.setdefault("artifact", artifact)
            malformed.append(item)
    if isinstance(materialization, dict) and materialization.get("status") != "passed":
        malformed.extend(as_list(materialization.get("errors")))
        malformed.append(
            {
                "artifact": "review_materialization",
                "error": "materialization_status_not_passed",
                "status": materialization.get("status"),
            }
        )
    if not tests_executed_successfully(test_gate):
        item = {
            "artifact": "test_evidence_gate",
            "error": "tests_not_executed_successfully",
            "reason": "Run the changed or claimed tests and record a passing command in validation.",
        }
        judgment = test_gate.get("judgment") if isinstance(test_gate, dict) else {}
        if isinstance(judgment, dict):
            hard_failures = as_list(judgment.get("hard_failures"))
            warnings = as_list(judgment.get("warnings"))
            if hard_failures:
                item["hard_failures"] = hard_failures
                item["reason"] = "; ".join(str(failure) for failure in hard_failures)
            if warnings:
                item["warnings"] = warnings
        malformed.append(item)

    rows = as_list(adversarial.get("rows") if isinstance(adversarial, dict) else None)
    dispositions = (
        []
        if adversarial_error
        else as_list(moderator.get("dispositions") if isinstance(moderator, dict) else None)
    )
    changed_files = {
        clean_path(path)
        for path in as_list(test_gate.get("changed_files") if isinstance(test_gate, dict) else None)
    }
    if (
        "``OPTIONS``" in settings_ref_diff
        and "+Default: ``0o644``" in settings_ref_diff
        and "Extra parameters to pass to the cache backend" in settings_ref_diff
    ) or settings_ref_diff.count(
        "The numeric mode (i.e. ``0o644``) to set newly uploaded files to."
    ) > 1:
        malformed.append(
            {
                "artifact": "patch",
                "path": "docs/ref/settings.txt",
                "error": "docs_settings_corruption",
                "check": "cache OPTIONS default changed or FILE_UPLOAD_PERMISSIONS text duplicated",
                "offending_diff": settings_ref_diff[:1200],
            }
        )
    negative_coverage_removals = detect_negative_coverage_removal(patch_diff)
    for removal in negative_coverage_removals:
        malformed.append(
            {
                "artifact": "patch",
                "error": "negative_coverage_removed",
                "path": removal["path"],
                "removed_line": removal["removed_line"],
                "check": "negative test coverage was removed without replacement",
            }
        )
    if isinstance(adversarial, dict) and not isinstance(adversarial.get("rows", []), list):
        malformed.append(
            {
                "artifact": "adversarial_review",
                "field": "rows",
                "error": "expected_list",
            }
        )
    if isinstance(moderator, dict) and not isinstance(moderator.get("dispositions", []), list):
        malformed.append(
            {
                "artifact": "moderator_filter",
                "field": "dispositions",
                "error": "expected_list",
            }
        )

    row_by_id = {}
    duplicate_adversarial_row_ids = []
    for row in rows:
        rid = row_id(row)
        if rid and rid not in row_by_id:
            row_by_id[rid] = row
        elif rid:
            duplicate_adversarial_row_ids.append(rid)
            malformed.append(
                {
                    "artifact": "adversarial_review",
                    "field": "rows.id",
                    "error": "duplicate_id",
                    "id": rid,
                }
            )
        else:
            malformed.append(
                {
                    "artifact": "adversarial_review",
                    "field": "rows.id",
                    "error": "missing_id",
                }
            )

    seen = set()
    duplicate_disposition_ids = []
    orphan_dispositions = []
    open_rows = []
    closed_rows = []
    downgraded_rows = []
    rejected_rows = []
    invalid_dispositions = []
    closure_check_failures = []

    for raw_disposition in dispositions:
        if not isinstance(raw_disposition, dict):
            malformed.append(
                {
                    "artifact": "moderator_filter",
                    "field": "dispositions",
                    "error": "disposition_not_object",
                }
            )
            continue
        disposition = dict(raw_disposition)
        did = row_id(disposition)
        state = str(disposition.get("state") or disposition.get("disposition") or "").lower()
        if not did:
            invalid_dispositions.append(disposition)
            malformed.append(
                {
                    "artifact": "moderator_filter",
                    "field": "dispositions.id",
                    "error": "missing_id",
                }
            )
            continue
        if did in seen:
            duplicate_disposition_ids.append(did)
        seen.add(did)
        if did not in row_by_id:
            orphan_dispositions.append(disposition)
        if state not in STATES:
            invalid_dispositions.append(disposition)
            continue
        disposition["row_closure_requires"] = row_by_id.get(did, {}).get("closure_requires")
        forbidden_changed = [
            path
            for path in (
                clean_path(path) for path in as_list(row_by_id.get(did, {}).get("forbidden_files"))
            )
            if path in changed_files
        ]
        if state in {"closed_by_evidence", "downgraded", "rejected"} and forbidden_changed:
            disposition["forbidden_files_changed"] = forbidden_changed
            add_unique(closure_check_failures, disposition)
        severe = (
            str(row_by_id.get(did, {}).get("severity", disposition.get("severity", ""))).lower()
            in MAJOR
        )
        closure_check = str(disposition.get("closure_check", "")).strip()
        if state in {"closed_by_evidence", "downgraded", "rejected"} and not closure_check:
            add_unique(closure_check_failures, disposition)
        if (
            severe
            and did in row_by_id
            and str(disposition.get("category", "")).lower()
            != str(row_by_id[did].get("category", "")).lower()
        ):
            disposition["category_mismatch"] = {
                "row": row_by_id[did].get("category"),
                "disposition": disposition.get("category"),
            }
        missing_required = [
            path
            for path in (
                clean_path(path) for path in as_list(row_by_id.get(did, {}).get("required_files"))
            )
            if path not in changed_files and not is_issue_artifact_path(path)
        ]
        if state in {"closed_by_evidence", "downgraded", "rejected"} and missing_required:
            disposition["missing_required_files"] = missing_required
            add_unique(closure_check_failures, disposition)
        if (
            severe
            and state in {"closed_by_evidence", "downgraded", "rejected"}
            and str(row_by_id.get(did, {}).get("closure_requires", "")).lower()
            == "runtime_tests"
            and not tests_executed_successfully(test_gate)
        ):
            disposition["missing_closure_requirement"] = "runtime_tests"
            add_unique(closure_check_failures, disposition)
        if state == "open":
            open_rows.append(disposition)
        elif state == "closed_by_evidence":
            if not has_evidence(disposition):
                invalid_dispositions.append(disposition)
            score_closure(
                disposition,
                row_by_id.get(did, {}),
                changed_files=changed_files,
                test_gate=test_gate,
                patch_diff=patch_diff,
            )
            if closure_score_too_low(disposition, severe):
                add_unique(closure_check_failures, disposition)
            closed_rows.append(disposition)
        elif state == "downgraded":
            if not has_evidence(disposition):
                invalid_dispositions.append(disposition)
            score_closure(
                disposition,
                row_by_id.get(did, {}),
                changed_files=changed_files,
                test_gate=test_gate,
                patch_diff=patch_diff,
            )
            if closure_score_too_low(disposition, severe):
                add_unique(closure_check_failures, disposition)
            downgraded_rows.append(disposition)
        elif state == "rejected":
            score_closure(
                disposition,
                row_by_id.get(did, {}),
                changed_files=changed_files,
                test_gate=test_gate,
                patch_diff=patch_diff,
            )
            if closure_score_too_low(disposition, severe):
                add_unique(closure_check_failures, disposition)
            rejected_rows.append(disposition)

    unaccounted_rows = [row for rid, row in row_by_id.items() if rid not in seen]
    unaccounted_major_rows = [
        row for row in unaccounted_rows if str(row.get("severity", "")).lower() in MAJOR
    ]
    blocking_rows = list(open_rows)

    process_failures = []
    malformed_names = {
        str(item.get("artifact")) for item in malformed if isinstance(item, dict)
    }
    if malformed_names & {"adversarial_review", "moderator_filter", "review_materialization"}:
        process_failures.append("review_artifact_missing_or_malformed")
    if "patch" in malformed_names:
        process_failures.append("patch_malformed_or_scope_drift")
    if any(
        isinstance(item, dict) and item.get("error") == "tests_not_executed_successfully"
        for item in malformed
    ):
        process_failures.append("tests_not_executed_successfully")
    if rows and not dispositions:
        process_failures.append("adversarial_rows_without_moderator_dispositions")
    if unaccounted_major_rows:
        process_failures.append("unaccounted_major_adversarial_rows")
    elif unaccounted_rows:
        process_failures.append("unaccounted_adversarial_rows")
    if duplicate_disposition_ids:
        process_failures.append("duplicate_moderator_dispositions")
    if duplicate_adversarial_row_ids:
        process_failures.append("duplicate_adversarial_rows")
    if orphan_dispositions:
        process_failures.append("orphan_moderator_dispositions")
    if invalid_dispositions:
        process_failures.append("invalid_moderator_dispositions")
    if closure_check_failures:
        process_failures.append("invalid_closure_checks")
    if blocking_rows:
        process_failures.append("open_review_rows")

    failed = bool(process_failures)
    fixup_required_rows = (
        blocking_rows
        or closure_check_failures
        or unaccounted_major_rows
        or unaccounted_rows
        or orphan_dispositions
        or invalid_dispositions
    ) + malformed
    readiness = (
        "process_failed"
        if failed
        else ("ready_verified" if tests_executed_successfully(test_gate) else "ready_unverified")
    )
    return {
        "schema_version": 1,
        "stage": "review_accountability_gate",
        "status": "failed" if failed else "passed",
        "process_status": "process_failed" if failed else "passed",
        "preferred_next_label": "Fix" if failed else "Approve",
        "route_decision": "fixup" if failed else "export",
        "readiness_tier": readiness,
        "failure_reason": "; ".join(process_failures),
        "process_failures": process_failures,
        "adversarial_row_count": len(rows),
        "moderator_disposition_count": len(dispositions),
        "open_rows": open_rows,
        "closed_rows": closed_rows,
        "downgraded_rows": downgraded_rows,
        "rejected_rows": rejected_rows,
        "blocking_rows": blocking_rows,
        "fixup_required_rows": fixup_required_rows,
        "unaccounted_adversarial_rows": unaccounted_rows,
        "unaccounted_major_rows": unaccounted_major_rows,
        "duplicate_disposition_ids": duplicate_disposition_ids,
        "duplicate_adversarial_row_ids": duplicate_adversarial_row_ids,
        "orphan_dispositions": orphan_dispositions,
        "invalid_dispositions": invalid_dispositions,
        "closure_check_failures": closure_check_failures,
        "tests_executed_successfully": tests_executed_successfully(test_gate),
        "materialization": materialization or {},
        "malformed_artifacts": malformed,
        "do_not_repeat": [
            "Do not export until every review objection is closed, rejected, or downgraded with cited evidence."
        ]
        if failed
        else [],
        "next_agent_guidance": next_agent_guidance(fixup_required_rows)
        if failed
        else "Proceed to patch extraction.",
    }


def load_json_object(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        obj = json.loads(path.read_text())
    except Exception as exc:
        return None, {"path": str(path), "error": str(exc)}
    if not isinstance(obj, dict):
        return None, {"path": str(path), "error": "expected_json_object"}
    return obj, None


def row_id(row: Any) -> str | None:
    value = row.get("id") if isinstance(row, dict) else None
    return None if value in (None, "") else str(value)


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def add_unique(values: list[Any], value: Any) -> None:
    if value not in values:
        values.append(value)


def clean_path(value: Any) -> str:
    text = str(value).strip()
    return text[len("/workspace/") :] if text.startswith("/workspace/") else text


def is_issue_artifact_path(path: str) -> bool:
    return path.startswith(".fabro/issue-to-pr/")


def has_evidence(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    for key in ("evidence", "evidence_citations", "cited_evidence"):
        value = row.get(key)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return bool(row.get("artifact_path") or row.get("artifact_field"))


def tests_executed_successfully(gate: Any) -> bool:
    if not isinstance(gate, dict):
        return False
    if gate.get("status") != "passed":
        return False
    observed = gate.get("observed") if isinstance(gate.get("observed"), dict) else {}
    for key in ("tests_passed_count",):
        value = observed.get(key) or gate.get(key)
        if type(value) is int and value > 0:
            return True
    return False


def score_closure(
    disposition: dict[str, Any],
    row: dict[str, Any],
    *,
    changed_files: set[str],
    test_gate: Any,
    patch_diff: str,
) -> None:
    closure_check = str(disposition.get("closure_check", "")).strip()
    if not closure_check:
        disposition["closure_score"] = 0
        disposition["closure_score_reason"] = "missing closure_check"
        return

    evidence_values = (
        text_values(disposition.get("evidence"))
        + text_values(disposition.get("evidence_citations"))
        + text_values(disposition.get("cited_evidence"))
    )
    evidence_blob = " ".join([closure_check, *evidence_values]).strip()
    if not concrete_evidence_present(evidence_blob, changed_files, patch_diff):
        disposition["closure_score"] = 1
        disposition["closure_score_reason"] = "closure has no concrete artifact evidence"
        return

    required_files = [clean_path(path) for path in as_list(row.get("required_files"))]
    cited_required = [path for path in required_files if path and path in evidence_blob]
    if (
        required_files
        and len(cited_required) == len(required_files)
        and all(path in changed_files for path in required_files)
    ):
        disposition["closure_score"] = 3
        disposition["closure_score_reason"] = "closure cites required changed file"
        return

    if (
        str(row.get("closure_requires", "")).lower() == "runtime_tests"
        and tests_executed_successfully(test_gate)
        and mentions_runtime_test(evidence_blob)
    ):
        disposition["closure_score"] = 3
        disposition["closure_score_reason"] = "closure cites machine-observed runtime test proof"
        return

    disposition["closure_score"] = 2
    disposition["closure_score_reason"] = "closure cites concrete artifact evidence"


def closure_score_too_low(disposition: dict[str, Any], severe: bool) -> bool:
    score = disposition.get("closure_score")
    if type(score) is not int:
        return True
    if safe_minor_rejection_without_artifact_evidence(disposition, severe):
        return False
    return score < (3 if severe else 2)


def safe_minor_rejection_without_artifact_evidence(
    disposition: dict[str, Any],
    severe: bool,
) -> bool:
    return (
        not severe
        and disposition.get("state") == "rejected"
        and disposition.get("closure_score") == 1
        and str(disposition.get("row_closure_requires", "")).lower() == "none"
    )


def text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def concrete_evidence_present(
    text: str,
    changed_files: set[str],
    patch_diff: str,
) -> bool:
    if any(path and path in text for path in changed_files):
        return True
    if ".fabro/issue-to-pr/" in text:
        return True
    if mentions_runtime_test(text):
        return True
    return bool(patch_diff and any(token.startswith(("+", "-")) for token in text.split()))


def mentions_runtime_test(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "pytest",
            "python -m",
            "python3 -m",
            "unittest",
            "tests_passed_count",
            "machine-observed",
            "runtime test",
            "test command",
        )
    )


def detect_negative_coverage_removal(patch_diff: str) -> list[dict[str, str]]:
    removals: list[dict[str, str]] = []
    added_keys_by_path: dict[str, set[str]] = {}
    old_path = ""
    current_path = ""
    lines = patch_diff.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("--- a/"):
            old_path = clean_path(line[len("--- a/") :])
            continue
        if line == "+++ /dev/null":
            current_path = old_path
            continue
        if line.startswith("+++ b/"):
            current_path = clean_path(line[len("+++ b/") :])
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if is_test_path(current_path) and is_negative_test_line(line[1:]):
                added_keys_by_path.setdefault(current_path, set()).add(
                    negative_test_key(line[1:], following_changed_line(lines, index, "+"))
                )
            continue
        if (
            line.startswith("-")
            and not line.startswith("---")
            and is_test_path(current_path)
            and is_negative_test_line(line[1:])
        ):
            removals.append(
                {
                    "path": current_path,
                    "removed_line": line[1:].strip(),
                    "key": negative_test_key(
                        line[1:], following_changed_line(lines, index, "-")
                    ),
                }
            )
    return [
        removal
        for removal in removals
        if removal["key"] not in added_keys_by_path.get(removal["path"], set())
    ]


def is_test_path(path: str) -> bool:
    name = Path(path).name.lower()
    parts = {part.lower() for part in Path(path).parts}
    return "tests" in parts or name.startswith("test_") or name.endswith("_test.py")


def is_negative_test_line(line: str) -> bool:
    lowered = line.lower()
    return any(
        marker in lowered
        for marker in (
            "pytest.raises",
            "assert_raises",
            "assertraises",
            "raises(",
            "expect_error",
            "assert_error",
        )
    )


def following_changed_line(lines: list[str], index: int, prefix: str) -> str:
    for line in lines[index + 1 :]:
        if line.startswith(("diff --git ", "@@ ", "+++ ", "--- ")):
            return ""
        if line.startswith(prefix) and not line.startswith(prefix * 3):
            text = line[1:].strip()
            if text:
                return text
    return ""


def negative_test_key(line: str, following_line: str) -> str:
    normalized_following = normalize_negative_test_line(following_line)
    if normalized_following:
        return normalized_following
    return normalize_negative_test_line(line)


def normalize_negative_test_line(line: str) -> str:
    return " ".join(line.replace('"', "'").split()).lower()


def next_agent_guidance(fixup_required_rows: list[Any]) -> str:
    values = []
    for item in fixup_required_rows[:3]:
        item = item if isinstance(item, dict) else {}
        values.append(
            str(
                item.get("falsifiable_check")
                or item.get("check")
                or item.get("reason")
                or item
            )
        )
    return "; ".join(values)


def build_embedded_accountability_gate_script(
    *,
    adversarial_path: str,
    moderator_path: str,
    test_gate_path: str,
    materialization_path: str,
    output_path: str,
) -> str:
    """Return a self-contained script for sandbox workflow execution."""
    return f"""python3 - <<'PY'
import json
import subprocess
from pathlib import Path
from typing import Any

MAJOR = {MAJOR!r}
STATES = {STATES!r}

{_embedded_gate_functions_source()}

ADVERSARIAL = Path({adversarial_path!r})
MODERATOR = Path({moderator_path!r})
TEST_GATE = Path({test_gate_path!r})
MATERIALIZATION = Path({materialization_path!r})
OUT = Path({output_path!r})
OUT.parent.mkdir(parents=True, exist_ok=True)

adversarial, adversarial_error = load_json_object(ADVERSARIAL)
moderator, moderator_error = load_json_object(MODERATOR)
test_gate, test_gate_error = load_json_object(TEST_GATE)
materialization, materialization_error = load_json_object(MATERIALIZATION)
settings_ref_diff = subprocess.run(["git", "diff", "--", "docs/ref/settings.txt"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True).stdout
patch_diff = subprocess.run(["git", "diff"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True).stdout
report = evaluate_review_accountability(
    adversarial=adversarial,
    moderator=moderator,
    test_gate=test_gate,
    materialization=materialization,
    adversarial_error=adversarial_error,
    moderator_error=moderator_error,
    test_gate_error=test_gate_error,
    materialization_error=materialization_error,
    patch_diff=patch_diff,
    settings_ref_diff=settings_ref_diff,
)
OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n")
print(json.dumps(report, sort_keys=True))
raise SystemExit(1 if report["status"] == "failed" else 0)
PY
"""


def _embedded_gate_functions_source() -> str:
    functions = (
        evaluate_review_accountability,
        load_json_object,
        row_id,
        as_list,
        add_unique,
        clean_path,
        is_issue_artifact_path,
        has_evidence,
        tests_executed_successfully,
        score_closure,
        closure_score_too_low,
        safe_minor_rejection_without_artifact_evidence,
        text_values,
        concrete_evidence_present,
        mentions_runtime_test,
        detect_negative_coverage_removal,
        is_test_path,
        is_negative_test_line,
        following_changed_line,
        negative_test_key,
        normalize_negative_test_line,
        next_agent_guidance,
    )
    return "\n\n".join(inspect.getsource(function) for function in functions)
