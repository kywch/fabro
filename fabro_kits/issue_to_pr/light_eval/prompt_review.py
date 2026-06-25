"""Adversarial-review prompt recall eval."""

from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from typing import Any

from ..workflow_generator import (
    ADVERSARIAL_REVIEW_PATH,
    DIFF_AUDIT_PATH,
    MODERATOR_FILTER_PATH,
    TEST_EVIDENCE_GATE_PATH,
    VALIDATION_CONTRACT_PATH,
    _adversarial_review_prompt,
    _moderator_filter_prompt,
    dot_escape,
)
from .mini_swe.credentials import bridge_model_credentials, credential_preflight_report
from .mini_swe.repo import append_working_dir_config
from .process import git_capture, git_run
from .workflow_common import (
    extract_workflow_smoke_run_id,
    run_fabro_command,
    workflow_smoke_env,
    write_workflow_smoke_config,
)


PROMPT_REVIEW_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "adversarial_prompt_review"
)
TMP_RESEARCH_PATH = Path("/tmp/fabro-research.md")
TMP_RESEARCH_LOCK_PATH = Path("/tmp/fabro-research.md.lock")
PROCESS_FAILURE_KINDS = {
    "credential_bridge_failed",
    "credential_preflight_failed",
    "fabro_binary_missing",
    "workflow_run_failed",
    "workflow_run_id_missing",
}
REQUIRED_ROW_FIELDS = ("failure_mode", "falsifiable_check", "why_it_matters", "evidence")
SEMANTIC_ROW_FIELDS = ("failure_mode", "falsifiable_check", "why_it_matters")
PROMPT_REVIEW_MODES = ("adversarial", "moderated")
MODERATOR_DISPOSITION_STATES = {"open", "closed_by_evidence", "rejected", "downgraded"}
MODERATOR_EXPECTED_OPEN_STATES = {"open"}


@dataclass(frozen=True)
class PromptReviewCase:
    case_id: str
    root: Path
    validation: dict[str, Any]
    expected: dict[str, Any]


def list_prompt_review_cases(case: str = "all") -> list[Path]:
    if case == "all":
        return sorted(
            path
            for path in PROMPT_REVIEW_FIXTURE_ROOT.iterdir()
            if path.is_dir() and (path / "expected.json").exists()
        )
    path = PROMPT_REVIEW_FIXTURE_ROOT / case
    if not path.is_dir():
        raise SystemExit(f"unknown prompt-review case: {case}")
    return [path]


