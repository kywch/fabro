"""Lightweight issue-to-PR eval package facade."""

from __future__ import annotations

from .cli import main
from .mini_swe import list_mini_swe_cases, run_mini_swe
from .paths import list_fixtures
from .replay import run_replay
from .synthetic import list_synthetic_tasks, run_synthetic

__all__ = [
    "list_fixtures",
    "list_mini_swe_cases",
    "list_synthetic_tasks",
    "main",
    "run_mini_swe",
    "run_replay",
    "run_synthetic",
]
