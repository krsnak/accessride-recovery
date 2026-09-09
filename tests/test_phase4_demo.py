import unittest
from datetime import UTC, datetime

from accessride.domain.states import CompatibilityStatus, IncidentState
from accessride.services.demo import DemoRecoveryService


class DemoVerticalSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixed_now = datetime(2026, 9, 9, 9, 5, tzinfo=UTC)
        self.service = DemoRecoveryService(now=lambda: self.fixed_now)

    def test_fixture_slice_reaches_options_ready_with_required_outcomes(self) -> None:
        self.assertEqual(self.service.incident.state, IncidentState.OPTIONS_READY)
        self.assertEqual({provider: decision.status for provider, decision in self.service.options.items()}, {
            "provider-a": CompatibilityStatus.INCOMPATIBLE,
            "provider-b": CompatibilityStatus.UNKNOWN,
            "provider-c": CompatibilityStatus.VERIFIED_COMPATIBLE,
        })
        self.assertEqual(len(self.service.activity()), 6)
        self.assertTrue(all(item.provider_id in {"provider-a", "provider-b", "provider-c"}
                            for item in self.service.activity()))

    def test_only_authoritative_current_verified_option_can_create_referral_packet(self) -> None:
        verified = self.service.options["provider-c"]
        packet = self.service.approve_current_option(verified.decision_id, "human-operator")
        self.assertFalse(packet.booking_made)
        self.assertEqual(packet.provider_id, "provider-c")
        self.assertEqual(self.service.incident.state, IncidentState.HANDOFF)
        self.assertEqual(self.service.incident.handoff_provider_id, "provider-c")

    def test_forged_unknown_and_incompatible_decisions_are_refused_without_state_change(self) -> None:
        for decision_id in ("forged", self.service.options["provider-a"].decision_id,
                            self.service.options["provider-b"].decision_id):
            with self.assertRaises((ValueError, PermissionError)):
                self.service.approve_current_option(decision_id, "human-operator")
            self.assertEqual(self.service.incident.state, IncidentState.OPTIONS_READY)
            self.assertIsNone(self.service.packet)

    def test_expired_current_decision_is_refused(self) -> None:
        self.fixed_now = datetime(2026, 9, 9, 10, 6, tzinfo=UTC)
        self.service.refresh_options()
        decision = self.service.options["provider-c"]
        self.assertEqual(decision.status, CompatibilityStatus.EXPIRED)
        with self.assertRaises(ValueError):
            self.service.approve_current_option(decision.decision_id, "human-operator")
        self.assertEqual(self.service.incident.state, IncidentState.OPTIONS_READY)

    def test_repeated_approval_or_handoff_is_refused(self) -> None:
        decision = self.service.options["provider-c"]
        self.service.approve_current_option(decision.decision_id, "human-operator")
        with self.assertRaises(ValueError):
            self.service.approve_current_option(decision.decision_id, "human-operator")

    def test_fixture_is_rebased_to_the_injected_current_clock(self) -> None:
        self.assertEqual(self.service.now(), self.fixed_now)
        self.assertEqual(self.service.incident.reported_at, self.fixed_now.replace(minute=0))
        self.assertGreater(self.service.options["provider-c"].valid_until, self.fixed_now)

    def test_unified_timeline_is_monotonic_and_includes_handoff_transitions(self) -> None:
        before = self.service.audit_timeline()
        self.assertEqual([event.sequence for event in before], list(range(1, len(before) + 1)))
        self.assertEqual([event.at for event in before], sorted(event.at for event in before))
        confirmation = self.service.issue_confirmation(self.service.options["provider-c"].decision_id)
        self.service.approve_with_confirmation(self.service.options["provider-c"].decision_id, confirmation.token)
        after = self.service.audit_timeline()
        self.assertEqual([event.kind for event in after[-2:]], ["HUMAN_APPROVED", "HANDOFF"])
