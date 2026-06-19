"""Paths and constants for lightweight issue-to-PR evals."""

from __future__ import annotations

from pathlib import Path


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tier1_artifact_replay"
DEFAULT_SYNTHETIC_DOCKER_IMAGE = "sweb.base.x86_64:latest"


def list_fixtures(fixture: str) -> list[Path]:
    if fixture == "all":
        return sorted(
            path
            for path in FIXTURE_ROOT.iterdir()
            if path.is_dir() and (path / "expected.json").exists()
        )
    path = FIXTURE_ROOT / fixture
    if not path.is_dir():
        raise SystemExit(f"unknown replay fixture: {fixture}")
    return [path]