def run_prompt_review(
    case: str = "all",
    *,
    output_dir: Path | None = None,
    fabro_bin: Path = Path("target/debug/fabro"),
    provider: str | None = None,
    model: str | None = None,
    credential_bridge: str = "off",
    auth_storage_dir: Path | None = None,
    credential_preflight: bool = False,
    review_mode: str = "adversarial",
) -> dict[str, Any]:
    if review_mode not in PROMPT_REVIEW_MODES:
        raise ValueError(f"unsupported prompt-review mode: {review_mode}")
    case_dirs = list_prompt_review_cases(case)
    if output_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return _run_prompt_review_to_dir(
                case_dirs,
                Path(tmp),
                fabro_bin=fabro_bin,
                provider=provider,
                model=model,
                credential_bridge=credential_bridge,
                auth_storage_dir=auth_storage_dir,
                credential_preflight=credential_preflight,
                review_mode=review_mode,
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    return _run_prompt_review_to_dir(
        case_dirs,
        output_dir.resolve(),
        fabro_bin=fabro_bin,
        provider=provider,
        model=model,
        credential_bridge=credential_bridge,
        auth_storage_dir=auth_storage_dir,
        credential_preflight=credential_preflight,
        review_mode=review_mode,
    )


def _run_prompt_review_to_dir(
    case_dirs: list[Path],
    output_dir: Path,
    *,
    fabro_bin: Path,
    provider: str | None,
    model: str | None,
    credential_bridge: str,
    auth_storage_dir: Path | None,
    credential_preflight: bool,
    review_mode: str,
) -> dict[str, Any]:
    results = []
    failures = []
    for case_dir in case_dirs:
        result = run_prompt_review_case(
            case_dir,
            output_dir=output_dir,
            fabro_bin=fabro_bin,
            provider=provider,
            model=model,
            credential_bridge=credential_bridge,
            auth_storage_dir=auth_storage_dir,
            credential_preflight=credential_preflight,
            review_mode=review_mode,
        )
        results.append(result)
        failures.extend(result["failures"])

    summary = prompt_review_summary(results, failures)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    with (output_dir / "results.jsonl").open("w") as handle:
        for result in results:
            handle.write(json.dumps(result, sort_keys=True) + "\n")
    return summary


def run_prompt_review_case(
    case_dir: Path,
    *,
    output_dir: Path,
    fabro_bin: Path,
    provider: str | None,
    model: str | None,
    credential_bridge: str,
    auth_storage_dir: Path | None,
    credential_preflight: bool,
    review_mode: str,
) -> dict[str, Any]:
    case = load_prompt_review_case(case_dir)
    case_out = output_dir / case.case_id
    if case_out.exists():
        shutil.rmtree(case_out)
    case_out.mkdir(parents=True)
    repo_dir = case_out / "workspace"
    run_dir = case_out / "run"
    run_dir.mkdir()
    output_artifacts = case_out / "output"
    output_artifacts.mkdir()

    failures = []
    include_moderator = review_mode == "moderated"
    effective_provider = provider or ("openai" if credential_bridge == "openai-codex" else None)
    prepare_prompt_review_workspace(case_dir, repo_dir)
    patch = git_capture(repo_dir, "diff")
    (case_out / "patch.diff").write_text(patch)
    (output_artifacts / "patch.diff").write_text(patch)

    if not fabro_bin.exists():
        failure = {
            "kind": "fabro_binary_missing",
            "case_id": case.case_id,
            "path": str(fabro_bin),
            "reason": "Build fabro-cli first or pass --fabro-bin.",
        }
        failures.append(failure)
        score = score_adversarial_review(
            None,
            case.expected,
            not_applicable_reason="process_failed",
        )
        moderator_score = score_moderator_filter(
            None,
            None,
            case.expected,
            not_applicable_reason="process_failed",
        )
        _write_case_outputs(
            case_out,
            output_artifacts,
            score,
            failures,
            None,
            moderator_score=moderator_score,
            moderator=None,
        )
        return _case_result(
            case,
            score,
            failures,
            run_id=None,
            run_transcript="",
            moderator_score=moderator_score,
            review_mode=review_mode,
        )

    storage_dir = run_dir / "storage"
    config_path = run_dir / "settings.toml"
    workflow_path = run_dir / "workflow.fabro"
    write_workflow_smoke_config(storage_dir=storage_dir, config_path=config_path)
    append_working_dir_config(config_path, repo_dir)
    write_prompt_review_workflow(workflow_path, include_moderator=include_moderator)

    credential_bridge_report = bridge_model_credentials(
        bridge=credential_bridge,
        source_storage_dir=auth_storage_dir,
        target_storage_dir=storage_dir,
    )
    (run_dir / "credential_bridge.json").write_text(
        json.dumps(credential_bridge_report, indent=2, sort_keys=True) + "\n"
    )
    if credential_bridge == "openai-codex" and credential_bridge_report.get("status") != "copied":
        failures.append(
            {
                "kind": "credential_bridge_failed",
                "case_id": case.case_id,
                "status": credential_bridge_report.get("status"),
            }
        )

    env = workflow_smoke_env(config_path=config_path, storage_dir=storage_dir)
    if credential_bridge == "openai-codex":
        env.pop("OPENAI_API_KEY", None)
    preflight = credential_preflight_report(
        fabro_bin=fabro_bin,
        env=env,
        enabled=credential_preflight,
        provider=effective_provider,
        model=model,
    )
    (run_dir / "credential_preflight.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n"
    )
    if credential_preflight and preflight.get("status") == "failed":
        failures.append(
            {
                "kind": "credential_preflight_failed",
                "case_id": case.case_id,
                "returncode": preflight.get("returncode"),
            }
        )

    run_transcript = ""
    run_id = ""
    if not failures:
        with prompt_review_tmp_research(case_dir):
            run_proc = _run_prompt_review_workflow(
                fabro_bin=fabro_bin,
                workflow_path=workflow_path,
                env=env,
                provider=effective_provider,
                model=model,
            )
        (run_dir / "run.stdout").write_text(run_proc.stdout)
        (run_dir / "run.stderr").write_text(run_proc.stderr)
        run_transcript = run_proc.stdout + run_proc.stderr
        (run_dir / "run.transcript").write_text(run_transcript)
        run_id = extract_workflow_smoke_run_id(run_transcript)
        try:
            if run_proc.returncode != 0:
                failures.append(
                    {
                        "kind": "workflow_run_failed",
                        "case_id": case.case_id,
                        "exit_code": run_proc.returncode,
                        "stderr": run_proc.stderr[-2000:],
                    }
                )
            elif not run_id:
                failures.append({"kind": "workflow_run_id_missing", "case_id": case.case_id})
        finally:
            stop_proc = run_fabro_command(
                fabro_bin,
                ["--no-upgrade-check", "server", "stop", "--storage-dir", str(storage_dir)],
                env=env,
            )
            (run_dir / "stop.stdout").write_text(stop_proc.stdout)
            (run_dir / "stop.stderr").write_text(stop_proc.stderr)

    process_failed = any(is_process_failure(failure) for failure in failures)
    review, artifact_status = read_optional_json_artifact(repo_dir / ADVERSARIAL_REVIEW_PATH)
    if review is not None:
        (output_artifacts / "adversarial_review.json").write_text(
            json.dumps(review, indent=2, sort_keys=True) + "\n"
        )
    moderator, moderator_artifact_status = read_optional_json_artifact(
        repo_dir / MODERATOR_FILTER_PATH
    )
    if moderator is not None:
        (output_artifacts / "moderator_filter.json").write_text(
            json.dumps(moderator, indent=2, sort_keys=True) + "\n"
        )
    score = score_adversarial_review(
        review,
        case.expected,
        not_applicable_reason="process_failed" if process_failed else None,
    )
    moderator_score = score_moderator_filter(
        review,
        moderator,
        case.expected,
        not_applicable_reason="process_failed"
        if process_failed
        else (
            "adversarial_review_failed"
            if not score["passed"]
            else (None if include_moderator else "moderator_disabled")
        ),
    )
    if not process_failed and not score["passed"]:
        if artifact_status != "present":
            failures.append({"kind": artifact_status, "case_id": case.case_id, "score": score})
        else:
            failures.append({"kind": "prompt_miss", "case_id": case.case_id, "score": score})
    if include_moderator and not process_failed and score["passed"] and not moderator_score["passed"]:
        if moderator_artifact_status != "present":
            failures.append(
                {
                    "kind": f"moderator_{moderator_artifact_status}",
                    "case_id": case.case_id,
                    "score": moderator_score,
                }
            )
        else:
            failures.append(
                {
                    "kind": "moderator_prompt_miss",
                    "case_id": case.case_id,
                    "score": moderator_score,
                }
            )
    _write_case_outputs(
        case_out,
        output_artifacts,
        score,
        failures,
        review,
        moderator_score=moderator_score,
        moderator=moderator,
    )
    return _case_result(
        case,
        score,
        failures,
        run_id=run_id or None,
        run_transcript=run_transcript,
        moderator_score=moderator_score,
        review_mode=review_mode,
    )


def load_prompt_review_case(case_dir: Path) -> PromptReviewCase:
    validation = read_json_object(case_dir / "input" / "validation.json")
    expected = read_json_object(case_dir / "expected.json")
    expected_rows = expected.get("expected_rows")
    expected_no_findings = expected.get("expected_no_findings") is True
    if not isinstance(expected_rows, list) or (not expected_rows and not expected_no_findings):
        raise ValueError(f"prompt-review case needs expected_rows: {case_dir}")
    return PromptReviewCase(
        case_id=str(expected.get("case_id") or case_dir.name),
        root=case_dir,
        validation=validation,
        expected=expected,
    )


def prepare_prompt_review_workspace(case_dir: Path, repo_dir: Path) -> None:
    input_dir = case_dir / "input"
    base_files = input_dir / "base_files"
    shutil.copytree(base_files, repo_dir)
    git_run(repo_dir, "init")
    git_run(repo_dir, "config", "user.email", "light-eval@example.invalid")
    git_run(repo_dir, "config", "user.name", "Light Eval")
    git_run(repo_dir, "add", ".")
    git_run(repo_dir, "commit", "-m", "base")

    patch = (input_dir / "patch.diff").read_text()
    proc_patch = repo_dir / ".prompt-review.patch"
    proc_patch.write_text(patch)
    git_run(repo_dir, "apply", "--whitespace=nowarn", str(proc_patch))
    proc_patch.unlink()
    write_json_artifact(repo_dir / VALIDATION_CONTRACT_PATH, read_json_object(input_dir / "validation.json"))
    write_json_artifact(repo_dir / DIFF_AUDIT_PATH, read_json_object(input_dir / "diff-audit.json"))
    write_json_artifact(
        repo_dir / TEST_EVIDENCE_GATE_PATH,
        read_json_object(input_dir / "test-evidence-gate.json"),
    )
    research = input_dir / "fabro-research.md"
    if research.exists():
        (repo_dir / "fabro-research.md").write_text(research.read_text())
        (repo_dir / "tmp").mkdir(exist_ok=True)
        (repo_dir / "tmp" / "fabro-research.md").write_text(research.read_text())


@contextmanager
def prompt_review_tmp_research(case_dir: Path) -> Any:
    TMP_RESEARCH_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TMP_RESEARCH_LOCK_PATH.open("a+") as lock_handle:
        flock(lock_handle, LOCK_EX)
        try:
            with _locked_prompt_review_tmp_research(case_dir):
                yield
        finally:
            flock(lock_handle, LOCK_UN)


@contextmanager
def _locked_prompt_review_tmp_research(case_dir: Path) -> Any:
    research = case_dir / "input" / "fabro-research.md"

    old_bytes = TMP_RESEARCH_PATH.read_bytes() if TMP_RESEARCH_PATH.exists() else None
    if research.exists():
        TMP_RESEARCH_PATH.write_text(research.read_text())
    else:
        try:
            TMP_RESEARCH_PATH.unlink()
        except FileNotFoundError:
            pass
    try:
        yield
    finally:
        if old_bytes is None:
            try:
                TMP_RESEARCH_PATH.unlink()
            except FileNotFoundError:
                pass
        else:
            TMP_RESEARCH_PATH.write_bytes(old_bytes)


def write_prompt_review_workflow(workflow_path: Path, *, include_moderator: bool = False) -> None:
    moderator_node = ""
    review_edges = "  start -> adversarial_review -> exit\n"
    if include_moderator:
        moderator_node = (
            f"  moderator_filter [label=\"Moderator Filter\", "
            f"prompt=\"{dot_escape(_moderator_filter_prompt())}\"]\n"
        )
        review_edges = "  start -> adversarial_review -> moderator_filter -> exit\n"
    workflow_path.write_text(
        "digraph AdversarialPromptReview {\n"
        "  graph [goal=\"issue-to-pr adversarial prompt recall review\"]\n"
        "  start [shape=Mdiamond, label=\"Start\"]\n"
        "  exit [shape=Msquare, label=\"Exit\"]\n"
        f"  adversarial_review [label=\"Adversarial Review\", prompt=\"{dot_escape(_adversarial_review_prompt())}\"]\n"
        f"{moderator_node}"
        f"{review_edges}"
        "}\n"
    )


def score_adversarial_review(
    review: dict[str, Any] | None,
    expected: dict[str, Any],
    *,
    not_applicable_reason: str | None = None,
) -> dict[str, Any]:
    artifact_valid = isinstance(review, dict) and isinstance(review.get("rows"), list)
    rows = review.get("rows", []) if artifact_valid else []
    expected_rows = expected.get("expected_rows", [])
    matched_ids = []
    hidden_ids = []
    for expected_row in expected_rows:
        if any(row_matches_expected(row, expected_row) for row in rows if isinstance(row, dict)):
            matched_ids.append(expected_row.get("id"))
        elif risk_hidden(review, expected_row):
            hidden_ids.append(expected_row.get("id"))
    near_miss_ids = [
        expected_row.get("id")
        for expected_row in expected_rows
        if expected_row.get("id") not in matched_ids
        and expected_row.get("id") not in hidden_ids
        and any(
            row_semantically_mentions_expected(row, expected_row)
            for row in rows
            if isinstance(row, dict)
        )
    ]
    extra_rows = [
        row
        for row in rows
        if isinstance(row, dict)
        and not any(row_matches_expected(row, expected_row) for expected_row in expected_rows)
    ]
    severity_failures = unexpected_severity_failures(
        extra_rows,
        str(expected.get("expected_max_severity") or ""),
    )
    row_recall = len(matched_ids) / len(expected_rows) if expected_rows else 1.0
    precision = len(matched_ids) / len(rows) if rows else (1.0 if not expected_rows else 0.0)
    not_hidden = not hidden_ids
    precision_pass = not precision_failures(extra_rows)
    not_applicable = not_applicable_reason is not None
    passed = (
        not not_applicable
        and artifact_valid
        and row_recall == 1.0
        and not_hidden
        and precision_pass
        and not severity_failures
    )
    return {
        "artifact_valid": artifact_valid,
        "matched_expected_ids": matched_ids,
        "hidden_expected_ids": hidden_ids,
        "near_miss_expected_ids": near_miss_ids,
        "missing_expected_ids": [
            expected_row.get("id")
            for expected_row in expected_rows
            if expected_row.get("id") not in matched_ids
        ],
        "extra_row_count": len(extra_rows),
        "not_hidden": not_hidden,
        "not_applicable": not_applicable,
        "not_applicable_reason": not_applicable_reason,
        "passed": passed,
        "precision_pass": precision_pass,
        "precision": precision,
        "precision_failures": precision_failures(extra_rows),
        "row_recall": row_recall,
        "unexpected_severity_failures": severity_failures,
    }


def score_moderator_filter(
    review: dict[str, Any] | None,
    moderator: dict[str, Any] | None,
    expected: dict[str, Any],
    *,
    not_applicable_reason: str | None = None,
) -> dict[str, Any]:
    review_rows = review.get("rows", []) if isinstance(review, dict) else []
    artifact_valid = (
        isinstance(moderator, dict)
        and isinstance(moderator.get("dispositions"), list)
        and isinstance(review_rows, list)
    )
    dispositions = moderator.get("dispositions", []) if artifact_valid else []
    rows = [row for row in review_rows if isinstance(row, dict)] if artifact_valid else []
    row_ids = [str(row.get("id")) for row in rows if row.get("id") is not None]
    disposition_ids = [
        str(disposition.get("id"))
        for disposition in dispositions
        if isinstance(disposition, dict) and disposition.get("id") is not None
    ]
    duplicate_disposition_ids = sorted(
        {
            disposition_id
            for disposition_id in disposition_ids
            if disposition_ids.count(disposition_id) > 1
        }
    )
    orphan_disposition_ids = sorted(set(disposition_ids) - set(row_ids))
    missing_disposition_ids = sorted(set(row_ids) - set(disposition_ids))
    invalid_dispositions = [
        {
            "id": disposition.get("id"),
            "reason": "invalid_disposition",
        }
        for disposition in dispositions
        if not valid_moderator_disposition(disposition)
    ]

    matched_expected_ids = []
    expected_non_open_ids = []
    missing_expected_disposition_ids = []
    for expected_row in expected.get("expected_rows", []):
        matched_row = next(
            (row for row in rows if row_matches_expected(row, expected_row)),
            None,
        )
        if matched_row is None:
            continue
        expected_id = expected_row.get("id")
        matched_expected_ids.append(expected_id)
        disposition = disposition_for_row(dispositions, matched_row)
        if disposition is None:
            missing_expected_disposition_ids.append(expected_id)
        elif str(disposition.get("state", "")).lower() not in MODERATOR_EXPECTED_OPEN_STATES:
            expected_non_open_ids.append(expected_id)

    not_applicable = not_applicable_reason is not None
    rows_accounted = (
        not duplicate_disposition_ids
        and not orphan_disposition_ids
        and not missing_disposition_ids
        and not missing_expected_disposition_ids
    )
    expected_open_recall = (
        (
            len(matched_expected_ids)
            - len(expected_non_open_ids)
            - len(missing_expected_disposition_ids)
        )
        / len(matched_expected_ids)
        if matched_expected_ids
        else 0.0
    )
    passed = (
        not not_applicable
        and artifact_valid
        and rows_accounted
        and not invalid_dispositions
        and expected_open_recall == 1.0
    )
    return {
        "artifact_valid": artifact_valid,
        "duplicate_disposition_ids": duplicate_disposition_ids,
        "expected_non_open_ids": expected_non_open_ids,
        "expected_open_recall": expected_open_recall,
        "invalid_dispositions": invalid_dispositions,
        "matched_expected_ids": matched_expected_ids,
        "missing_disposition_ids": missing_disposition_ids,
        "missing_expected_disposition_ids": missing_expected_disposition_ids,
        "not_applicable": not_applicable,
        "not_applicable_reason": not_applicable_reason,
        "orphan_disposition_ids": orphan_disposition_ids,
        "passed": passed,
        "rows_accounted": rows_accounted,
    }


def disposition_for_row(
    dispositions: list[Any],
    row: dict[str, Any],
) -> dict[str, Any] | None:
    row_id = row.get("id")
    for disposition in dispositions:
        if isinstance(disposition, dict) and disposition.get("id") == row_id:
            return disposition
    return None


def valid_moderator_disposition(disposition: Any) -> bool:
    if not isinstance(disposition, dict):
        return False
    state = str(disposition.get("state", "")).lower()
    if state not in MODERATOR_DISPOSITION_STATES:
        return False
    if not row_field_nonempty(disposition.get("id")):
        return False
    if not row_field_nonempty(disposition.get("reason")):
        return False
    if state != "open" and not row_field_nonempty(disposition.get("closure_check")):
        return False
    return True


def row_matches_expected(row: dict[str, Any], expected: dict[str, Any]) -> bool:
    if not required_row_fields_present(row):
        return False
    categories = expected.get("categories") or [expected.get("category")]
    if categories != [None] and row.get("category") not in categories:
        return False
    if not severity_at_least(str(row.get("severity") or ""), str(expected.get("min_severity") or expected.get("severity") or "")):
        return False
    haystack = row_text(row)
    semantic_haystack = row_text({field: row.get(field) for field in SEMANTIC_ROW_FIELDS})
    for token in expected.get("required_path_tokens", []):
        if str(token).lower() not in haystack:
            return False
    for group in expected.get("keyword_groups", []):
        if not any(str(keyword).lower() in semantic_haystack for keyword in group):
            return False
    return True


def row_semantically_mentions_expected(row: dict[str, Any], expected: dict[str, Any]) -> bool:
    keyword_groups = expected.get("keyword_groups", [])
    if not keyword_groups:
        return False
    semantic_haystack = row_text({field: row.get(field) for field in SEMANTIC_ROW_FIELDS})
    return all(
        any(str(keyword).lower() in semantic_haystack for keyword in group)
        for group in keyword_groups
    )


def required_row_fields_present(row: dict[str, Any]) -> bool:
    return all(row_field_nonempty(row.get(field)) for field in REQUIRED_ROW_FIELDS)


def row_field_nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(row_field_nonempty(item) for item in value)
    return value is not None


def severity_at_least(actual: str, minimum: str) -> bool:
    if not minimum:
        return True
    return severity_level(actual, default=-1) >= severity_level(minimum, default=99)


def severity_level(severity: str, *, default: int) -> int:
    levels = {"info": 0, "minor": 1, "major": 2, "blocker": 3, "critical": 3}
    return levels.get(severity.lower(), default)


def precision_failures(extra_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures = []
    for row in extra_rows:
        if str(row.get("severity", "")).lower() not in {"major", "blocker", "critical"}:
            continue
        text = row_text(row)
        if any(token in text for token in ("changelog", "release note", "release-note", "whatsnew")):
            failures.append(
                {
                    "id": row.get("id"),
                    "reason": "invented_release_or_changelog_requirement",
                }
            )
    return failures


def unexpected_severity_failures(
    extra_rows: list[dict[str, Any]],
    max_severity: str,
) -> list[dict[str, Any]]:
    if not max_severity:
        return []
    maximum = severity_level(max_severity, default=99)
    failures = []
    for row in extra_rows:
        actual = str(row.get("severity", ""))
        if severity_level(actual, default=99) > maximum:
            failures.append(
                {
                    "id": row.get("id"),
                    "reason": "unexpected_severity_above_fixture_max",
                    "severity": actual,
                    "expected_max_severity": max_severity,
                }
            )
    return failures


def risk_hidden(review: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    if not isinstance(review, dict):
        return False
    hidden_payload = {
        "checked_risks": review.get("checked_risks"),
        "counterexample_checks": review.get("counterexample_checks"),
    }
    haystack = row_text(hidden_payload)
    return all(
        any(str(keyword).lower() in haystack for keyword in group)
        for group in expected.get("keyword_groups", [])
    )


def row_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str).lower()


def prompt_review_summary(results: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    failure_case_ids = {failure.get("case_id") for failure in failures if failure.get("case_id")}
    applicable_moderator_scores = [
        result["moderator_score"]
        for result in results
        if not result["moderator_score"]["not_applicable"]
    ]
    review_modes = sorted({str(result.get("review_mode", "adversarial")) for result in results})
    return {
        "schema_version": 1,
        "mode": "prompt-review",
        "review_mode": review_modes[0] if len(review_modes) == 1 else "mixed",
        "total": total,
        "failed": len(failure_case_ids),
        "failures": failures,
        "process_failed": len(
            {
                failure.get("case_id")
                for failure in failures
                if failure.get("case_id") and is_process_failure(failure)
            }
        ),
        "artifact_missing": len(
            {
                failure.get("case_id")
                for failure in failures
                if failure.get("case_id") and failure.get("kind") == "artifact_missing"
            }
        ),
        "artifact_invalid": len(
            {
                failure.get("case_id")
                for failure in failures
                if failure.get("case_id") and failure.get("kind") == "artifact_invalid"
            }
        ),
        "prompt_miss": len(
            {
                failure.get("case_id")
                for failure in failures
                if failure.get("case_id") and failure.get("kind") == "prompt_miss"
            }
        ),
        "moderator_prompt_miss": len(
            {
                failure.get("case_id")
                for failure in failures
                if failure.get("case_id")
                and failure.get("kind") == "moderator_prompt_miss"
            }
        ),
        "artifact_valid": sum(1 for result in results if result["score"]["artifact_valid"]),
        "row_recall": (
            sum(result["score"]["row_recall"] for result in results) / total if total else 0.0
        ),
        "not_hidden": sum(1 for result in results if result["score"]["not_hidden"]),
        "precision": (
            sum(result["score"]["precision"] for result in results) / total if total else 0.0
        ),
        "moderator_artifact_valid": sum(
            1 for score in applicable_moderator_scores if score["artifact_valid"]
        ),
        "moderator_rows_accounted": sum(
            1 for score in applicable_moderator_scores if score["rows_accounted"]
        ),
        "moderator_expected_open_recall": (
            sum(score["expected_open_recall"] for score in applicable_moderator_scores)
            / len(applicable_moderator_scores)
            if applicable_moderator_scores
            else 0.0
        ),
    }


def is_process_failure(failure: dict[str, Any]) -> bool:
    return failure.get("kind") in PROCESS_FAILURE_KINDS


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def read_optional_json_object(path: Path) -> dict[str, Any] | None:
    try:
        return read_json_object(path)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return None


def read_optional_json_artifact(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        return read_json_object(path), "present"
    except FileNotFoundError:
        return None, "artifact_missing"
    except (json.JSONDecodeError, ValueError):
        return None, "artifact_invalid"


def write_json_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _run_prompt_review_workflow(
    *,
    fabro_bin: Path,
    workflow_path: Path,
    env: dict[str, str],
    provider: str | None,
    model: str | None,
) -> Any:
    args = [
        "--no-upgrade-check",
        "run",
        "--auto-approve",
        "--environment",
        "local",
    ]
    if provider:
        args.extend(["--provider", provider])
    if model:
        args.extend(["--model", model])
    args.append(str(workflow_path))
    return run_fabro_command(fabro_bin, args, env=env, timeout=600)


def _write_case_outputs(
    case_out: Path,
    output_artifacts: Path,
    score: dict[str, Any],
    failures: list[dict[str, Any]],
    review: dict[str, Any] | None,
    *,
    moderator_score: dict[str, Any],
    moderator: dict[str, Any] | None,
) -> None:
    (case_out / "score.json").write_text(json.dumps(score, indent=2, sort_keys=True) + "\n")
    (case_out / "moderator-score.json").write_text(
        json.dumps(moderator_score, indent=2, sort_keys=True) + "\n"
    )
    if review is None and not (output_artifacts / "adversarial_review.json").exists():
        (output_artifacts / "adversarial_review.json").write_text("{}\n")
    if moderator is None and not (output_artifacts / "moderator_filter.json").exists():
        (output_artifacts / "moderator_filter.json").write_text("{}\n")
    (case_out / "failures.json").write_text(json.dumps(failures, indent=2, sort_keys=True) + "\n")


def _case_result(
    case: PromptReviewCase,
    score: dict[str, Any],
    failures: list[dict[str, Any]],
    *,
    run_id: str | None,
    run_transcript: str,
    moderator_score: dict[str, Any],
    review_mode: str,
) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "review_mode": review_mode,
        "status": "passed" if not failures else "failed",
        "score": score,
        "moderator_score": moderator_score,
        "failures": failures,
        "fabro_run_id": run_id,
        "run_transcript_tail": run_transcript[-2000:],
    }
