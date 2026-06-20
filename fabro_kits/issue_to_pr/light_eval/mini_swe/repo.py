"""Generated repository helpers for mini-SWE cases."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ..process import docker_image_available, git_run
from ..task_schema import MiniSweCase
from .cases import (
    case_behavior,
    greeting_test_text,
    initial_public_test_expected,
    public_test_text_for_case,
)


def create_repo_for_case(case: MiniSweCase, repo_dir: Path) -> None:
    src = repo_dir / "src"
    tests = repo_dir / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "__init__.py").write_text("")
    (src / "greeting.py").write_text(
        "def greeting(name):\n"
        "    return f\"hello {name}\"\n"
    )
    (tests / "test_greeting.py").write_text(
        greeting_test_text(initial_public_test_expected(case))
    )
    git_run(repo_dir, "init")
    git_run(repo_dir, "add", "src/greeting.py", "src/__init__.py", "tests/test_greeting.py")


def apply_case_patch(case: MiniSweCase, repo_dir: Path) -> None:
    behavior = case_behavior(case)
    if behavior.change_source:
        (repo_dir / "src" / "greeting.py").write_text(
            "def greeting(name):\n"
            "    return f\"hello, {name}\"\n"
    )
    if behavior.change_test:
        (repo_dir / "tests" / "test_greeting.py").write_text(
            public_test_text_for_case(case)
        )


def model_setup_script(repo_dir: Path) -> str:
    return (
        "python3 - <<'PY'\n"
        "import shutil\n"
        "from pathlib import Path\n"
        f"src = Path({json.dumps(str(repo_dir))})\n"
        "dst = Path.cwd()\n"
        "if any(dst.iterdir()):\n"
        "    raise SystemExit(f'mini-swe model setup refuses non-empty cwd: {dst}')\n"
        "for child in src.iterdir():\n"
        "    target = dst / child.name\n"
        "    if child.is_dir():\n"
        "        shutil.copytree(child, target, dirs_exist_ok=True)\n"
        "    else:\n"
        "        shutil.copy2(child, target)\n"
        "print('mini-swe model setup: copied generated repo')\n"
        "PY"
    )


def append_working_dir_config(config_path: Path, working_dir: Path) -> None:
    with config_path.open("a") as handle:
        handle.write(
            "\n"
            "[run]\n"
            f"working_dir = {json.dumps(str(working_dir.resolve()))}\n"
        )


def apply_patch_to_repo(repo_dir: Path, patch: str) -> None:
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
        raise RuntimeError(f"mini-swe model patch did not apply to generated repo: {proc.stderr}")


def run_public_tests(repo_dir: Path) -> None:
    proc = subprocess.run(
        ["python3", "-m", "unittest", "discover", "-s", "tests"],
        cwd=repo_dir,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"scripted mini-swe test command failed: {proc.stderr}")


def apply_case_patch_in_docker(
    case: MiniSweCase,
    repo_dir: Path,
    *,
    docker_image: str,
) -> None:
    if not docker_image_available(docker_image):
        raise SystemExit(
            f"mini-swe docker image is not available locally: {docker_image}. "
            "Build/pull it or pass --docker-image."
        )
    behavior = case_behavior(case)
    test_text = (
        public_test_text_for_case(case)
        if behavior.change_test
        else None
    )
    script = (
        "set -euo pipefail\n"
        "cd /workspace\n"
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "repo = Path('/workspace')\n"
        f"change_source = {behavior.change_source!r}\n"
        "if change_source:\n"
        "    (repo / 'src' / 'greeting.py').write_text('def greeting(name):\\n    return f\"hello, {name}\"\\n')\n"
        f"test_text = {json.dumps(test_text)}\n"
        "if test_text is not None:\n"
        "    (repo / 'tests' / 'test_greeting.py').write_text(test_text)\n"
        "PY\n"
        "PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests\n"
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
        raise RuntimeError(f"docker mini-swe scripted task failed: {proc.stderr}")


def run_hidden_oracle(case: MiniSweCase, repo_dir: Path) -> dict[str, Any]:
    expected = case_behavior(case).hidden_oracle_expected
    name = "Grace"
    code = (
        "from src.greeting import greeting\n"
        f"assert greeting({name!r}) == {expected!r}, greeting({name!r})\n"
    )
    proc = subprocess.run(
        ["python3", "-c", code],
        cwd=repo_dir,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return {
        "schema_version": 1,
        "command": "python3 -c <mini-swe-hidden-oracle>",
        "case_id": case.case_id,
        "passed": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }
