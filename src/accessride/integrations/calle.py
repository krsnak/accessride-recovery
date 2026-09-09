"""Fixture-first CALL-E verification boundary; it never places a phone call."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock
from typing import Protocol, TYPE_CHECKING
from uuid import uuid4

from accessride.domain.evidence import CapabilityEvidence, EvidenceValue
from accessride.domain.mobility import MobilityRequirement, RequirementCode, transport_disclosure
from accessride.domain.states import AssertionState

if TYPE_CHECKING:
    from accessride.services.orchestration import RecoveryOrchestrator


class CalleRunStatus(StrEnum):
    PLANNED = "PLANNED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CalleFixtureError(ValueError):
    """A fixture cannot safely be interpreted as a verification result."""


class LiveCalleUnavailableError(RuntimeError):
    """Live CALL-E execution is deliberately unavailable in this repository."""


_BOOLEAN_CODES = frozenset({RequirementCode.VEHICLE_AVAILABILITY, RequirementCode.WHEELCHAIR_COMPATIBILITY,
    RequirementCode.POWER_WHEELCHAIR_COMPATIBILITY, RequirementCode.LIFT, RequirementCode.RAMP,
    RequirementCode.SECUREMENT, RequirementCode.SERVICE_ANIMAL})
_NUMERIC_CODES = frozenset({RequirementCode.ETA, RequirementCode.WIDTH_CAPACITY, RequirementCode.LENGTH_CAPACITY,
    RequirementCode.WEIGHT_CAPACITY, RequirementCode.ARRIVAL_DEADLINE})


@dataclass(frozen=True, slots=True)
class CalleVerificationRequest:
    incident_id: str
    provider_id: str
    provider_attempt_id: str
    idempotency_key: str
    requirements: tuple[MobilityRequirement, ...]
    requested_at: datetime

    def __post_init__(self) -> None:
        for value, label in ((self.incident_id, "incident id"), (self.provider_id, "provider id"),
                             (self.provider_attempt_id, "provider attempt id"), (self.idempotency_key, "idempotency key")):
            if not value.strip():
                raise ValueError(f"{label} is required")
        if self.requested_at.tzinfo is None or self.requested_at.utcoffset() is None:
            raise ValueError("requested_at must be timezone-aware")
        if len({item.code for item in self.requirements}) != len(self.requirements):
            raise ValueError("CALL-E request requirement keys must be unique")


@dataclass(frozen=True, slots=True)
class CalleCallPlan:
    plan_id: str
    incident_id: str
    provider_id: str
    provider_attempt_id: str
    idempotency_key: str
    requirements_snapshot: tuple[MobilityRequirement, ...]
    disclosure: tuple[str, ...]
    planned_at: datetime

    def __post_init__(self) -> None:
        for value, label in ((self.plan_id, "plan id"), (self.incident_id, "incident id"), (self.provider_id, "provider id"),
                             (self.provider_attempt_id, "provider attempt id"), (self.idempotency_key, "idempotency key")):
            if not value.strip():
                raise CalleFixtureError(f"{label} is required")
        if self.planned_at.tzinfo is None or self.planned_at.utcoffset() is None:
            raise CalleFixtureError("plan time must be timezone-aware")
        if self.disclosure != transport_disclosure(self.requirements_snapshot):
            raise CalleFixtureError("plan disclosure must match its immutable requirement snapshot")


@dataclass(frozen=True, slots=True)
class CalleVerificationAssertion:
    requirement_code: RequirementCode
    assertion: AssertionState
    value: EvidenceValue | None
    observed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        try:
            code = RequirementCode(self.requirement_code)
        except ValueError as error:
            raise CalleFixtureError("assertion requirement code is not an allowed operational key") from error
        if not isinstance(self.assertion, AssertionState):
            raise CalleFixtureError("assertion state must be typed")
        for value, label in ((self.observed_at, "observed_at"), (self.expires_at, "expires_at")):
            if value.tzinfo is None or value.utcoffset() is None:
                raise CalleFixtureError(f"assertion {label} must be timezone-aware")
        if self.observed_at >= self.expires_at:
            raise CalleFixtureError("assertion observed_at must precede expires_at")
        if self.assertion is AssertionState.UNKNOWN and self.value is not None:
            raise CalleFixtureError("UNKNOWN assertion cannot carry a positive or negative value")
        if code in _BOOLEAN_CODES:
            if self.value is not None and type(self.value) is not bool:
                raise CalleFixtureError("boolean capability assertions require boolean values")
            expected = {AssertionState.VERIFIED: True, AssertionState.INCOMPATIBLE: False,
                        AssertionState.UNKNOWN: None}[self.assertion]
            if self.value is not expected:
                raise CalleFixtureError("boolean assertion state and value are inconsistent")
        elif code in _NUMERIC_CODES:
            if self.value is not None and type(self.value) not in (int, float):
                raise CalleFixtureError("numeric operational facts require numeric values")
            if self.assertion is not AssertionState.UNKNOWN and self.value is None:
                raise CalleFixtureError("numeric asserted facts require a value")
        else:
            if self.value is not None and type(self.value) is not str:
                raise CalleFixtureError("text operational facts require text values")
            if self.assertion is not AssertionState.UNKNOWN and not self.value:
                raise CalleFixtureError("text asserted facts require a value")


@dataclass(frozen=True, slots=True)
class CalleCallRun:
    run_id: str
    plan_id: str
    incident_id: str
    provider_id: str
    provider_attempt_id: str
    idempotency_key: str
    status: CalleRunStatus
    assertions: tuple[CalleVerificationAssertion, ...]
    provenance: str
    started_at: datetime
    completed_at: datetime | None

    def __post_init__(self) -> None:
        for value, label in ((self.run_id, "run id"), (self.plan_id, "plan id"), (self.incident_id, "incident id"),
                             (self.provider_id, "provider id"), (self.provider_attempt_id, "provider attempt id"),
                             (self.idempotency_key, "idempotency key"), (self.provenance, "provenance")):
            if not value.strip():
                raise CalleFixtureError(f"{label} is required")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise CalleFixtureError("run started_at must be timezone-aware")
        if self.completed_at is not None and (self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None):
            raise CalleFixtureError("run completed_at must be timezone-aware")
        if self.status is CalleRunStatus.COMPLETED:
            if self.completed_at is None or self.completed_at < self.started_at:
                raise CalleFixtureError("completed run requires completion at or after start")
        elif self.assertions or self.completed_at is not None:
            raise CalleFixtureError("only completed runs may contain assertions or completion time")
        codes = [item.requirement_code for item in self.assertions]
        if len(codes) != len(set(codes)):
            raise CalleFixtureError("ambiguous fixture contains multiple assertions for one requirement")

    def to_evidence(self, request: CalleVerificationRequest) -> tuple[CapabilityEvidence, ...]:
        raise CalleFixtureError("CALL-E evidence conversion requires adapter-issued authority")


@dataclass(frozen=True, slots=True, eq=False)
class CalleCallAuthorization:
    """Single-use operator authorization for a future live phone call."""
    authorization_id: str
    incident_id: str
    provider_id: str
    provider_attempt_id: str
    plan_id: str
    operator_id: str
    approved_at: datetime
    expires_at: datetime


class CalleCallAuthorizationGate:
    """Separate gate for live phone execution; it is not a handoff approval."""
    def __init__(self) -> None:
        self._issued: dict[str, CalleCallAuthorization] = {}
        self._used: set[str] = set()
        self._lock = RLock()

    def approve_call(self, plan: CalleCallPlan, operator_id: str, *, at: datetime,
                     expires_in: timedelta = timedelta(minutes=5)) -> CalleCallAuthorization:
        if not operator_id.strip() or expires_in <= timedelta(0):
            raise ValueError("operator identity and positive call authorization lifetime are required")
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("call authorization time must be timezone-aware")
        authorization = CalleCallAuthorization(str(uuid4()), plan.incident_id, plan.provider_id, plan.provider_attempt_id,
                                               plan.plan_id, operator_id, at, at + expires_in)
        with self._lock:
            self._issued[authorization.authorization_id] = authorization
        return authorization

    def validate(self, authorization: CalleCallAuthorization, plan: CalleCallPlan, *, at: datetime) -> None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("call execution time must be timezone-aware")
        with self._lock:
            self._validate_locked(authorization, plan, at=at)

    def consume(self, authorization: CalleCallAuthorization, plan: CalleCallPlan, *, at: datetime) -> None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("call execution time must be timezone-aware")
        with self._lock:
            self._validate_locked(authorization, plan, at=at)
            self._used.add(authorization.authorization_id)

    def _validate_locked(self, authorization: CalleCallAuthorization, plan: CalleCallPlan, *, at: datetime) -> None:
        """Validate while the caller holds ``_lock``."""
        if self._issued.get(authorization.authorization_id) is not authorization:
            raise PermissionError("call authorization was not issued by this gate")
        if authorization.authorization_id in self._used or at >= authorization.expires_at:
            raise PermissionError("call authorization is used or expired")
        if (authorization.incident_id, authorization.provider_id, authorization.provider_attempt_id, authorization.plan_id) != (
                plan.incident_id, plan.provider_id, plan.provider_attempt_id, plan.plan_id):
            raise PermissionError("call authorization does not match this plan")


class CalleVerificationAdapter(Protocol):
    def plan_call(self, request: CalleVerificationRequest, authority: object) -> CalleCallPlan: ...
    def run_call(self, plan: CalleCallPlan) -> CalleCallRun: ...
    def evidence_for(self, run: CalleCallRun, request: CalleVerificationRequest) -> tuple[CapabilityEvidence, ...]: ...
    def get_call_run(self, run_id: str) -> CalleCallRun: ...


class CalleAdapter(Protocol):
    def prepare_operator_brief(self, incident_id: str, provider_id: str) -> str: ...


def _plan_id(request: CalleVerificationRequest) -> str:
    material = "\x1f".join((request.incident_id, request.provider_id, request.provider_attempt_id,
                             request.idempotency_key, request.requested_at.isoformat(), *transport_disclosure(request.requirements)))
    return f"fixture-plan:{sha256(material.encode()).hexdigest()}"


@dataclass(frozen=True, slots=True)
class FixtureCalleAdapter:
    """Thread-safe deterministic local results; no call, booking, or handoff exists here."""
    fixtures: tuple[CalleCallRun, ...]
    orchestrator: "RecoveryOrchestrator"
    fixture_label: str = "demo"
    _plans: dict[tuple[str, str], CalleCallPlan] = field(default_factory=dict, init=False, repr=False, compare=False)
    _runs_by_plan: dict[str, CalleCallRun] = field(default_factory=dict, init=False, repr=False, compare=False)
    _runs_by_id: dict[str, CalleCallRun] = field(default_factory=dict, init=False, repr=False, compare=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Import here avoids an integration/service import cycle while ensuring
        # this fixture boundary cannot be wired to a duck-typed allow-all.
        from accessride.services.orchestration import RecoveryOrchestrator
        if type(self.orchestrator) is not RecoveryOrchestrator:
            raise TypeError("FixtureCalleAdapter requires a concrete RecoveryOrchestrator")
        run_ids = [item.run_id for item in self.fixtures]
        plan_ids = [item.plan_id for item in self.fixtures]
        correlations = [(item.incident_id, item.provider_id, item.provider_attempt_id, item.idempotency_key) for item in self.fixtures]
        if len(run_ids) != len(set(run_ids)) or len(plan_ids) != len(set(plan_ids)) or len(correlations) != len(set(correlations)):
            raise CalleFixtureError("fixture run, plan, and correlation identities must be unique")

    def prepare_operator_brief(self, incident_id: str, provider_id: str) -> str:
        return f"[{self.fixture_label}] operator brief for incident {incident_id}, provider {provider_id}"

    def plan_call(self, request: CalleVerificationRequest, authority: object) -> CalleCallPlan:
        self.orchestrator.validate_calle_plan_authority(authority, request)
        key = (request.incident_id, request.idempotency_key)
        with self._lock:
            existing = self._plans.get(key)
            if existing is not None:
                if (existing.provider_id, existing.provider_attempt_id, existing.requirements_snapshot, existing.planned_at) != (
                        request.provider_id, request.provider_attempt_id, request.requirements, request.requested_at):
                    raise CalleFixtureError("idempotency key is bound to another immutable request snapshot")
                return existing
            plan = CalleCallPlan(_plan_id(request), request.incident_id, request.provider_id, request.provider_attempt_id,
                                 request.idempotency_key, request.requirements, transport_disclosure(request.requirements), request.requested_at)
            self._plans[key] = plan
            return plan

    def run_call(self, plan: CalleCallPlan) -> CalleCallRun:
        with self._lock:
            known = self._plans.get((plan.incident_id, plan.idempotency_key))
            if known is not plan:
                raise CalleFixtureError("CALL-E plan was not issued by this fixture adapter")
            existing = self._runs_by_plan.get(plan.plan_id)
            if existing is not None:
                return existing
            matching = tuple(item for item in self.fixtures if (item.incident_id, item.provider_id, item.provider_attempt_id,
                         item.idempotency_key) == (plan.incident_id, plan.provider_id, plan.provider_attempt_id, plan.idempotency_key))
            if len(matching) != 1:
                raise CalleFixtureError("fixture requires exactly one result for the planned provider attempt")
            run = matching[0]
            if run.plan_id != plan.plan_id:
                raise CalleFixtureError("fixture result plan id does not match issued plan")
            self._validate_run_lifecycle(run, plan)
            requested_codes = {item.code for item in plan.requirements_snapshot}
            if any(item.requirement_code not in requested_codes for item in run.assertions):
                raise CalleFixtureError("fixture assertions must be a subset of the request requirement snapshot")
            self._runs_by_plan[plan.plan_id] = run
            self._runs_by_id[run.run_id] = run
            return run

    @staticmethod
    def _validate_run_lifecycle(run: CalleCallRun, plan: CalleCallPlan) -> None:
        if run.started_at < plan.planned_at:
            raise CalleFixtureError("run cannot start before its request was planned")
        if run.completed_at is None:
            raise CalleFixtureError("completed fixture run requires completion metadata")
        for assertion in run.assertions:
            if assertion.observed_at < run.started_at or assertion.observed_at > run.completed_at:
                raise CalleFixtureError("assertion observation must occur within the actual run lifecycle")

    def evidence_for(self, run: CalleCallRun, request: CalleVerificationRequest) -> tuple[CapabilityEvidence, ...]:
        with self._lock:
            known = self._runs_by_id.get(run.run_id)
            if known is not run:
                raise CalleFixtureError("CALL-E evidence requires an adapter-issued registered run")
            plan = self._plans.get((request.incident_id, request.idempotency_key))
            if plan is None or self._runs_by_plan.get(plan.plan_id) is not run:
                raise CalleFixtureError("CALL-E evidence run is not bound to this request")
            if (run.incident_id, run.provider_id, run.provider_attempt_id, run.idempotency_key) != (
                    request.incident_id, request.provider_id, request.provider_attempt_id, request.idempotency_key):
                raise CalleFixtureError("CALL-E result does not match its incident/provider attempt correlation")
            if request.requirements != plan.requirements_snapshot or request.requested_at != plan.planned_at:
                raise CalleFixtureError("CALL-E evidence request snapshot differs from issued plan")
            return tuple(CapabilityEvidence(f"{run.run_id}:{item.requirement_code.value}", run.provider_id,
                item.requirement_code, item.assertion, item.value, item.observed_at, item.expires_at, run.provenance)
                for item in run.assertions) if run.status is CalleRunStatus.COMPLETED else ()

    def get_call_run(self, run_id: str) -> CalleCallRun:
        with self._lock:
            try:
                return self._runs_by_id[run_id]
            except KeyError as error:
                raise CalleFixtureError("CALL-E run is not available from this fixture adapter") from error


class LiveCalleAdapter:
    """Fail-closed placeholder. A future implementation must validate call authorization."""
    def __init__(self, call_authorizations: CalleCallAuthorizationGate) -> None:
        self._call_authorizations = call_authorizations

    @staticmethod
    def _unavailable() -> None:
        raise LiveCalleUnavailableError("live CALL-E is not implemented; no external call was attempted")

    def plan_call(self, request: CalleVerificationRequest, authority: object) -> CalleCallPlan:
        self._unavailable()

    def run_call(self, plan: CalleCallPlan, authorization: CalleCallAuthorization, *, at: datetime) -> CalleCallRun:
        # Consume immediately before the (future) external execution boundary.
        # Even this unavailable stub models an attempted live execution, so a
        # retry requires a newly issued, explicit operator authorization.
        self._call_authorizations.consume(authorization, plan, at=at)
        self._unavailable()

    def get_call_run(self, run_id: str) -> CalleCallRun:
        self._unavailable()


def load_fixture_runs(path: Path) -> tuple[CalleCallRun, ...]:
    """Load strict local JSON fixtures into typed results; no network access occurs."""
    try:
        entries = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CalleFixtureError("CALL-E fixture cannot be loaded") from error
    if not isinstance(entries, list):
        raise CalleFixtureError("CALL-E fixture root must be a list")
    runs = []
    for entry in entries:
        try:
            assertions = tuple(CalleVerificationAssertion(RequirementCode(item["requirement_code"]),
                AssertionState(item["assertion"]), item.get("value"), datetime.fromisoformat(item["observed_at"]),
                datetime.fromisoformat(item["expires_at"])) for item in entry["assertions"])
            runs.append(CalleCallRun(entry["run_id"], entry["plan_id"], entry["incident_id"], entry["provider_id"],
                entry["provider_attempt_id"], entry["idempotency_key"], CalleRunStatus(entry["status"]), assertions,
                entry["provenance"], datetime.fromisoformat(entry["started_at"]),
                datetime.fromisoformat(entry["completed_at"]) if entry.get("completed_at") else None))
        except (KeyError, TypeError, ValueError) as error:
            raise CalleFixtureError("CALL-E fixture entry is malformed") from error
    run_ids = [item.run_id for item in runs]
    plan_ids = [item.plan_id for item in runs]
    correlations = [(item.incident_id, item.provider_id, item.provider_attempt_id, item.idempotency_key)
                    for item in runs]
    if len(run_ids) != len(set(run_ids)) or len(plan_ids) != len(set(plan_ids)) or len(correlations) != len(set(correlations)):
        raise CalleFixtureError("fixture run, plan, and correlation identities must be unique")
    return tuple(runs)
