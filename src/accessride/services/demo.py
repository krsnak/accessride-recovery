"""Fixture-only application service for the single-process hackathon demo."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from threading import RLock
from typing import Callable

from accessride.domain.compatibility import CompatibilityDecision
from accessride.domain.incidents import Incident
from accessride.domain.mobility import MobilityRequirement, RequirementCode
from accessride.domain.providers import InMemoryApprovedProviderRoster, Provider
from accessride.domain.states import CompatibilityStatus, HandoffAction, IncidentState
from accessride.integrations.calle import CalleCallRun, CalleVerificationRequest, FixtureCalleAdapter, load_fixture_runs
from accessride.services.compatibility import DecisionRegistry, DeterministicCompatibilityService
from accessride.services.orchestration import ActivityEvent, OrchestrationPolicy, RecoveryOrchestrator
from accessride.services.safety import ApprovalGate, HumanApproval, SafeHandoff


_FIXTURE_TIME = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class HandoffPacket:
    """A referral artifact only. It is explicitly not a booking confirmation."""

    packet_id: str
    incident_id: str
    provider_id: str
    decision_id: str
    approval_id: str
    created_at: datetime
    booking_made: bool = False


@dataclass(frozen=True, slots=True)
class LocalConfirmation:
    """A short-lived, single-use local-demo confirmation capability.

    This is deliberately a UI safeguard, not authentication or production
    authorization. Its opaque value is the authority; displayed fields alone
    cannot be reconstructed into a valid capability.
    """

    token: str
    incident_id: str
    decision_id: str
    operator_id: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuditTimelineEvent:
    """Read-only normalized view of orchestration and incident audit records."""

    at: datetime
    kind: str
    provider_id: str | None
    reason: str
    actor_id: str
    source: str
    sequence: int


class DemoRecoveryService:
    """One coherent fixture run, held in memory for one process lifetime."""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        startup_at = self.now()
        # The fixture is a five-minute recording. Rebase every timestamp as one
        # coherent unit so the running demo evaluates it at real startup time,
        # while preserving each observation, completion, and expiry interval.
        fixture_origin = startup_at - timedelta(minutes=5)
        root = Path(__file__).resolve().parents[3]
        requirements = self._load_requirements(root / "fixtures" / "demo_incident.json")
        self.incident = Incident("demo-001", fixture_origin, requirements)
        self.incident.transition(IncidentState.AUTHORIZED, at=fixture_origin,
                                 actor_id="fixture-operator", reason="failure report accepted")
        self.roster = InMemoryApprovedProviderRoster((
            Provider("provider-a", "Provider A"), Provider("provider-b", "Provider B"),
            Provider("provider-c", "Provider C"),
        ))
        self.orchestrator = RecoveryOrchestrator(
            OrchestrationPolicy(fixture_origin + timedelta(hours=2), 3), self.roster)
        raw_runs = load_fixture_runs(root / "fixtures" / "calle_provider_verification.json")
        runs = self._rebase_fixture_runs(raw_runs, fixture_origin, requirements)
        self.adapter = FixtureCalleAdapter(
            runs, self.orchestrator)
        planned: list[tuple[object, CalleVerificationRequest, object]] = []
        for provider in self.roster.providers():
            attempt = self.orchestrator.start_attempt(
                self.incident, provider.provider_id, f"{provider.provider_id}-key",
                at=fixture_origin, actor_id="fixture-operator")
            authority = self.orchestrator.authorize_calle_plan(self.incident, attempt.attempt_id, at=fixture_origin)
            request = CalleVerificationRequest(self.incident.incident_id, provider.provider_id, attempt.attempt_id,
                                                attempt.idempotency_key, self.incident.requirements, fixture_origin)
            run = self.adapter.run_call(self.adapter.plan_call(request, authority))
            planned.append((attempt, request, run))
        evidence = []
        for attempt, request, run in planned:
            evidence.extend(self.adapter.evidence_for(run, request))
            self.orchestrator.complete_attempt(self.incident, attempt.attempt_id, at=run.completed_at,
                                               actor_id="fixture-adapter", succeeded=True,
                                               reason="fixture verification recorded")
        self.decisions = DecisionRegistry(tuple(evidence), self.roster)
        self.compatibility = DeterministicCompatibilityService(self.decisions)
        options_at = self.now()
        self.options = {provider.provider_id: self.compatibility.evaluate(self.incident, provider.provider_id, options_at)
                        for provider in self.roster.providers()}
        self._options_refresh_at = min(item.expires_at for item in evidence)
        self._options_last_evaluated_at = options_at
        self.incident.transition(IncidentState.OPTIONS_READY, at=self.now(), actor_id="fixture-operator",
                                 reason="approved provider compatibility options are ready")
        self.approvals = ApprovalGate(self.decisions)
        self.handoff = SafeHandoff(self.roster, self.decisions, self.approvals)
        self.packet: HandoffPacket | None = None
        self._confirmations: dict[str, LocalConfirmation] = {}
        self._used_confirmations: set[str] = set()
        self._confirmation_lock = RLock()

    @staticmethod
    def _rebase_fixture_runs(runs: tuple[CalleCallRun, ...], origin: datetime,
                             requirements: tuple[MobilityRequirement, ...]) -> tuple[CalleCallRun, ...]:
        """Move fixture timestamps together and recompute their request-bound IDs."""
        from accessride.integrations.calle import _plan_id

        offset = origin - _FIXTURE_TIME
        rebased = []
        for run in runs:
            assertions = tuple(replace(item, observed_at=item.observed_at + offset,
                                       expires_at=item.expires_at + offset) for item in run.assertions)
            request = CalleVerificationRequest(run.incident_id, run.provider_id, run.provider_attempt_id,
                                                run.idempotency_key, requirements, origin)
            rebased.append(replace(run, plan_id=_plan_id(request), assertions=assertions,
                                   started_at=run.started_at + offset, completed_at=run.completed_at + offset))
        return tuple(rebased)

    @staticmethod
    def _load_requirements(path: Path) -> tuple[MobilityRequirement, ...]:
        import json
        payload = json.loads(path.read_text())
        return tuple(MobilityRequirement(RequirementCode(item["code"]), item["hard_constraint"])
                     for item in payload["requirements"])

    def now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("demo clock must return a timezone-aware time")
        return value

    def decision_for(self, decision_id: str) -> CompatibilityDecision | None:
        return self.decisions.resolve(decision_id)

    def refresh_options(self) -> None:
        """Evaluate fixture evidence against the actual current clock."""
        if self.incident.state is IncidentState.OPTIONS_READY:
            at = self.now()
            if at != self._options_last_evaluated_at and at >= self._options_refresh_at:
                self.options = {provider.provider_id: self.compatibility.evaluate(self.incident, provider.provider_id, at)
                                for provider in self.roster.providers()}
                future_expiries = [item.expires_at for decision in self.options.values()
                                   for item in decision.evidence_snapshot if item.expires_at > at]
                self._options_refresh_at = min(future_expiries, default=at)
                self._options_last_evaluated_at = at

    def issue_confirmation(self, decision_id: str) -> LocalConfirmation:
        """Issue one local form capability for the exact current option."""
        self.refresh_options()
        decision = self.decision_for(decision_id)
        if (decision is None or decision not in self.options.values()
                or decision.status is not CompatibilityStatus.VERIFIED_COMPATIBLE):
            raise PermissionError("confirmation requires a current verified demo option")
        issued_at = self.now()
        confirmation = LocalConfirmation(token_urlsafe(32), self.incident.incident_id, decision.decision_id,
                                         f"local-confirmation:{token_urlsafe(12)}", issued_at,
                                         issued_at + timedelta(minutes=5))
        with self._confirmation_lock:
            self._confirmations[confirmation.token] = confirmation
        return confirmation

    def approve_with_confirmation(self, decision_id: str, token: str) -> HandoffPacket:
        """Consume an issued local confirmation before the safety-gated handoff."""
        self.refresh_options()
        at = self.now()
        with self._confirmation_lock:
            confirmation = next((item for value, item in self._confirmations.items()
                                 if compare_digest(value, token)), None)
            if (confirmation is None or confirmation.token in self._used_confirmations
                    or at >= confirmation.expires_at
                    or (confirmation.incident_id, confirmation.decision_id)
                    != (self.incident.incident_id, decision_id)):
                raise PermissionError("local human confirmation is missing, invalid, expired, or already used")
            self._used_confirmations.add(confirmation.token)
        return self._approve_current_option(decision_id, confirmation.operator_id)

    def approve_current_option(self, decision_id: str, operator_id: str) -> HandoffPacket:
        """Perform the explicit human approval and validated referral handoff."""
        self.refresh_options()
        return self._approve_current_option(decision_id, operator_id)

    def _approve_current_option(self, decision_id: str, operator_id: str) -> HandoffPacket:
        if self.packet is not None:
            raise ValueError("a handoff packet already exists for this incident")
        decision = self.decision_for(decision_id)
        if decision is None or decision not in self.options.values():
            raise PermissionError("decision is not a current demo option")
        if decision.status is not CompatibilityStatus.VERIFIED_COMPATIBLE:
            raise ValueError("only a current verified compatible option can be approved")
        at = self.now()
        approval: HumanApproval = self.approvals.approve(self.incident, decision, HandoffAction.REFERRAL,
                                                          operator_id, at)
        self.handoff.handoff(self.incident, decision.provider_id, decision, approval, at=at)
        self.packet = HandoffPacket(f"referral:{approval.approval_id}", self.incident.incident_id,
                                    decision.provider_id, decision.decision_id, approval.approval_id, at)
        return self.packet

    def activity(self) -> tuple[ActivityEvent, ...]:
        return self.orchestrator.activity_for(self.incident)

    def audit_timeline(self) -> tuple[AuditTimelineEvent, ...]:
        """Merge the two immutable audit streams using a causal stable order."""
        entries: list[tuple[datetime, int, int, AuditTimelineEvent]] = []
        # A transition occurs before the activity it enables at an equal time.
        for index, event in enumerate(self.incident.events):
            entries.append((event.at, 0, index, AuditTimelineEvent(
                event.at, event.to_state, None, event.reason, event.actor_id, "incident", index)))
        for event in self.activity():
            entries.append((event.at, 1, event.sequence, AuditTimelineEvent(
                event.at, event.kind, event.provider_id, event.reason, event.actor_id,
                "orchestration", event.sequence)))
        ordered = sorted(entries, key=lambda item: item[:3])
        return tuple(replace(event, sequence=index + 1) for index, (_, _, _, event) in enumerate(ordered))
