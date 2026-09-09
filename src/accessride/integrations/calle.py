"""Fixture-first CALL-E verification boundary; it never places a phone call."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock
from typing import Callable, Mapping, Protocol, TYPE_CHECKING
from uuid import uuid4

from accessride.domain.evidence import CapabilityEvidence, EvidenceValue
from accessride.domain.mobility import MobilityRequirement, RequirementCode, transport_disclosure
from accessride.domain.states import AssertionState

if TYPE_CHECKING:
    from accessride.services.orchestration import RecoveryOrchestrator


class CalleRunStatus(StrEnum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CalleFixtureError(ValueError):
    """A fixture cannot safely be interpreted as a verification result."""


class LiveCalleUnavailableError(RuntimeError):
    """Live CALL-E execution is deliberately unavailable in this repository."""


class CalleTransportError(ValueError):
    """An untrusted CALL-E transport response cannot be safely correlated."""


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
        elif self.status is CalleRunStatus.FAILED:
            if self.assertions:
                raise CalleFixtureError("only completed runs may contain assertions")
            if self.completed_at is not None and self.completed_at < self.started_at:
                raise CalleFixtureError("failed run completion cannot predate start")
        elif self.assertions or self.completed_at is not None:
            raise CalleFixtureError("only terminal runs may contain assertions or completion time")
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


class CalleTransport(Protocol):
    """Narrow untrusted boundary around future CALL-E plan/run/status calls.

    Values returned by this interface are never evidence and must be parsed by
    ``LiveCalleAdapter`` before entering the typed domain boundary.
    """
    def plan_call(self, request: Mapping[str, object]) -> Mapping[str, object]: ...
    def run_call(self, plan: Mapping[str, object]) -> Mapping[str, object]: ...
    def get_call_run(self, run_id: str) -> Mapping[str, object]: ...


@dataclass(slots=True)
class InMemoryCalleTransport:
    """Scripted local transport used by contract tests; it has no network path."""
    plan_responses: list[Mapping[str, object]] = field(default_factory=list)
    run_responses: list[Mapping[str, object]] = field(default_factory=list)
    status_responses: dict[str, list[Mapping[str, object]]] = field(default_factory=dict)
    plan_requests: list[Mapping[str, object]] = field(default_factory=list, init=False)
    run_requests: list[Mapping[str, object]] = field(default_factory=list, init=False)

    @staticmethod
    def _next(items: list[Mapping[str, object]], operation: str) -> Mapping[str, object]:
        if not items:
            raise CalleTransportError(f"mock transport has no {operation} response")
        return items.pop(0)

    def plan_call(self, request: Mapping[str, object]) -> Mapping[str, object]:
        self.plan_requests.append(dict(request))
        return self._next(self.plan_responses, "plan")

    def run_call(self, plan: Mapping[str, object]) -> Mapping[str, object]:
        self.run_requests.append(dict(plan))
        return self._next(self.run_responses, "run")

    def get_call_run(self, run_id: str) -> Mapping[str, object]:
        responses = self.status_responses.get(run_id)
        if not responses:
            raise CalleTransportError("mock transport has no status response for run")
        return responses.pop(0)


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
    """Fail-closed CALL-E adapter over an explicitly supplied transport.

    This class deliberately does not construct a CLI, read credentials, or
    discover CALL-E.  Its generic transport envelope is documented in
    ``docs/architecture.md`` and must be proven against a live smoke test.
    """
    def __init__(self, call_authorizations: CalleCallAuthorizationGate,
                 orchestrator: "RecoveryOrchestrator | None" = None,
                 transport: CalleTransport | None = None) -> None:
        self._call_authorizations = call_authorizations
        self._orchestrator = orchestrator
        self._transport = transport
        self._plans: dict[tuple[str, str], CalleCallPlan] = {}
        self._runs: dict[str, CalleCallRun] = {}
        self._lock = RLock()

    def _require_transport(self) -> CalleTransport:
        if self._transport is None:
            raise LiveCalleUnavailableError("live CALL-E transport is not configured; no external call was attempted")
        return self._transport

    @staticmethod
    def _time(value: object, field_name: str, *, optional: bool = False) -> datetime | None:
        if value is None and optional:
            return None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as error:
                raise CalleTransportError(f"transport {field_name} is not an ISO timestamp") from error
        else:
            raise CalleTransportError(f"transport {field_name} is required")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise CalleTransportError(f"transport {field_name} must be timezone-aware")
        return parsed

    @staticmethod
    def _text(payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise CalleTransportError(f"transport {key} is required")
        return value

    @staticmethod
    def _request_payload(request: CalleVerificationRequest) -> dict[str, object]:
        return {"incident_id": request.incident_id, "provider_id": request.provider_id,
                "provider_attempt_id": request.provider_attempt_id, "idempotency_key": request.idempotency_key,
                "requirements": tuple(item.code.value for item in request.requirements),
                "disclosure": transport_disclosure(request.requirements), "requested_at": request.requested_at.isoformat()}

    @classmethod
    def _parse_plan(cls, payload: Mapping[str, object], request: CalleVerificationRequest) -> CalleCallPlan:
        expected = cls._request_payload(request)
        for key in ("incident_id", "provider_id", "provider_attempt_id", "idempotency_key", "requirements", "disclosure", "requested_at"):
            actual = payload.get(key)
            if key in {"requirements", "disclosure"} and isinstance(actual, (list, tuple)):
                actual = tuple(actual)
            if actual != expected[key]:
                raise CalleTransportError(f"transport plan {key} does not exactly match request")
        return CalleCallPlan(cls._text(payload, "plan_id"), request.incident_id, request.provider_id,
            request.provider_attempt_id, request.idempotency_key, request.requirements,
            transport_disclosure(request.requirements), cls._time(payload.get("planned_at"), "planned_at"))

    @classmethod
    def _parse_assertions(cls, raw: object) -> tuple[CalleVerificationAssertion, ...]:
        if not isinstance(raw, (list, tuple)):
            raise CalleTransportError("transport assertions must be a list")
        assertions = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise CalleTransportError("transport assertion is malformed")
            try:
                assertions.append(CalleVerificationAssertion(RequirementCode(cls._text(item, "requirement_code")),
                    AssertionState(cls._text(item, "assertion")), item.get("value"),
                    cls._time(item.get("observed_at"), "observed_at"), cls._time(item.get("expires_at"), "expires_at")))
            except (ValueError, CalleFixtureError) as error:
                raise CalleTransportError("transport assertion is invalid") from error
        return tuple(assertions)

    @classmethod
    def _parse_run(cls, payload: Mapping[str, object], plan: CalleCallPlan) -> CalleCallRun:
        for key, expected in (("plan_id", plan.plan_id), ("incident_id", plan.incident_id),
                              ("provider_id", plan.provider_id), ("provider_attempt_id", plan.provider_attempt_id),
                              ("idempotency_key", plan.idempotency_key)):
            if payload.get(key) != expected:
                raise CalleTransportError(f"transport run {key} does not match plan")
        external_status = cls._text(payload, "status")
        status_map = {"PLANNED": CalleRunStatus.PLANNED, "IN_PROGRESS": CalleRunStatus.IN_PROGRESS,
                      "RINGING": CalleRunStatus.IN_PROGRESS, "COMPLETED": CalleRunStatus.COMPLETED,
                      "FAILED": CalleRunStatus.FAILED, "NO_ANSWER": CalleRunStatus.FAILED,
                      "DECLINED": CalleRunStatus.FAILED, "CANCELED": CalleRunStatus.FAILED,
                      "CANCELLED": CalleRunStatus.FAILED, "VOICEMAIL": CalleRunStatus.FAILED,
                      "BUSY": CalleRunStatus.FAILED, "EXPIRED": CalleRunStatus.FAILED}
        if external_status not in status_map:
            raise CalleTransportError("transport returned an unexpected CALL-E status")
        status = status_map[external_status]
        completed_at = cls._time(payload.get("completed_at"), "completed_at", optional=True)
        assertions = cls._parse_assertions(payload.get("assertions", []))
        if status is not CalleRunStatus.COMPLETED and assertions:
            raise CalleTransportError("only completed transport runs may include assertions")
        if status in {CalleRunStatus.PLANNED, CalleRunStatus.IN_PROGRESS} and completed_at is not None:
            raise CalleTransportError("nonterminal transport run cannot have completion time")
        try:
            run = CalleCallRun(cls._text(payload, "run_id"), plan.plan_id, plan.incident_id, plan.provider_id,
                plan.provider_attempt_id, plan.idempotency_key, status, assertions, cls._text(payload, "provenance"),
                cls._time(payload.get("started_at"), "started_at"), completed_at)
        except CalleFixtureError as error:
            raise CalleTransportError("transport run is invalid") from error
        requested_codes = {item.code for item in plan.requirements_snapshot}
        if any(item.requirement_code not in requested_codes for item in run.assertions):
            raise CalleTransportError("transport assertions are outside the request snapshot")
        if run.completed_at is not None and any(item.observed_at < run.started_at or item.observed_at > run.completed_at
                                                for item in run.assertions):
            raise CalleTransportError("transport assertions are outside the run lifecycle")
        return run

    def plan_call(self, request: CalleVerificationRequest, authority: object) -> CalleCallPlan:
        if self._orchestrator is None:
            raise LiveCalleUnavailableError("live CALL-E orchestrator is not configured; no external call was attempted")
        self._orchestrator.validate_calle_plan_authority(authority, request)
        key = (request.incident_id, request.idempotency_key)
        with self._lock:
            existing = self._plans.get(key)
            if existing is not None:
                if (existing.provider_id, existing.provider_attempt_id, existing.requirements_snapshot, existing.planned_at) != (
                        request.provider_id, request.provider_attempt_id, request.requirements, request.requested_at):
                    raise CalleTransportError("idempotency key is bound to another immutable request snapshot")
                return existing
            plan = self._parse_plan(self._require_transport().plan_call(self._request_payload(request)), request)
            if any(item.plan_id == plan.plan_id for item in self._plans.values()):
                raise CalleTransportError("transport returned a duplicate plan id")
            self._plans[key] = plan
            return plan

    def run_call(self, plan: CalleCallPlan, authorization: CalleCallAuthorization, *, at: datetime) -> CalleCallRun:
        with self._lock:
            # Preserve the historical fail-closed seam: an unconfigured live
            # adapter validates the authorization before reporting unavailable.
            if self._transport is None:
                self._call_authorizations.consume(authorization, plan, at=at)
                self._require_transport()
            known = self._plans.get((plan.incident_id, plan.idempotency_key))
            if known is not plan:
                raise CalleTransportError("CALL-E plan was not issued by this live adapter")
            existing = next((run for run in self._runs.values() if run.plan_id == plan.plan_id), None)
            if existing is not None:
                return existing
            self._call_authorizations.consume(authorization, plan, at=at)
            run = self._parse_run(self._require_transport().run_call({"plan_id": plan.plan_id,
                "incident_id": plan.incident_id, "provider_id": plan.provider_id,
                "provider_attempt_id": plan.provider_attempt_id, "idempotency_key": plan.idempotency_key}), plan)
            if run.run_id in self._runs:
                raise CalleTransportError("transport returned a duplicate run id")
            self._runs[run.run_id] = run
            return run

    def get_call_run(self, run_id: str) -> CalleCallRun:
        with self._lock:
            previous = self._runs.get(run_id)
            if previous is None:
                raise CalleTransportError("CALL-E status requires an adapter-issued run id")
            current = self._parse_run(self._require_transport().get_call_run(run_id), self._plans[(previous.incident_id, previous.idempotency_key)])
            if current.run_id != run_id or current.started_at != previous.started_at:
                raise CalleTransportError("transport status identity changed")
            rank = {CalleRunStatus.PLANNED: 0, CalleRunStatus.IN_PROGRESS: 1,
                    CalleRunStatus.COMPLETED: 2, CalleRunStatus.FAILED: 2}
            if (rank[current.status] < rank[previous.status]
                    or previous.status in {CalleRunStatus.COMPLETED, CalleRunStatus.FAILED} and current != previous):
                raise CalleTransportError("transport status regressed or changed after terminal state")
            self._runs[run_id] = current
            return current

    def evidence_for(self, run: CalleCallRun, request: CalleVerificationRequest) -> tuple[CapabilityEvidence, ...]:
        """Issue typed evidence only for this adapter's registered completed run."""
        with self._lock:
            known = self._runs.get(run.run_id)
            plan = self._plans.get((request.incident_id, request.idempotency_key))
            if known is not run or plan is None or run.plan_id != plan.plan_id:
                raise CalleTransportError("CALL-E evidence requires an adapter-issued registered run")
            if (run.incident_id, run.provider_id, run.provider_attempt_id, run.idempotency_key,
                    request.requirements, request.requested_at) != (
                    request.incident_id, request.provider_id, request.provider_attempt_id, request.idempotency_key,
                    plan.requirements_snapshot, plan.planned_at):
                raise CalleTransportError("CALL-E evidence does not match its immutable request snapshot")
            return tuple(CapabilityEvidence(f"{run.run_id}:{item.requirement_code.value}", run.provider_id,
                item.requirement_code, item.assertion, item.value, item.observed_at, item.expires_at, run.provenance)
                for item in run.assertions) if run.status is CalleRunStatus.COMPLETED else ()

    def monitor_call_run(self, run_id: str, *, deadline_at: datetime, max_polls: int,
                         now: Callable[[], datetime]) -> tuple[CalleCallRun, ...]:
        """Bounded status polling; callers schedule any delay outside this adapter."""
        if deadline_at.tzinfo is None or deadline_at.utcoffset() is None or max_polls <= 0:
            raise ValueError("monitoring requires an aware deadline and positive poll limit")
        updates = []
        for _ in range(max_polls):
            if now() >= deadline_at:
                break
            run = self.get_call_run(run_id)
            updates.append(run)
            if run.status in {CalleRunStatus.COMPLETED, CalleRunStatus.FAILED}:
                break
        return tuple(updates)


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
