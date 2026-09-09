from datetime import datetime
from typing import Protocol

from accessride.domain.incidents import Incident
from accessride.domain.compatibility import CompatibilityDecision
from accessride.domain.states import HandoffAction
from accessride.services.safety import HumanApproval


class CompatibilityService(Protocol):
    def evaluate(self, incident: Incident, provider_id: str, at: datetime) -> CompatibilityDecision: ...


class ApprovalService(Protocol):
    def approve(self, incident: Incident, decision: CompatibilityDecision, action: HandoffAction,
                operator_id: str, at: datetime) -> HumanApproval: ...


class HandoffService(Protocol):
    def handoff(self, incident: Incident, provider_id: str, decision: CompatibilityDecision,
                approval: HumanApproval, *, at: datetime) -> None: ...


class RecoveryOrchestrator(Protocol):
    """Coordinates assessment and approved handoff; it never books transport."""

    def prepare_handoff(self, incident: Incident, provider_id: str) -> None: ...
