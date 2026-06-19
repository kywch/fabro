"""Shared schema objects for mini-SWE lightweight evals."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol


AttemptOrigin = Literal["scripted", "workflow-slice", "model"]
ArtifactOrigin = Literal["fixture", "workflow_stage", "model_workflow"]
Substrate = Literal["local", "docker"]

MINI_SWE_DATASET = "fabro-kits/issue-to-pr-mini-swe"


@dataclass(frozen=True)
class MiniSweCase:
    """Minimal generated-repo task definition for the first mini-SWE milestone."""

    case_id: str
    family: str
    suite: str
    issue_text: str
    expected_files: tuple[str, ...] = ()
    allowed_extra_files: tuple[str, ...] = ()
    allowed_test_files: tuple[str, ...] = ()
    forbidden_files: tuple[str, ...] = ()
    requires_test_change: bool | str = "case_specific"
    expected_decision_hint: str = "export"


@dataclass(frozen=True)
class AttemptResult:
    """Result returned by one mini-SWE attempt runner."""

    attempt_origin: AttemptOrigin
    artifact_origin: ArtifactOrigin
    substrate: Substrate
    source: dict[str, Any]
    b2_slice_eligible: bool
    b2_model_eligible: bool
    b2_eligible: bool
    eligibility_failures: tuple[str, ...]
    patch_path: Path | None = None
    artifact_paths: dict[str, str] = field(default_factory=dict)
    commands_run_path: Path | None = None
    trajectory_path: Path | None = None
    transcript_path: Path | None = None
    dump_path: Path | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def eval_metadata(self) -> dict[str, Any]:
        """Return JSON-compatible eval provenance metadata."""
        return {
            "attempt_origin": self.attempt_origin,
            "artifact_origin": self.artifact_origin,
            "substrate": self.substrate,
            "b2_slice_eligible": self.b2_slice_eligible,
            "b2_model_eligible": self.b2_model_eligible,
            "b2_eligible": self.b2_eligible,
            "eligibility_failures": list(self.eligibility_failures),
            "artifact_paths": self.artifact_paths,
            "commands_run_path": self.commands_run_path.as_posix()
            if self.commands_run_path
            else None,
            "trajectory_path": self.trajectory_path.as_posix()
            if self.trajectory_path
            else None,
            "transcript_path": self.transcript_path.as_posix()
            if self.transcript_path
            else None,
            "dump_path": self.dump_path.as_posix() if self.dump_path else None,
            "provenance": self.provenance,
        }


class AttemptRunner(Protocol):
    """Protocol for scripted, workflow-slice, and model mini-SWE attempts."""

    name: AttemptOrigin

    def run(self, case: MiniSweCase, repo_dir: Path, work_dir: Path) -> AttemptResult:
        """Run one attempt against a generated repo."""


def mini_swe_source(case: MiniSweCase) -> dict[str, str]:
    """Return stable source identity for mini-SWE tasks."""
    return {
        "kind": "mini_swe",
        "external_id": case.case_id,
        "dataset": MINI_SWE_DATASET,
        "split": case.suite,
    }
