from collections.abc import Iterable
from datetime import datetime

from accessride.domain.compatibility import CompatibilityDecision, make_decision
from accessride.domain.evidence import CapabilityEvidence
from accessride.domain.incidents import Incident
from accessride.domain.providers import ApprovedProviderRoster


class DecisionRegistry:
    """In-memory authority boundary that creates decisions from approved inputs only."""
    def __init__(self, evidence: Iterable[CapabilityEvidence], roster: ApprovedProviderRoster) -> None:
        self._decisions: dict[str, CompatibilityDecision] = {}
        self._evidence = tuple(evidence)
        self._roster = roster

    def evaluate(self, incident: Incident, provider_id: str, at: datetime) -> CompatibilityDecision:
        provider = self._roster.resolve(provider_id)
        if provider is None:
            raise PermissionError("provider is not on the approved roster")
        decision = make_decision(incident.incident_id, provider, incident.requirements, self._evidence, at=at)
        self._decisions[decision.decision_id] = decision
        return decision

    def resolve(self, decision_id: str) -> CompatibilityDecision | None:
        return self._decisions.get(decision_id)

    def is_authoritative(self, decision: CompatibilityDecision) -> bool:
        return self.resolve(decision.decision_id) is decision


class DeterministicCompatibilityService:
    """Small evaluation facade; the registry owns roster/evidence authority inputs."""

    def __init__(self, decisions: DecisionRegistry) -> None:
        self._decisions = decisions

    def evaluate(self, incident: Incident, provider_id: str, at: datetime) -> CompatibilityDecision:
        return self._decisions.evaluate(incident, provider_id, at)
