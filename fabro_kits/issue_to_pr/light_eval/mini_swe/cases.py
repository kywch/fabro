"""Case catalog for mini-SWE lightweight evals."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..task_schema import MiniSweCase


SUPPORTED_ATTEMPTS = ("scripted", "workflow-slice", "model")
SUPPORTED_CREDENTIAL_BRIDGES = ("off", "openai-codex")
SUPPORTED_SUBSTRATES = ("local", "docker")
SUPPORTED_SUITES = ("dev", "locked", "shadow", "all")
PUBLIC_TEST_COMMAND_ID = "cmd-001"


@dataclass(frozen=True)
class MiniSweCaseBehavior:
    """Derived behavior knobs shared by mini-SWE runners and artifacts."""

    change_source: bool
    change_test: bool
    public_test_expected: str
    public_test_extra_name: str | None
    hidden_oracle_expected: str
    include_public_test_command_id: bool
    no_test_justification: str | None

    @property
    def public_test_command_id(self) -> str | None:
        if not self.include_public_test_command_id:
            return None
        return PUBLIC_TEST_COMMAND_ID

KNOWN_MINI_SWE_CASES = (
    MiniSweCase(
        case_id="good-source-plus-test",
        family="positive",
        suite="dev",
        issue_text=(
            "Fix `greeting(name)` so it returns `hello, <name>` and add a "
            "regression test for the comma."
        ),
        expected_files=("src/greeting.py",),
        allowed_test_files=("tests/test_greeting.py",),
        requires_test_change=True,
        expected_decision_hint="export",
    ),
    MiniSweCase(
        case_id="runtime-proof-honesty",
        family="evidence",
        suite="dev",
        issue_text=(
            "Fix `greeting(name)` so it returns `hello, <name>`, but do not "
            "treat runtime proof as valid unless validation cites an observed "
            "command ID."
        ),
        expected_files=("src/greeting.py",),
        allowed_test_files=("tests/test_greeting.py",),
        requires_test_change=True,
        expected_decision_hint="fixup",
    ),
    MiniSweCase(
        case_id="good-source-existing-test",
        family="positive",
        suite="dev",
        issue_text=(
            "Fix `greeting(name)` so it returns `hello, <name>`. The existing "
            "test already covers the expected behavior, so a source-only patch "
            "is acceptable."
        ),
        expected_files=("src/greeting.py",),
        allowed_test_files=(),
        requires_test_change=False,
        expected_decision_hint="export",
    ),
    MiniSweCase(
        case_id="good-test-only",
        family="positive",
        suite="dev",
        issue_text=(
            "Add regression coverage for the existing `greeting(name)` behavior. "
            "The source implementation is already correct for this task."
        ),
        expected_files=(),
        allowed_test_files=("tests/test_greeting.py",),
        requires_test_change=True,
        expected_decision_hint="export",
    ),
    MiniSweCase(
        case_id="overblocking-good-patch-with-minor-risk",
        family="review_moderation",
        suite="dev",
        issue_text=(
            "Fix `greeting(name)` so it returns `hello, <name>` and add a "
            "regression test. A review may raise a minor naming concern, but "
            "it should not block export when the concern is accounted for."
        ),
        expected_files=("src/greeting.py",),
        allowed_test_files=("tests/test_greeting.py",),
        requires_test_change=True,
        expected_decision_hint="export",
    ),
)


SUPPORTED_CASE_IDS_BY_ATTEMPT = {
    "scripted": tuple(case.case_id for case in KNOWN_MINI_SWE_CASES),
    "workflow-slice": tuple(case.case_id for case in KNOWN_MINI_SWE_CASES),
    "model": tuple(case.case_id for case in KNOWN_MINI_SWE_CASES),
}


def case_behavior(case: MiniSweCase) -> MiniSweCaseBehavior:
    """Return derived behavior for a generated mini-SWE case."""
    is_test_only = case.case_id == "good-test-only"
    return MiniSweCaseBehavior(
        change_source=not is_test_only,
        change_test=bool(case.allowed_test_files),
        public_test_expected="hello Ada" if is_test_only else "hello, Ada",
        public_test_extra_name="Grace" if is_test_only else None,
        hidden_oracle_expected="hello Grace" if is_test_only else "hello, Grace",
        include_public_test_command_id=case.case_id != "runtime-proof-honesty",
        no_test_justification=(
            "Existing test coverage already asserts the requested greeting behavior."
            if not case.allowed_test_files
            else None
        ),
    )


def initial_public_test_expected(case: MiniSweCase) -> str:
    """Return the expected value in the generated repo's initial public test."""
    if case.case_id == "good-source-existing-test":
        return "hello, Ada"
    return "hello Ada"


