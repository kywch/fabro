"""Synthetic repository case construction helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .claimed_test_mismatch import claimed_test_mismatch_patch_text
from .process import git_run


def create_claimed_test_mismatch_repo(repo_dir: Path) -> None:
    src = repo_dir / "src"
    src.mkdir()
    (src / "greeting.py").write_text(
        "def greeting(name):\n"
        "    return f\"hello {name}\"\n"
    )
    git_run(repo_dir, "init")
    git_run(repo_dir, "add", "src/greeting.py")

def apply_claimed_test_mismatch_patch(repo_dir: Path) -> None:
    patch = claimed_test_mismatch_patch_text()
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=repo_dir,
        input=patch,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git apply failed: {proc.stderr}")
