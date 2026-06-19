"""Small process and file helpers for lightweight evals."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def docker_image_available(image: str) -> bool:
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0

def git_run(repo_dir: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")

def git_capture(repo_dir: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout

def as_str_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []

def is_test_path(path: str) -> bool:
    return (
        path.startswith("tests/")
        or path.startswith("testing/")
        or "/tests/" in path
        or Path(path).name.startswith("test_")
        or Path(path).name.endswith("_test.py")
        or path.endswith("/tests.py")
    )

def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())

def read_optional_text(path: Path) -> str:
    return path.read_text() if path.exists() else ""
