"""Offline-only guardrails for the first approved CALL-E smoke test.

This module validates a local scenario.  It deliberately contains no CALL-E
client, credential access, subprocess invocation, or network capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Callable, Mapping

from accessride.domain.mobility import RequirementCode
from accessride.integrations.calle import load_fixture_runs


_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
_FORBIDDEN = re.compile(r"\b(book(?:ing)?|reserv(?:e|ation)|dispatch|purchase)\b", re.IGNORECASE)
_MEDICAL = re.compile(r"\b(diagnos(?:is|ed)|medical|medication|condition|history)\b", re.IGNORECASE)
_REQUIRED_ENV = (
    "ACCESSRIDE_SMOKE_RECIPIENT_E164",
    "ACCESSRIDE_SMOKE_RECIPIENT_CONTROL_CONFIRMED",
    "ACCESSRIDE_SMOKE_OPERATOR_ID",
    "ACCESSRIDE_SMOKE_CALL_AUTHORIZATION",
)
_ALLOWED_REQUIREMENTS = frozenset({
    RequirementCode.VEHICLE_AVAILABILITY,
    RequirementCode.BOARDING_METHOD,
    RequirementCode.LIFT,
    RequirementCode.RAMP,
    RequirementCode.SECUREMENT,
    RequirementCode.WHEELCHAIR_COMPATIBILITY,
    RequirementCode.POWER_WHEELCHAIR_COMPATIBILITY,
    RequirementCode.PICKUP_AREA,
    RequirementCode.SERVICE_AREA,
    RequirementCode.ETA,
    RequirementCode.AUTHORIZATION_ROUTE,
    RequirementCode.PAYMENT_ROUTE,
})


@dataclass(frozen=True, slots=True)
class SmokeScenario:
    recipient_e164: str
    recipient_control_confirmed: bool
    operator_id: str
    call_authorization: str
    objective: str
    disclosure: str
    requirements: tuple[RequirementCode | str, ...]
    max_calls: int
    max_duration_seconds: int
    deadline_seconds: int
    max_polls: int
    poll_interval_seconds: int
    missing_environment_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreflightReport:
    ok: bool
    diagnostics: tuple[str, ...]
    live_execution_enabled: bool


def scenario_from_env(environ: Mapping[str, str]) -> SmokeScenario:
    """Read named configuration without ever returning values in diagnostics."""
    missing = tuple(name for name in _REQUIRED_ENV if not environ.get(name, "").strip())
    raw_requirements = tuple(item for item in environ.get("ACCESSRIDE_SMOKE_REQUIREMENTS", "").split(",") if item)
    requirements: tuple[RequirementCode | str, ...] = tuple(
        _requirement_or_raw(item) for item in raw_requirements
    )
    return SmokeScenario(
        recipient_e164=environ.get("ACCESSRIDE_SMOKE_RECIPIENT_E164", ""),
        recipient_control_confirmed=environ.get("ACCESSRIDE_SMOKE_RECIPIENT_CONTROL_CONFIRMED", "").lower() == "yes",
        operator_id=environ.get("ACCESSRIDE_SMOKE_OPERATOR_ID", ""),
        call_authorization=environ.get("ACCESSRIDE_SMOKE_CALL_AUTHORIZATION", ""),
        objective=environ.get("ACCESSRIDE_SMOKE_OBJECTIVE", ""),
        disclosure=environ.get("ACCESSRIDE_SMOKE_DISCLOSURE", ""),
        requirements=requirements,
        max_calls=_int(environ.get("ACCESSRIDE_SMOKE_MAX_CALLS"), 0),
        max_duration_seconds=_int(environ.get("ACCESSRIDE_SMOKE_MAX_DURATION_SECONDS"), 0),
        deadline_seconds=_int(environ.get("ACCESSRIDE_SMOKE_DEADLINE_SECONDS"), 0),
        max_polls=_int(environ.get("ACCESSRIDE_SMOKE_MAX_POLLS"), 0),
        poll_interval_seconds=_int(environ.get("ACCESSRIDE_SMOKE_POLL_INTERVAL_SECONDS"), 0),
        missing_environment_names=missing,
    )


def _int(value: str | None, default: int) -> int:
    try:
        return int(value or default)
    except ValueError:
        return default


def _requirement_or_raw(value: str) -> RequirementCode | str:
    try:
        return RequirementCode(value)
    except ValueError:
        return value


def validate_preflight(scenario: SmokeScenario, *, repo_root: Path, one_shot_operator_flag: bool = False,
                       git_status: Callable[[Path], str] | None = None) -> PreflightReport:
    """Validate the package without contacting CALL-E or examining secret values."""
    findings: list[str] = []
    status = (git_status or _git_status)(repo_root)
    if status.strip():
        findings.append("repository must be clean before a smoke test")
    try:
        load_fixture_runs(repo_root / "fixtures" / "calle_provider_verification.json")
    except Exception:  # Deliberately do not expose fixture content in a diagnostic.
        findings.append("fixture/demo health check failed")
    if scenario.missing_environment_names:
        findings.append("missing required configuration: " + ", ".join(scenario.missing_environment_names))
    if not _E164.fullmatch(scenario.recipient_e164):
        findings.append("recipient must use E.164 placeholder format")
    if not scenario.recipient_control_confirmed:
        findings.append("recipient control/consent confirmation is required")
    if not scenario.operator_id.strip():
        findings.append("operator identity is required")
    if scenario.call_authorization != "APPROVED_FOR_ONE_CALL":
        findings.append("explicit one-shot call authorization is required")
    if _FORBIDDEN.search(scenario.objective):
        findings.append("booking-like objective is prohibited")
    if _MEDICAL.search(scenario.disclosure):
        findings.append("medical or diagnosis disclosure is prohibited")
    if not scenario.requirements or any(item not in _ALLOWED_REQUIREMENTS for item in scenario.requirements):
        findings.append("requirement allowlist is enforced")
    elif len(set(scenario.requirements)) != len(scenario.requirements):
        findings.append("smoke-test requirement keys must be unique")
    if scenario.max_calls != 1:
        findings.append("first smoke test max_calls must equal 1")
    if not 30 <= scenario.max_duration_seconds <= 300:
        findings.append("call duration must be 30..300 seconds")
    if not 60 <= scenario.deadline_seconds <= 600:
        findings.append("deadline must be 60..600 seconds")
    if not 1 <= scenario.max_polls <= 10 or not 5 <= scenario.poll_interval_seconds <= 30:
        findings.append("poll bounds must be max_polls 1..10 and interval 5..30 seconds")
    if scenario.max_polls * scenario.poll_interval_seconds > scenario.deadline_seconds:
        findings.append("poll schedule must fit inside deadline")
    if not one_shot_operator_flag:
        findings.append("live execution remains disabled without --operator-one-shot-approval")
    return PreflightReport(not findings, tuple(findings), one_shot_operator_flag and not findings)


def _git_status(repo_root: Path) -> str:
    completed = subprocess.run(("git", "status", "--porcelain"), cwd=repo_root, text=True,
                               capture_output=True, check=False)
    return completed.stdout if completed.returncode == 0 else "unavailable"
