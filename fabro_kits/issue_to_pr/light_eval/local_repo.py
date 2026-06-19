"""Local repository execution for synthetic eval cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .process import git_capture, git_run
from .repo_cases import apply_claimed_test_mismatch_patch


def run_claimed_test_mismatch_local_repo(repo_dir: Path) -> dict[str, Any]:
    apply_claimed_test_mismatch_patch(repo_dir)
    git_run(repo_dir, "add", "-N", ".")
    return {
        "mode": "synthetic-local-repo",
        "sandbox_provider": "synthetic-local-repo",
        "source_kind": "synthetic_local_repo",
        "patch": git_capture(repo_dir, "diff"),
        "changed_files": git_capture(repo_dir, "diff", "--name-only").splitlines(),
    }
