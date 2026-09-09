import unittest
from datetime import UTC, datetime, timedelta

from accessride.domain.compatibility import CompatibilityDecision, evaluate_compatibility, make_decision
from accessride.domain.evidence import CapabilityEvidence
from accessride.domain.incidents import Incident
from accessride.domain.mobility import MobilityRequirement, RequirementCode, transport_disclosure
from accessride.domain.providers import InMemoryApprovedProviderRoster, Provider
from accessride.domain.states import AssertionState, CompatibilityStatus, HandoffAction, IncidentState
from accessride.integrations.calle import FixtureCalleAdapter
from accessride.services.compatibility import DecisionRegistry, DeterministicCompatibilityService
from accessride.services.orchestration import OrchestrationPolicy, RecoveryOrchestrator
from accessride.services.safety import ApprovalGate, SafeHandoff

NOW = datetime(2026, 9, 9, tzinfo=UTC)
HARD = MobilityRequirement(RequirementCode.SECUREMENT)
SOFT = MobilityRequirement(RequirementCode.SERVICE_AREA, hard_constraint=False)
APPROVED = Provider("approved-1", "Approved Mobility")
OTHER = Provider("approved-2", "Other Mobility")


def evidence(provider=APPROVED, requirement=HARD, assertion=AssertionState.VERIFIED,
             observed_at=NOW, expires_at=NOW + timedelta(days=1), evidence_id=None) -> CapabilityEvidence:
    return CapabilityEvidence(evidence_id or "ev-" + provider.provider_id + requirement.code + assertion.value, provider.provider_id,
                              requirement.code, assertion, True, observed_at, expires_at, "fixture")


def incident(incident_id="i-1", requirements=(HARD,)) -> Incident:
    return Incident(incident_id, NOW, requirements)


def options_ready(item: Incident) -> None:
    item.transition(IncidentState.AUTHORIZED, at=NOW, actor_id="operator-7", reason="scope authorized")
    item.transition(IncidentState.PROVIDER_CHECKING, at=NOW, actor_id="operator-7", reason="checking roster")
    item.transition(IncidentState.OPTIONS_READY, at=NOW, actor_id="operator-7", reason="option evaluated")


