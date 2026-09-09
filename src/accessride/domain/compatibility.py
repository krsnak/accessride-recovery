from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from .evidence import CapabilityEvidence
from .mobility import MobilityRequirement
from .providers import Provider
from .states import AssertionState, CompatibilityStatus


@dataclass(frozen=True, slots=True)
class CompatibilityDecision:
    """Immutable evaluation record, authoritative only when stored by a decision registry."""

    decision_id: str
    incident_id: str
    provider_id: str
    requirements_snapshot: tuple[MobilityRequirement, ...]
    evidence_snapshot: tuple[CapabilityEvidence, ...]
    evaluated_at: datetime
    valid_until: datetime
    status: CompatibilityStatus

    def __post_init__(self) -> None:
        for value, label in ((self.decision_id, "decision id"), (self.incident_id, "incident id"),
                             (self.provider_id, "provider id")):
            if not value.strip():
                raise ValueError(f"{label} is required")
        for value, label in ((self.evaluated_at, "evaluated_at"), (self.valid_until, "valid_until")):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label} must be timezone-aware")
        if self.valid_until < self.evaluated_at:
            raise ValueError("decision validity cannot precede evaluation")
        if len({item.evidence_id for item in self.evidence_snapshot}) != len(self.evidence_snapshot):
            raise ValueError("decision evidence IDs must be unique")


def evaluate_compatibility(provider: Provider, requirements: Iterable[MobilityRequirement],
                           evidence: Iterable[CapabilityEvidence], *, at: datetime) -> CompatibilityStatus:
    """Conservative deterministic policy; only current positive hard evidence verifies."""
    requirements = tuple(requirements)
    hard_requirements = tuple(item for item in requirements if item.hard_constraint)
    if not hard_requirements:
        return CompatibilityStatus.UNKNOWN
    provider_evidence = tuple(item for item in evidence if item.provider_id == provider.provider_id)
    hard_expired = False
    for requirement in hard_requirements:
        matches = tuple(item for item in provider_evidence if item.requirement_code == requirement.code)
        current = tuple(item for item in matches if item.is_current(at))
        if not current:
            # Future assertions are not stale: they cannot support a conclusion yet.
            if any(item.observed_at > at for item in matches):
                return CompatibilityStatus.UNKNOWN
            if matches:
                hard_expired = True
            else:
                return CompatibilityStatus.UNKNOWN
            continue
        if any(item.assertion is AssertionState.INCOMPATIBLE for item in current):
            return CompatibilityStatus.INCOMPATIBLE
        if any(item.assertion is AssertionState.UNKNOWN for item in current):
            return CompatibilityStatus.UNKNOWN
        if not any(item.assertion is AssertionState.VERIFIED for item in current):
            return CompatibilityStatus.UNKNOWN
    return CompatibilityStatus.EXPIRED if hard_expired else CompatibilityStatus.VERIFIED_COMPATIBLE


def make_decision(incident_id: str, provider: Provider, requirements: Iterable[MobilityRequirement],
                  evidence: Iterable[CapabilityEvidence], *, at: datetime) -> CompatibilityDecision:
    requirements_snapshot = tuple(requirements)
    evidence_snapshot = tuple(item for item in evidence if item.provider_id == provider.provider_id)
    status = evaluate_compatibility(provider, requirements_snapshot, evidence_snapshot, at=at)
    hard_codes = {item.code for item in requirements_snapshot if item.hard_constraint}
    # A non-verified decision is never an authorization. For a verified decision,
    # only current verified hard assertions needed by its hard requirements set its deadline.
    required_expiries = [item.expires_at for item in evidence_snapshot
                         if item.requirement_code in hard_codes and item.is_current(at)
                         and item.assertion is AssertionState.VERIFIED]
    valid_until = min(required_expiries) if status is CompatibilityStatus.VERIFIED_COMPATIBLE else at
    return CompatibilityDecision(str(uuid4()), incident_id, provider.provider_id, requirements_snapshot,
                                 evidence_snapshot, at, valid_until, status)
