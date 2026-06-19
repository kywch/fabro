"""Lightweight issue-to-PR eval package facade."""

from __future__ import annotations

from .bundles import (
    dataset_for_source_kind,
    instance_for_task,
    prepare_config_dir,
    prepare_issue_workflow_smoke_config_dir,
    prepare_synthetic_config_dir,
    synthetic_instance_for_task,
    synthetic_source_for_task,
)
from .claimed_test_mismatch import claimed_test_mismatch_patch_text
from .cli import main
from .expectations import (
    check_expected,
    check_root_expected,
    issue_workflow_smoke_expected,
    synthetic_expected_for_task,
)
from .grader import MiniSweGrade, grade_mini_swe_attempt
from .issue_workflow_smoke import (
    build_issue_workflow_smoke_case,
    issue_workflow_diff_script,
    issue_workflow_review_script,
    run_issue_workflow_smoke,
    write_issue_workflow_smoke_files,
    write_issue_workflow_smoke_summary,
)
from .local_repo import run_claimed_test_mismatch_local_repo
from .mini_swe import list_mini_swe_cases, run_mini_swe, run_mini_swe_case
from .paths import DEFAULT_SYNTHETIC_DOCKER_IMAGE, FIXTURE_ROOT, list_fixtures
from .process import (
    as_str_list,
    docker_image_available,
    git_capture,
    git_run,
    is_test_path,
    read_json,
    read_optional_text,
)
from .replay import run_replay, run_replay_fixture
from .repo_cases import apply_claimed_test_mismatch_patch, create_claimed_test_mismatch_repo
from .sandboxed_repo import run_claimed_test_mismatch_docker_repo
from .synthetic import list_synthetic_tasks, run_synthetic, run_synthetic_task
from .task_schema import AttemptResult, AttemptRunner, MiniSweCase, mini_swe_source
from .workflow_common import (
    extract_workflow_smoke_run_id,
    run_fabro_command,
    workflow_smoke_dev_token,
    workflow_smoke_env,
    workflow_smoke_session_secret,
    write_workflow_smoke_config,
)
from .workflow_smoke import (
    run_workflow_smoke,
    workflow_probe_script,
    write_workflow_smoke_files,
    write_workflow_smoke_summary,
)
from ..evidence_gate import evaluate_evidence_gate
from ..review_accountability_gate import evaluate_review_accountability, load_json_object
from ..workflow_generator import dot_escape

__all__ = [
    "DEFAULT_SYNTHETIC_DOCKER_IMAGE",
    "FIXTURE_ROOT",
    "apply_claimed_test_mismatch_patch",
    "as_str_list",
    "AttemptResult",
    "AttemptRunner",
    "build_issue_workflow_smoke_case",
    "check_expected",
    "check_root_expected",
    "claimed_test_mismatch_patch_text",
    "create_claimed_test_mismatch_repo",
    "dataset_for_source_kind",
    "dot_escape",
    "docker_image_available",
    "evaluate_evidence_gate",
    "evaluate_review_accountability",
    "extract_workflow_smoke_run_id",
    "git_capture",
    "git_run",
    "grade_mini_swe_attempt",
    "instance_for_task",
    "is_test_path",
    "issue_workflow_diff_script",
    "issue_workflow_review_script",
    "issue_workflow_smoke_expected",
    "list_fixtures",
    "list_mini_swe_cases",
    "list_synthetic_tasks",
    "load_json_object",
    "main",
    "MiniSweCase",
    "MiniSweGrade",
    "mini_swe_source",
    "prepare_config_dir",
    "prepare_issue_workflow_smoke_config_dir",
    "prepare_synthetic_config_dir",
    "read_json",
    "read_optional_text",
    "run_claimed_test_mismatch_docker_repo",
    "run_claimed_test_mismatch_local_repo",
    "run_fabro_command",
    "run_issue_workflow_smoke",
    "run_mini_swe",
    "run_mini_swe_case",
    "run_replay",
    "run_replay_fixture",
    "run_synthetic",
    "run_synthetic_task",
    "run_workflow_smoke",
    "synthetic_expected_for_task",
    "synthetic_instance_for_task",
    "synthetic_source_for_task",
    "workflow_probe_script",
    "workflow_smoke_dev_token",
    "workflow_smoke_env",
    "workflow_smoke_session_secret",
    "write_issue_workflow_smoke_files",
    "write_issue_workflow_smoke_summary",
    "write_workflow_smoke_config",
    "write_workflow_smoke_files",
    "write_workflow_smoke_summary",
]