class CompatibilityTests(unittest.TestCase):
    def test_unknown_propagates_for_unverified_hard_constraint(self) -> None:
        self.assertEqual(evaluate_compatibility(APPROVED, [HARD], [], at=NOW), CompatibilityStatus.UNKNOWN)

    def test_explicit_unknown_stays_unknown(self) -> None:
        self.assertEqual(evaluate_compatibility(APPROVED, [HARD], [evidence(assertion=AssertionState.UNKNOWN)], at=NOW), CompatibilityStatus.UNKNOWN)
        self.assertEqual(evaluate_compatibility(APPROVED, [HARD], [evidence(), evidence(assertion=AssertionState.UNKNOWN)], at=NOW), CompatibilityStatus.UNKNOWN)

    def test_current_hard_incompatibility_wins(self) -> None:
        self.assertEqual(evaluate_compatibility(APPROVED, [HARD], [evidence(assertion=AssertionState.INCOMPATIBLE)], at=NOW), CompatibilityStatus.INCOMPATIBLE)

    def test_empty_requirements_cannot_be_verified(self) -> None:
        self.assertEqual(evaluate_compatibility(APPROVED, [], [], at=NOW), CompatibilityStatus.UNKNOWN)

    def test_soft_stale_evidence_does_not_block_hard_compatible(self) -> None:
        stale_soft = evidence(requirement=SOFT, observed_at=NOW - timedelta(days=1), expires_at=NOW)
        self.assertEqual(evaluate_compatibility(APPROVED, [HARD, SOFT], [evidence(), stale_soft], at=NOW), CompatibilityStatus.VERIFIED_COMPATIBLE)

    def test_future_only_hard_evidence_is_unknown(self) -> None:
        future = evidence(observed_at=NOW + timedelta(minutes=1), expires_at=NOW + timedelta(days=1))
        self.assertEqual(evaluate_compatibility(APPROVED, [HARD], [future], at=NOW), CompatibilityStatus.UNKNOWN)

    def test_stale_hard_evidence_is_expired_but_stale_and_future_is_unknown(self) -> None:
        stale = evidence(observed_at=NOW - timedelta(days=2), expires_at=NOW - timedelta(days=1), evidence_id="stale")
        future = evidence(observed_at=NOW + timedelta(minutes=1), expires_at=NOW + timedelta(days=1), evidence_id="future")
        self.assertEqual(evaluate_compatibility(APPROVED, [HARD], [stale], at=NOW), CompatibilityStatus.EXPIRED)
        self.assertEqual(evaluate_compatibility(APPROVED, [HARD], [stale, future], at=NOW), CompatibilityStatus.UNKNOWN)

    def test_soft_only_cannot_verify_and_soft_evidence_does_not_set_validity(self) -> None:
        self.assertEqual(evaluate_compatibility(APPROVED, [SOFT], [evidence(requirement=SOFT)], at=NOW), CompatibilityStatus.UNKNOWN)

    def test_nonverified_decision_has_no_authorization_window(self) -> None:
        decision = make_decision("unknown", APPROVED, (HARD,),
                                 (evidence(assertion=AssertionState.UNKNOWN),), at=NOW)
        self.assertEqual(decision.valid_until, NOW)

    def test_controlled_requirement_keys_and_disclosure(self) -> None:
        self.assertEqual(transport_disclosure((HARD, SOFT)), ("wheelchair_securement", "service_area"))
        with self.assertRaises(ValueError):
            MobilityRequirement("diagnosis")
        with self.assertRaises(ValueError):
            CapabilityEvidence("x", APPROVED.provider_id, "free_text", AssertionState.VERIFIED, True,
                               NOW, NOW + timedelta(days=1), "fixture")

    def test_evidence_validation(self) -> None:
        with self.assertRaises(ValueError):
            CapabilityEvidence("", APPROVED.provider_id, HARD.code, AssertionState.VERIFIED, True, NOW, NOW + timedelta(days=1), "fixture")
        with self.assertRaises(ValueError):
            CapabilityEvidence("x", APPROVED.provider_id, HARD.code, AssertionState.VERIFIED, True, NOW.replace(tzinfo=None), NOW + timedelta(days=1), "fixture")
        with self.assertRaises(ValueError):
            CapabilityEvidence("x", APPROVED.provider_id, HARD.code, AssertionState.VERIFIED, True, NOW, NOW, "fixture")


class HandoffSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.roster = InMemoryApprovedProviderRoster((APPROVED, OTHER))
        self.decisions = DecisionRegistry((evidence(), evidence(provider=OTHER)), self.roster)
        self.compatibility = DeterministicCompatibilityService(self.decisions)
        self.gate = ApprovalGate(self.decisions)
        self.handoff = SafeHandoff(self.roster, self.decisions, self.gate)

    def approved_flow(self) -> tuple[Incident, CompatibilityDecision, object]:
        item = incident()
        options_ready(item)
        decision = self.compatibility.evaluate(item, APPROVED.provider_id, NOW)
        approval = self.gate.approve(item, decision, HandoffAction.REFERRAL, "operator-7", NOW)
        return item, decision, approval

    def test_authoritative_decision_and_human_approval_are_required(self) -> None:
        item, decision, approval = self.approved_flow()
        self.handoff.handoff(item, APPROVED.provider_id, decision, approval, at=NOW)
        self.assertEqual(item.state, IncidentState.HANDOFF)
        self.assertEqual(len(item.events), 5)

    def test_fake_caller_supplied_verified_decision_is_impossible(self) -> None:
        item = incident()
        options_ready(item)
        fake = CompatibilityDecision("fake", item.incident_id, APPROVED.provider_id, (HARD,), (), NOW,
                                     NOW + timedelta(days=1), CompatibilityStatus.VERIFIED_COMPATIBLE)
        with self.assertRaises(PermissionError):
            self.gate.approve(item, fake, HandoffAction.REFERRAL, "operator-7", NOW)
        self.assertFalse(hasattr(self.decisions, "_store_issued"))
        self.assertFalse(self.decisions.is_authoritative(fake))

    def test_approval_provider_or_decision_mismatch_fails(self) -> None:
        item, decision, approval = self.approved_flow()
        other = self.compatibility.evaluate(item, OTHER.provider_id, NOW)
        with self.assertRaises(PermissionError):
            self.handoff.handoff(item, OTHER.provider_id, other, approval, at=NOW)

    def test_approval_expiry_and_single_use_fail(self) -> None:
        item, decision, approval = self.approved_flow()
        with self.assertRaises(PermissionError):
            self.handoff.handoff(item, APPROVED.provider_id, decision, approval, at=approval.expires_at)
        item, decision, approval = self.approved_flow()
        self.handoff.handoff(item, APPROVED.provider_id, decision, approval, at=NOW)
        with self.assertRaises(PermissionError):
            self.handoff.handoff(item, APPROVED.provider_id, decision, approval, at=NOW)

    def test_direct_handoff_bypass_is_blocked(self) -> None:
        item = incident()
        with self.assertRaises(ValueError):
            item.transition(IncidentState.HANDOFF, at=NOW, actor_id="caller", reason="bypass")
        self.assertFalse(hasattr(item, "record_handoff"))
        self.assertFalse(hasattr(item, "_complete_authorized_handoff"))
        self.assertFalse(hasattr(item, "complete_handoff"))

    def test_incident_public_state_is_read_only(self) -> None:
        item = incident()
        with self.assertRaises(AttributeError):
            item.state = IncidentState.HANDOFF
        with self.assertRaises(AttributeError):
            item.handoff_provider_id = APPROVED.provider_id
        with self.assertRaises(AttributeError):
            item.events = ()
        with self.assertRaises(AttributeError):
            item.events.append(None)

    def test_cancelled_after_approval_does_not_consume_approval(self) -> None:
        item, decision, approval = self.approved_flow()
        item.transition(IncidentState.CANCELLED, at=NOW, actor_id="operator-7", reason="rider cancelled")
        with self.assertRaises(ValueError):
            self.handoff.handoff(item, APPROVED.provider_id, decision, approval, at=NOW)
        self.assertNotIn(approval.approval_id, self.gate._used)

    def test_decision_validity_uses_only_current_verified_hard_evidence(self) -> None:
        item = incident("validity", (HARD, SOFT))
        hard_expiry = NOW + timedelta(hours=1)
        soft_expiry = NOW + timedelta(minutes=1)
        registry = DecisionRegistry((
            evidence(expires_at=hard_expiry, evidence_id="hard-current"),
            evidence(requirement=SOFT, expires_at=soft_expiry, evidence_id="soft-current"),
        ), self.roster)
        decision = registry.evaluate(item, APPROVED.provider_id, NOW)
        self.assertEqual(decision.status, CompatibilityStatus.VERIFIED_COMPATIBLE)
        self.assertEqual(decision.valid_until, hard_expiry)

    def test_unapproved_provider_cannot_be_resolved(self) -> None:
        with self.assertRaises(PermissionError):
            self.compatibility.evaluate(incident(), "outside", NOW)

    def test_invalid_state_transition_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            incident().transition(IncidentState.OPTIONS_READY, at=NOW, actor_id="operator-7", reason="skip")

    def test_fixture_adapter_has_no_autonomous_booking_behavior(self) -> None:
        adapter = FixtureCalleAdapter((), RecoveryOrchestrator(
            OrchestrationPolicy(NOW + timedelta(hours=1), 1), InMemoryApprovedProviderRoster((APPROVED,))))
        self.assertFalse(hasattr(adapter, "book"))
        self.assertFalse(hasattr(adapter, "call"))
        self.assertIn("operator brief", adapter.prepare_operator_brief("i-1", "approved-1"))
