"""Fail-closed, in-memory orchestration for operator-mediated provider attempts.

This service deliberately records *attempts*, rather than making calls.  An
operator or a future approved interface may use an attempt record to prepare a
brief, but no transport action is exposed here.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import TYPE_CHECKING

from accessride.domain.incidents import Incident
from accessride.domain.mobility import MobilityRequirement
from accessride.domain.providers import ApprovedProviderRoster
from accessride.domain.states import IncidentState

if TYPE_CHECKING:
    from accessride.integrations.calle import CalleVerificationRequest


class ProviderAttemptStatus(StrEnum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StopCondition(StrEnum):
    DEADLINE = "DEADLINE"
    CALL_CEILING = "CALL_CEILING"
    PROVIDERS_EXHAUSTED = "PROVIDERS_EXHAUSTED"
    OPERATOR_STOPPED = "OPERATOR_STOPPED"


class ActivityKind(StrEnum):
    ATTEMPT_STARTED = "ATTEMPT_STARTED"
    ATTEMPT_COMPLETED = "ATTEMPT_COMPLETED"
    ATTEMPT_FAILED = "ATTEMPT_FAILED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    ATTEMPT_BLOCKED = "ATTEMPT_BLOCKED"
    ACTION_BLOCKED = "ACTION_BLOCKED"
    ORCHESTRATION_STOPPED = "ORCHESTRATION_STOPPED"


class OrchestrationError(ValueError):
    """Base error for a refused orchestration operation."""


class DeadlineExceeded(OrchestrationError):
    pass


class CallCeilingExceeded(OrchestrationError):
    pass


class ProviderCooldownActive(OrchestrationError):
    pass


class OrchestrationStopped(OrchestrationError):
    pass


@dataclass(frozen=True, slots=True)
class OrchestrationPolicy:
    deadline_at: datetime
    call_ceiling: int
    provider_cooldown: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.deadline_at.tzinfo is None or self.deadline_at.utcoffset() is None:
            raise ValueError("deadline_at must be timezone-aware")
        if self.call_ceiling < 0:
            raise ValueError("call_ceiling cannot be negative")
        if self.provider_cooldown < timedelta(0):
            raise ValueError("provider_cooldown cannot be negative")


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    attempt_id: str
    incident_id: str
    provider_id: str
    idempotency_key: str
    started_at: datetime
    status: ProviderAttemptStatus
    completed_at: datetime | None = None
    outcome_reason: str | None = None


@dataclass(frozen=True, slots=True, eq=False)
class CallePlanAuthority:
    """Opaque, process-local capability issued for one open approved attempt.

    Its fields are auditable correlation data, but those fields are not the
    authority. ``RecoveryOrchestrator`` keeps the object-identity registry that
    distinguishes an issued capability from a caller-constructed lookalike.
    """

    incident_id: str
    provider_id: str
    provider_attempt_id: str
    idempotency_key: str
    requirements_snapshot: tuple[MobilityRequirement, ...]
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    sequence: int
    incident_id: str
    kind: ActivityKind
    at: datetime
    actor_id: str
    reason: str
    provider_id: str | None = None
    attempt_id: str | None = None


_TERMINAL_STATES = frozenset({
    IncidentState.HUMAN_APPROVED,
    IncidentState.HANDOFF,
    IncidentState.NO_COMPATIBLE_OPTION,
    IncidentState.DEADLINE_EXPIRED,
    IncidentState.CALL_BUDGET_EXHAUSTED,
    IncidentState.CANCELLED,
    IncidentState.FAILED,
})


class RecoveryOrchestrator:
    """Owns attempt accounting and records every accepted/refused action in order.

    The policy is intentionally supplied at construction and held in memory.
    Persistence and external call execution are separate concerns.  The RLock
    provides atomicity only in this in-memory, single-process service. A
    multi-worker or serverless deployment needs transactional persistence and
    distributed/transactional locking before it can make the same guarantees.

    ``PROVIDERS_EXHAUSTED`` is deliberately not derived by this version. The
    retry policy has no finite per-provider attempt limit, so a failed approved
    provider can become eligible again after cooldown. Treating a temporarily
    unavailable provider as exhausted would lie about the available recovery
    path. Deadline and call-ceiling constraints are represented only by their
    corresponding stop conditions.
    """

    def __init__(self, policy: OrchestrationPolicy, roster: ApprovedProviderRoster) -> None:
        self._policy = policy
        self._roster = roster
        self._attempts: list[ProviderAttempt] = []
        self._idempotency: dict[tuple[str, str], ProviderAttempt] = {}
        self._events: list[ActivityEvent] = []
        # Capabilities are registered to the mutable incident, not to a copied
        # attempt record.  Attempt completion replaces immutable attempt values,
        # so every use must resolve the current record below.
        self._calle_authorities: dict[CallePlanAuthority, Incident] = {}
        # One lock deliberately covers incident state changes and all associated
        # accounting.  This makes a start decision a single atomic operation.
        self._lock = RLock()

    @property
    def policy(self) -> OrchestrationPolicy:
        return self._policy

    def attempts_for(self, incident: Incident) -> tuple[ProviderAttempt, ...]:
        with self._lock:
            return tuple(item for item in self._attempts if item.incident_id == incident.incident_id)

    def activity_for(self, incident: Incident) -> tuple[ActivityEvent, ...]:
        with self._lock:
            return tuple(item for item in self._events if item.incident_id == incident.incident_id)

    def authorize_calle_plan(self, incident: Incident, attempt_id: str, *, at: datetime) -> CallePlanAuthority:
        """Issue a narrow planning capability for a roster-approved open attempt."""
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("CALL-E authorization time must be timezone-aware")
        with self._lock:
            self._ensure_monotonic(incident, at)
            _, attempt = self._find_open_attempt(incident, attempt_id)
            if self._roster.resolve(attempt.provider_id) is None:
                raise PermissionError("CALL-E planning is limited to roster-approved providers")
            authority = CallePlanAuthority(
                incident.incident_id, attempt.provider_id, attempt.attempt_id,
                attempt.idempotency_key, tuple(incident.requirements), at,
            )
            self._calle_authorities[authority] = incident
            return authority

    def validate_calle_plan_authority(self, authority: object, request: "CalleVerificationRequest") -> None:
        """Validate identity and immutable request snapshot for a CALL-E adapter."""
        if not isinstance(authority, CallePlanAuthority):
            raise PermissionError("CALL-E planning requires orchestrator-issued authority")
        with self._lock:
            incident = self._calle_authorities.get(authority)
            if incident is None:
                raise PermissionError("CALL-E planning authority was not issued by this orchestrator")
            # Do not trust the immutable attempt that existed when authority
            # was issued. Resolve the currently registered attempt and current
            # incident state on every use.
            attempt = next((item for item in self._attempts
                            if item.incident_id == incident.incident_id
                            and item.attempt_id == authority.provider_attempt_id), None)
            if (incident.state in _TERMINAL_STATES or attempt is None
                    or attempt.status is not ProviderAttemptStatus.STARTED
                    or (attempt.incident_id, attempt.provider_id, attempt.attempt_id,
                        attempt.idempotency_key) != (authority.incident_id, authority.provider_id,
                                                      authority.provider_attempt_id, authority.idempotency_key)
                    or self._roster.resolve(attempt.provider_id) is None):
                raise PermissionError("CALL-E planning authority is no longer actionable")
            expected = (authority.incident_id, authority.provider_id, authority.provider_attempt_id,
                        authority.idempotency_key, authority.requirements_snapshot, authority.requested_at)
            actual = (request.incident_id, request.provider_id, request.provider_attempt_id,
                      request.idempotency_key, request.requirements, request.requested_at)
            if actual != expected:
                raise PermissionError("CALL-E request must exactly match its authorized attempt snapshot")

    # Alias makes the audit intent explicit for consumers that present activity.
    audit_events_for = activity_for

    def start_attempt(self, incident: Incident, provider_id: str, idempotency_key: str, *,
                      at: datetime, actor_id: str) -> ProviderAttempt:
        self._validate_input(provider_id, idempotency_key, at, actor_id)
        with self._lock:
            self._ensure_monotonic(incident, at)
            # Terminal state deliberately wins over a replay: a replay is not an
            # actionable operation once the incident has been stopped.
            if incident.state in _TERMINAL_STATES:
                self._blocked(incident, at, actor_id, "incident orchestration has stopped", provider_id)
                raise OrchestrationStopped("incident orchestration has stopped")
            key = (incident.incident_id, idempotency_key)
            existing = self._idempotency.get(key)
            if existing is not None:
                if existing.provider_id != provider_id:
                    self._blocked(incident, at, actor_id, "idempotency key is bound to another provider", provider_id)
                    raise OrchestrationError("idempotency key is already bound to another provider")
                self._record(incident, ActivityKind.IDEMPOTENT_REPLAY, at, actor_id,
                             "existing provider attempt returned", provider_id, existing.attempt_id)
                return existing
            if self._roster.resolve(provider_id) is None:
                self._blocked(incident, at, actor_id, "provider is not on the approved roster", provider_id)
                raise PermissionError("provider attempt is limited to roster-approved providers")
            if at >= self._policy.deadline_at:
                self.stop(incident, StopCondition.DEADLINE, at=at, actor_id=actor_id)
                raise DeadlineExceeded("incident deadline prevents a new provider attempt")
            if self._call_count(incident) >= self._policy.call_ceiling:
                if not self._has_open_attempts(incident):
                    self.stop(incident, StopCondition.CALL_CEILING, at=at, actor_id=actor_id)
                else:
                    self._blocked(incident, at, actor_id,
                                  "incident call ceiling prevents a new provider attempt", provider_id)
                raise CallCeilingExceeded("incident call ceiling prevents a new provider attempt")
            if self._has_open_provider_attempt(incident, provider_id):
                self._blocked(incident, at, actor_id, "provider already has an open attempt", provider_id)
                raise OrchestrationError("provider already has an open attempt")
            if self._in_cooldown(incident, provider_id, at):
                self._blocked(incident, at, actor_id, "provider cooldown prevents retry", provider_id)
                raise ProviderCooldownActive("provider cooldown prevents retry")
            self._enter_provider_checking(incident, at, actor_id)
            attempt = ProviderAttempt(f"{incident.incident_id}:attempt:{self._call_count(incident) + 1}",
                                      incident.incident_id, provider_id, idempotency_key, at,
                                      ProviderAttemptStatus.STARTED)
            self._attempts.append(attempt)
            self._idempotency[key] = attempt
            self._record(incident, ActivityKind.ATTEMPT_STARTED, at, actor_id,
                         "provider attempt started", provider_id, attempt.attempt_id)
            return attempt

    def complete_attempt(self, incident: Incident, attempt_id: str, *, at: datetime,
                         actor_id: str, succeeded: bool, reason: str) -> ProviderAttempt:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("attempt completion time must be timezone-aware")
        if not actor_id.strip() or not reason.strip():
            raise ValueError("actor identity and outcome reason are required")
        with self._lock:
            self._ensure_monotonic(incident, at)
            if incident.state in _TERMINAL_STATES:
                self._blocked(incident, at, actor_id, "late result ignored after orchestration stopped",
                              attempt_id=attempt_id)
                raise OrchestrationStopped("late provider result is not actionable")
            try:
                index, attempt = self._find_open_attempt(incident, attempt_id)
            except OrchestrationError:
                self._blocked(incident, at, actor_id, "attempt completion is not actionable",
                              attempt_id=attempt_id)
                raise
            if at < attempt.started_at:
                raise ValueError("attempt completion cannot precede start")
            completed = replace(attempt, status=(ProviderAttemptStatus.COMPLETED if succeeded else ProviderAttemptStatus.FAILED),
                                completed_at=at, outcome_reason=reason)
            self._attempts[index] = completed
            self._idempotency[(incident.incident_id, completed.idempotency_key)] = completed
            kind = ActivityKind.ATTEMPT_COMPLETED if succeeded else ActivityKind.ATTEMPT_FAILED
            self._record(incident, kind, at, actor_id, reason, completed.provider_id, completed.attempt_id)
            return completed

    def stop(self, incident: Incident, condition: StopCondition, *, at: datetime, actor_id: str) -> None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("stop time must be timezone-aware")
        if not actor_id.strip():
            raise ValueError("actor identity is required")
        with self._lock:
            self._ensure_monotonic(incident, at)
            if incident.state in _TERMINAL_STATES:
                self._record(incident, ActivityKind.ACTION_BLOCKED, at, actor_id,
                             "stop refused: incident orchestration has stopped")
                raise OrchestrationStopped("incident orchestration has stopped")
            self._validate_stop_condition(incident, condition, at, actor_id)
            target, reason = {
                StopCondition.DEADLINE: (IncidentState.DEADLINE_EXPIRED, "incident deadline reached"),
                StopCondition.CALL_CEILING: (IncidentState.CALL_BUDGET_EXHAUSTED, "incident call ceiling reached"),
                StopCondition.PROVIDERS_EXHAUSTED: (IncidentState.NO_COMPATIBLE_OPTION, "approved providers exhausted"),
                StopCondition.OPERATOR_STOPPED: (IncidentState.CANCELLED, "operator stopped orchestration"),
            }[condition]
            if incident.state in {IncidentState.AUTHORIZED, IncidentState.PROVIDER_CHECKING, IncidentState.OPTIONS_READY}:
                incident.transition(target, at=at, actor_id=actor_id, reason=reason)
            else:
                self._record(incident, ActivityKind.ACTION_BLOCKED, at, actor_id,
                             "stop refused: incident state cannot be stopped by orchestration")
                raise OrchestrationStopped("incident state cannot be stopped by orchestration")
            self._record(incident, ActivityKind.ORCHESTRATION_STOPPED, at, actor_id, reason)

    def _enter_provider_checking(self, incident: Incident, at: datetime, actor_id: str) -> None:
        if incident.state is IncidentState.AUTHORIZED:
            incident.transition(IncidentState.PROVIDER_CHECKING, at=at, actor_id=actor_id,
                                reason="provider orchestration started")
        elif incident.state is IncidentState.OPTIONS_READY:
            incident.transition(IncidentState.PROVIDER_CHECKING, at=at, actor_id=actor_id,
                                reason="provider orchestration resumed")
        elif incident.state is not IncidentState.PROVIDER_CHECKING:
            raise OrchestrationStopped("incident is not eligible for a provider attempt")

    def _find_open_attempt(self, incident: Incident, attempt_id: str) -> tuple[int, ProviderAttempt]:
        for index, attempt in enumerate(self._attempts):
            if attempt.incident_id == incident.incident_id and attempt.attempt_id == attempt_id:
                if attempt.status is not ProviderAttemptStatus.STARTED:
                    raise OrchestrationError("provider attempt is already closed")
                return index, attempt
        raise OrchestrationError("open provider attempt was not found")

    def _has_open_provider_attempt(self, incident: Incident, provider_id: str) -> bool:
        return any(item.incident_id == incident.incident_id
                   and item.provider_id == provider_id
                   and item.status is ProviderAttemptStatus.STARTED
                   for item in self._attempts)

    def _has_open_attempts(self, incident: Incident) -> bool:
        return any(item.incident_id == incident.incident_id
                   and item.status is ProviderAttemptStatus.STARTED
                   for item in self._attempts)

    def _call_count(self, incident: Incident) -> int:
        return sum(item.incident_id == incident.incident_id for item in self._attempts)

    def _in_cooldown(self, incident: Incident, provider_id: str, at: datetime) -> bool:
        for attempt in reversed(self._attempts):
            if attempt.incident_id == incident.incident_id and attempt.provider_id == provider_id:
                if attempt.status is ProviderAttemptStatus.FAILED and attempt.completed_at is not None:
                    return at < attempt.completed_at + self._policy.provider_cooldown
                return False
        return False

    def _validate_stop_condition(self, incident: Incident, condition: StopCondition, at: datetime,
                                 actor_id: str) -> None:
        """Refuse derived terminal states unless present service facts support them.

        Provider exhaustion remains unavailable until policy supplies a finite,
        evidence-backed per-provider attempt rule. In particular, it cannot be
        inferred from open attempts, an active cooldown, the call ceiling, or
        the deadline; the latter two have their own terminal conditions.
        """
        valid = (
            condition is StopCondition.OPERATOR_STOPPED
            or (condition is StopCondition.DEADLINE and at >= self._policy.deadline_at)
            or (condition is StopCondition.CALL_CEILING
                and self._call_count(incident) >= self._policy.call_ceiling
                and not self._has_open_attempts(incident))
        )
        if not valid:
            self._record(incident, ActivityKind.ACTION_BLOCKED, at, actor_id,
                         f"stop refused: {condition} is not currently derived from service state")
            raise OrchestrationError("stop condition is not currently satisfied")

    def _ensure_monotonic(self, incident: Incident, at: datetime) -> None:
        incident_times = (incident.reported_at, *(event.at for event in incident.events))
        if any(item.tzinfo is None or item.utcoffset() is None for item in incident_times):
            raise ValueError("incident history times must be timezone-aware")
        if at < max(incident_times):
            raise ValueError("orchestration action cannot predate incident history")
        prior = next((event.at for event in reversed(self._events)
                      if event.incident_id == incident.incident_id), None)
        if prior is not None and at < prior:
            raise ValueError("orchestration activity time cannot move backwards")

    def _blocked(self, incident: Incident, at: datetime, actor_id: str, reason: str,
                 provider_id: str | None = None, attempt_id: str | None = None) -> None:
        self._record(incident, ActivityKind.ATTEMPT_BLOCKED, at, actor_id, reason,
                     provider_id, attempt_id)

    def _record(self, incident: Incident, kind: ActivityKind, at: datetime, actor_id: str,
                reason: str, provider_id: str | None = None, attempt_id: str | None = None) -> None:
        sequence = sum(item.incident_id == incident.incident_id for item in self._events) + 1
        self._events.append(ActivityEvent(sequence, incident.incident_id, kind, at,
                                          actor_id, reason, provider_id, attempt_id))

    @staticmethod
    def _validate_input(provider_id: str, idempotency_key: str, at: datetime, actor_id: str) -> None:
        if not provider_id.strip() or not idempotency_key.strip() or not actor_id.strip():
            raise ValueError("provider id, idempotency key, and actor identity are required")
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("attempt start time must be timezone-aware")
