"""Sandboxed repository execution for synthetic eval cases."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .claimed_test_mismatch import claimed_test_mismatch_patch_text
from .process import docker_image_available


def run_claimed_test_mismatch_docker_repo(
    repo_dir: Path,
    *,
    docker_image: str,
) -> dict[str, Any]:
    if not docker_image_available(docker_image):
        raise SystemExit(
            f"synthetic docker image is not available locally: {docker_image}. "
            "Build/pull it or pass --docker-image."
        )
    script = "\n".join(
        [
            "set -euo pipefail",
            "cd /workspace",
            "git apply --whitespace=nowarn - <<'PATCH'",
            claimed_test_mismatch_patch_text().rstrip(),
            "PATCH",
            "git add -N .",
            "printf '__FABRO_PATCH_START__\\n'",
            "git diff",
            "printf '__FABRO_CHANGED_FILES_START__\\n'",
            "git diff --name-only",
        ]
    )
    proc = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{repo_dir.resolve()}:/workspace",
            "-w",
            "/workspace",
            docker_image,
            "bash",
            "-lc",
            script,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker synthetic task failed: {proc.stderr}")
    patch_marker = "__FABRO_PATCH_START__\n"
    changed_marker = "__FABRO_CHANGED_FILES_START__\n"
    if patch_marker not in proc.stdout or changed_marker not in proc.stdout:
        raise RuntimeError(f"docker synthetic task returned malformed output: {proc.stdout}")
    _, payload = proc.stdout.split(patch_marker, 1)
    patch, changed_text = payload.split(changed_marker, 1)
    return {
        "mode": "synthetic-docker-sandbox",
        "sandbox_provider": "docker",
        "source_kind": "synthetic_sandboxed_repo",
        "patch": patch,
        "changed_files": changed_text.splitlines(),
    }
