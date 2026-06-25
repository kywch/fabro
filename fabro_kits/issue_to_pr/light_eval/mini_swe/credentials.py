"""Credential and vault helpers for mini-SWE model attempts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..workflow_common import run_fabro_command


DEFAULT_AUTH_STORAGE_CANDIDATES = (
    Path("tmp/issue-to-pr-auth-storage"),
    Path("tmp/iter-workflow-v4/auth-storage"),
)


def bridge_model_credentials(
    *,
    bridge: str,
    source_storage_dir: Path | None,
    target_storage_dir: Path,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "bridge": bridge,
        "status": "disabled" if bridge == "off" else "not_run",
        "copied_secret_names": [],
        "missing_secret_names": [],
        "inherited_openai_api_key_present": "OPENAI_API_KEY" in os.environ,
    }
    if "OPENAI_API_KEY" in os.environ:
        report["warning"] = (
            "inherited OPENAI_API_KEY is present and may take precedence over vault:OPENAI_CODEX"
        )
    if bridge == "off":
        return report
    if bridge != "openai-codex":
        report["status"] = "unsupported"
        return report
    if source_storage_dir is None:
        source_storage_dir = default_auth_storage_dir()
        if source_storage_dir is not None:
            report["source_storage_dir"] = str(source_storage_dir)
            report["source_storage_dir_discovery"] = "auto"
        else:
            report["status"] = "missing_source_storage_dir"
            report["missing_secret_names"] = ["OPENAI_CODEX"]
            return report
    else:
        report["source_storage_dir"] = str(source_storage_dir)
        report["source_storage_dir_discovery"] = "explicit"

    source_vault_path = storage_vault_path(source_storage_dir)
    target_vault_path = storage_vault_path(target_storage_dir)
    try:
        source_vault = read_json_object(source_vault_path)
    except (OSError, json.JSONDecodeError) as exc:
        _ = exc
        report["status"] = "source_unreadable"
        report["missing_secret_names"] = ["OPENAI_CODEX"]
        return report

    if "OPENAI_CODEX" not in source_vault:
        report["status"] = "missing"
        report["missing_secret_names"] = ["OPENAI_CODEX"]
        return report

    source_entry = source_vault["OPENAI_CODEX"]
    source_secret_type = secret_type_name(source_entry)
    report["source_secret_type"] = source_secret_type
    if source_secret_type != "oauth":
        report["status"] = "source_secret_type_mismatch"
        report["missing_secret_names"] = ["OPENAI_CODEX"]
        return report

    try:
        target_vault = read_json_object(target_vault_path)
    except FileNotFoundError:
        target_vault = {}
    except json.JSONDecodeError as exc:
        _ = exc
        report["status"] = "target_unreadable"
        return report

    target_vault["OPENAI_CODEX"] = source_entry
    write_json_object_secure(target_vault_path, target_vault)
    report["status"] = "copied"
    report["copied_secret_names"] = ["OPENAI_CODEX"]
    return report


def default_auth_storage_dir() -> Path | None:
    env_path = os.environ.get("FABRO_AUTH_STORAGE_DIR")
    if env_path:
        path = Path(env_path)
        if storage_vault_path(path).is_file():
            return path
    for path in DEFAULT_AUTH_STORAGE_CANDIDATES:
        if storage_vault_path(path).is_file():
            return path
    return None


def credential_preflight_report(
    *,
    fabro_bin: Path,
    env: dict[str, str],
    enabled: bool,
    provider: str | None,
    model: str | None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "enabled": enabled,
        "status": "skipped",
        "provider": provider,
        "model": model,
        "inherited_openai_api_key_present": "OPENAI_API_KEY" in env,
    }
    if "OPENAI_API_KEY" in env:
        report["warning"] = (
            "inherited OPENAI_API_KEY is present and may take precedence over vault:OPENAI_CODEX"
        )
    if not enabled:
        return report

    args = ["--no-upgrade-check", "model", "test"]
    if provider:
        args.extend(["--provider", provider])
    if model:
        args.extend(["--model", model])
    proc = run_fabro_command(fabro_bin, args, env=env, timeout=120)
    report.update(
        {
            "status": "passed" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
        }
    )
    return report


def storage_vault_path(storage_dir: Path) -> Path:
    return storage_dir / "vaults" / "default" / "secrets.json"


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise json.JSONDecodeError("expected JSON object", "", 0)
    return payload


def write_json_object_secure(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def secret_type_name(entry: Any) -> str | None:
    if isinstance(entry, dict):
        secret_type = entry.get("type")
        if isinstance(secret_type, str):
            return secret_type
    return None
