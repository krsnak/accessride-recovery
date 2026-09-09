from dataclasses import dataclass
from datetime import datetime

from .mobility import MobilityRequirement
from .states import IncidentState

_ALLOWED = {
    IncidentState.REPORTED: {IncidentState.AUTHORIZED, IncidentState.CANCELLED, IncidentState.FAILED},
    IncidentState.AUTHORIZED: {IncidentState.PROVIDER_CHECKING, IncidentState.NO_COMPATIBLE_OPTION,
                               IncidentState.CANCELLED, IncidentState.DEADLINE_EXPIRED,
                               IncidentState.CALL_BUDGET_EXHAUSTED, IncidentState.FAILED},
    IncidentState.PROVIDER_CHECKING: {IncidentState.OPTIONS_READY, IncidentState.NO_COMPATIBLE_OPTION, IncidentState.DEADLINE_EXPIRED, IncidentState.CALL_BUDGET_EXHAUSTED, IncidentState.CANCELLED, IncidentState.FAILED},
    IncidentState.OPTIONS_READY: {IncidentState.HUMAN_APPROVED, IncidentState.PROVIDER_CHECKING, IncidentState.CANCELLED, IncidentState.DEADLINE_EXPIRED, IncidentState.CALL_BUDGET_EXHAUSTED, IncidentState.FAILED},
    IncidentState.HUMAN_APPROVED: {IncidentState.CANCELLED, IncidentState.DEADLINE_EXPIRED, IncidentState.FAILED},
}


@dataclass(frozen=True, slots=True)
class TransitionEvent:
    from_state: IncidentState
    to_state: IncidentState
    at: datetime
    actor_id: str
    reason: str


class Incident:
    """State is observable but mutation remains inside validated domain operations."""

    __slots__ = ("incident_id", "reported_at", "requirements", "_state", "_handoff_provider_id", "_events")

    def __init__(self, incident_id: str, reported_at: datetime,
                 requirements: tuple[MobilityRequirement, ...]) -> None:
        self.incident_id = incident_id
        self.reported_at = reported_at
        self.requirements = tuple(requirements)
        self._state = IncidentState.REPORTED
        self._handoff_provider_id: str | None = None
        self._events: list[TransitionEvent] = []

    @property
    def state(self) -> IncidentState:
        return self._state

    @property
    def handoff_provider_id(self) -> str | None:
        return self._handoff_provider_id

    @property
    def events(self) -> tuple[TransitionEvent, ...]:
        return tuple(self._events)

    def transition(self, to_state: IncidentState, *, at: datetime, actor_id: str, reason: str) -> None:
        if to_state is IncidentState.HANDOFF or to_state not in _ALLOWED.get(self._state, set()):
            raise ValueError(f"invalid incident transition: {self._state} -> {to_state}")
        if not actor_id.strip() or not reason.strip():
            raise ValueError("transition actor and reason are required")
        self._events.append(TransitionEvent(self._state, to_state, at, actor_id, reason))
        self._state = to_state


class _ValidatedHandoffAuthority:
    """Unexported capability held only by the validated handoff service."""


_HANDOFF_AUTHORITY = _ValidatedHandoffAuthority()


def _apply_validated_handoff(incident: Incident, authority: _ValidatedHandoffAuthority,
                             provider_id: str, *, at: datetime, actor_id: str) -> None:
    """Module-internal mutation endpoint; the capability is never exposed by Incident."""
    if authority is not _HANDOFF_AUTHORITY:
        raise PermissionError("validated handoff authority is required")
    if incident.state is not IncidentState.HUMAN_APPROVED:
        raise ValueError("handoff requires human-approved state")
    incident._handoff_provider_id = provider_id
    incident._events.append(TransitionEvent(incident.state, IncidentState.HANDOFF, at, actor_id,
                                            "approved referral handoff"))
    incident._state = IncidentState.HANDOFF
