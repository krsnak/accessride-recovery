from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from accessride.domain.compatibility import CompatibilityDecision
from accessride.domain.incidents import Incident, _HANDOFF_AUTHORITY, _apply_validated_handoff
from accessride.domain.providers import ApprovedProviderRoster
from accessride.domain.states import CompatibilityStatus, HandoffAction, IncidentState
from accessride.services.compatibility import DecisionRegistry


@dataclass(frozen=True, slots=True)
class HumanApproval:
    approval_id: str
    incident_id: str
    provider_id: str
    decision_id: str
    action: HandoffAction
    operator_id: str
    approved_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for value, label in ((self.approval_id, "approval id"), (self.incident_id, "incident id"),
                             (self.provider_id, "provider id"), (self.decision_id, "decision id"),
                             (self.operator_id, "operator id")):
            if not value.strip():
                raise ValueError(f"{label} is required")
        for value, label in ((self.approved_at, "approved_at"), (self.expires_at, "expires_at")):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label} must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("approval expiry must follow approval")


class ApprovalGate:
    def __init__(self, decisions: DecisionRegistry) -> None:
        self._decisions = decisions
        self._approvals: dict[str, HumanApproval] = {}
        self._used: set[str] = set()

    def approve(self, incident: Incident, decision: CompatibilityDecision, action: HandoffAction,
                operator_id: str, at: datetime, *, expires_in: timedelta = timedelta(minutes=15)) -> HumanApproval:
        if incident.state is not IncidentState.OPTIONS_READY:
            raise ValueError("approval requires options-ready state")
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("approval time must be timezone-aware")
        if not operator_id.strip() or expires_in <= timedelta(0):
            raise ValueError("operator identity and positive approval lifetime are required")
        if not self._decisions.is_authoritative(decision) or decision.incident_id != incident.incident_id:
            raise PermissionError("approval requires an authoritative decision for this incident")
        if decision.status is not CompatibilityStatus.VERIFIED_COMPATIBLE or at >= decision.valid_until:
            raise ValueError("approval requires a current verified compatibility decision")
        approval = HumanApproval(str(uuid4()), incident.incident_id, decision.provider_id, decision.decision_id,
                                 action, operator_id, at, min(at + expires_in, decision.valid_until))
        self._approvals[approval.approval_id] = approval
        incident.transition(IncidentState.HUMAN_APPROVED, at=at, actor_id=operator_id, reason="human approved referral")
        return approval

    def validate(self, approval: HumanApproval, *, incident_id: str, provider_id: str,
                 decision_id: str, action: HandoffAction, at: datetime) -> None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("handoff time must be timezone-aware")
        if self._approvals.get(approval.approval_id) is not approval:
            raise PermissionError("approval is not issued by this gate")
        if approval.approval_id in self._used or at >= approval.expires_at:
            raise PermissionError("approval is used or expired")
        if (approval.incident_id, approval.provider_id, approval.decision_id, approval.action) != (
            incident_id, provider_id, decision_id, action):
            raise PermissionError("approval does not authorize this handoff")

    def consume(self, approval: HumanApproval, *, incident_id: str, provider_id: str,
                decision_id: str, action: HandoffAction, at: datetime) -> None:
        self.validate(approval, incident_id=incident_id, provider_id=provider_id,
                      decision_id=decision_id, action=action, at=at)
        self._used.add(approval.approval_id)


class SafeHandoff:
    """Records a human-authorized referral; deliberately has no booking operation."""
    def __init__(self, roster: ApprovedProviderRoster, decisions: DecisionRegistry, approvals: ApprovalGate) -> None:
        self._roster = roster
        self._decisions = decisions
        self._approvals = approvals

    def handoff(self, incident: Incident, provider_id: str, decision: CompatibilityDecision,
                approval: HumanApproval, *, at: datetime) -> None:
        if self._roster.resolve(provider_id) is None:
            raise PermissionError("handoff is limited to roster-approved providers")
        if not self._decisions.is_authoritative(decision):
            raise PermissionError("compatibility decision is not authoritative")
        if (decision.incident_id != incident.incident_id or decision.provider_id != provider_id or
                decision.status is not CompatibilityStatus.VERIFIED_COMPATIBLE or at >= decision.valid_until):
            raise ValueError("decision does not currently authorize this handoff")
        # Preserve approval error semantics while ensuring failed incident state
        # checks happen before the approval is consumed.
        self._approvals.validate(approval, incident_id=incident.incident_id, provider_id=provider_id,
                                 decision_id=decision.decision_id, action=HandoffAction.REFERRAL, at=at)
        if incident.state is not IncidentState.HUMAN_APPROVED:
            raise ValueError("handoff requires human-approved state")
        self._approvals.consume(approval, incident_id=incident.incident_id, provider_id=provider_id,
                                decision_id=decision.decision_id, action=HandoffAction.REFERRAL, at=at)
        _apply_validated_handoff(incident, _HANDOFF_AUTHORITY, provider_id, at=at,
                                 actor_id=approval.operator_id)