def greeting_test_text(expected: str, *, extra_name: str | None = None) -> str:
    """Return the generated greeting unittest module text."""
    text = (
        "import unittest\n\n"
        "from src.greeting import greeting\n\n\n"
        "class GreetingTest(unittest.TestCase):\n"
        "    def test_greeting_uses_comma(self):\n"
        f"        self.assertEqual(greeting(\"Ada\"), {expected!r})\n"
    )
    if extra_name:
        text += (
            "\n"
            "    def test_greeting_covers_another_name(self):\n"
            f"        self.assertEqual(greeting({extra_name!r}), 'hello {extra_name}')\n"
        )
    return text


def public_test_text_for_case(case: MiniSweCase) -> str:
    """Return the public test text written by a successful case attempt."""
    behavior = case_behavior(case)
    return greeting_test_text(
        behavior.public_test_expected,
        extra_name=behavior.public_test_extra_name,
    )


def tests_added_for_case(case: MiniSweCase) -> list[dict[str, str]]:
    """Return validation-contract test additions for a case."""
    return [
        {
            "path": path,
            "description": "regression coverage for comma in greeting",
        }
        for path in case.allowed_test_files
    ]


def validate_mini_swe_options(
    *,
    attempt: str,
    substrate: str,
    credential_bridge: str,
    credential_preflight: bool,
    auth_storage_dir: Path | None,
) -> None:
    """Validate cross-cutting mini-SWE runner options."""
    if attempt not in SUPPORTED_ATTEMPTS:
        raise SystemExit(f"mini-swe attempt not implemented yet: {attempt}")
    if substrate not in SUPPORTED_SUBSTRATES:
        raise SystemExit(f"mini-swe substrate not implemented yet: {substrate}")
    if substrate == "docker" and attempt != "scripted":
        raise SystemExit("mini-swe docker substrate is only implemented for scripted attempts")
    if credential_bridge not in SUPPORTED_CREDENTIAL_BRIDGES:
        raise SystemExit(f"unknown mini-swe credential bridge: {credential_bridge}")
    if credential_bridge != "off" and attempt != "model":
        raise SystemExit("--credential-bridge is only supported with --attempt model")
    if credential_preflight and attempt != "model":
        raise SystemExit("--credential-preflight is only supported with --attempt model")
    if credential_bridge == "openai-codex" and auth_storage_dir is None:
        raise SystemExit("--auth-storage-dir is required with --credential-bridge openai-codex")


def ensure_mini_swe_case_supported(case: MiniSweCase, *, attempt: str) -> None:
    """Validate that an attempt runner supports a case."""
    supported_cases = SUPPORTED_CASE_IDS_BY_ATTEMPT.get(attempt)
    if supported_cases is None:
        raise SystemExit(f"mini-swe attempt not implemented yet: {attempt}")
    if case.case_id not in supported_cases:
        raise SystemExit(f"mini-swe {attempt} case not implemented yet: {case.case_id}")


def list_mini_swe_cases(case: str, *, suite: str = "all") -> list[MiniSweCase]:
    """Return selected mini-SWE case definitions."""
    if suite not in SUPPORTED_SUITES:
        raise SystemExit(f"unknown mini-swe suite: {suite}")
    cases = [
        known
        for known in KNOWN_MINI_SWE_CASES
        if suite == "all" or known.suite == suite
    ]
    if case == "all":
        return cases
    for known in cases:
        if known.case_id == case:
            return [known]
    suite_suffix = "" if suite == "all" else f" in suite {suite}"
    raise SystemExit(f"unknown mini-swe case: {case}{suite_suffix}")
