"""Shared workflow-smoke runtime helpers."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def write_workflow_smoke_config(*, storage_dir: Path, config_path: Path) -> None:
    storage_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "_version = 1\n"
        "\n"
        "[server.storage]\n"
        f"root = {json.dumps(str(storage_dir))}\n"
        "\n"
        "[server.auth]\n"
        "methods = [\"dev-token\"]\n"
        "\n"
        "[server.sandbox.providers.local]\n"
        "enabled = true\n"
        "\n"
        "[server.sandbox.providers.docker]\n"
        "enabled = false\n"
        "\n"
        "[server.sandbox.providers.daytona]\n"
        "enabled = false\n"
    )
    token = workflow_smoke_dev_token()
    secret = workflow_smoke_session_secret()
    (storage_dir / "server.dev-token").write_text(token + "\n")
    (storage_dir / "server.env").write_text(
        f"FABRO_DEV_TOKEN={token}\nSESSION_SECRET={secret}\n"
    )

def workflow_smoke_env(*, config_path: Path, storage_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "FABRO_CONFIG": str(config_path),
            "FABRO_STORAGE_DIR": str(storage_dir),
            "FABRO_SERVER": str(storage_dir / "fabro.sock"),
            "FABRO_NO_UPGRADE_CHECK": "true",
        }
    )
    return env

def workflow_smoke_dev_token() -> str:
    return "fabro_dev_abababababababababababababababababababababababababababababababab"

def workflow_smoke_session_secret() -> str:
    return "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

def extract_workflow_smoke_run_id(stdout: str) -> str:
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Run:"):
            return stripped.split("Run:", 1)[1].strip()
    return ""

def run_fabro_command(
    fabro_bin: Path,
    args: list[str],
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(fabro_bin), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=env,
        timeout=60,
    )
