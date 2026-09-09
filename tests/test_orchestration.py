import unittest
from datetime import UTC, datetime, timedelta
from threading import Barrier, Thread

from accessride.domain.incidents import Incident
from accessride.domain.mobility import MobilityRequirement, RequirementCode
from accessride.domain.providers import InMemoryApprovedProviderRoster, Provider
from accessride.domain.states import IncidentState
from accessride.services.orchestration import (
    ActivityKind,
    CallCeilingExceeded,
    DeadlineExceeded,
    OrchestrationPolicy,
    OrchestrationError,
    OrchestrationStopped,
    ProviderCooldownActive,
    RecoveryOrchestrator,
    StopCondition,
)


NOW = datetime(2026, 9, 9, tzinfo=UTC)
REQUIREMENT = MobilityRequirement(RequirementCode.SECUREMENT)
ROSTER = InMemoryApprovedProviderRoster((Provider("approved-1", "Approved One"),
                                         Provider("approved-2", "Approved Two")))


def incident() -> Incident:
    item = Incident("orchestration-1", NOW, (REQUIREMENT,))
    item.transition(IncidentState.AUTHORIZED, at=NOW, actor_id="operator-7", reason="authorized")
    return item


class OrchestrationTests(unittest.TestCase):
    def make_service(self, *, ceiling=3, deadline=NOW + timedelta(hours=1), cooldown=timedelta(minutes=5)):
        return RecoveryOrchestrator(OrchestrationPolicy(deadline, ceiling, cooldown), ROSTER)

    def test_deadline_prevents_starting_a_new_attempt(self) -> None:
        item = incident()
        service = self.make_service(deadline=NOW)
        with self.assertRaises(DeadlineExceeded):
            service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        self.assertEqual(item.state, IncidentState.DEADLINE_EXPIRED)
        self.assertEqual(service.attempts_for(item), ())

    def test_call_ceiling_refusal_keeps_open_attempt_actionable(self) -> None:
        item = incident()
        service = self.make_service(ceiling=1)
        attempt = service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        with self.assertRaises(CallCeilingExceeded):
            service.start_attempt(item, "approved-2", "two", at=NOW, actor_id="operator-7")
        self.assertEqual(len(service.attempts_for(item)), 1)
        self.assertEqual(item.state, IncidentState.PROVIDER_CHECKING)
        completed = service.complete_attempt(item, attempt.attempt_id, at=NOW, actor_id="operator-7",
                                             succeeded=True, reason="answered")
        self.assertEqual(completed.status.value, "COMPLETED")
        self.assertEqual(service.activity_for(item)[-2].kind, ActivityKind.ATTEMPT_BLOCKED)

    def test_call_ceiling_stops_after_open_attempt_closes_and_another_start_is_requested(self) -> None:
        item = incident()
        service = self.make_service(ceiling=1)
        attempt = service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        service.complete_attempt(item, attempt.attempt_id, at=NOW, actor_id="operator-7",
                                 succeeded=False, reason="unavailable")
        with self.assertRaises(CallCeilingExceeded):
            service.start_attempt(item, "approved-2", "two", at=NOW, actor_id="operator-7")
        self.assertEqual(item.state, IncidentState.CALL_BUDGET_EXHAUSTED)

    def test_duplicate_idempotency_key_does_not_start_second_attempt(self) -> None:
        item = incident()
        service = self.make_service()
        first = service.start_attempt(item, "approved-1", "stable-key", at=NOW, actor_id="operator-7")
        duplicate = service.start_attempt(item, "approved-1", "stable-key", at=NOW, actor_id="operator-7")
        self.assertIs(first, duplicate)
        self.assertEqual(len(service.attempts_for(item)), 1)

    def test_provider_cooldown_prevents_retry(self) -> None:
        item = incident()
        service = self.make_service()
        attempt = service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        service.complete_attempt(item, attempt.attempt_id, at=NOW, actor_id="operator-7",
                                 succeeded=False, reason="no response")
        with self.assertRaises(ProviderCooldownActive):
            service.start_attempt(item, "approved-1", "two", at=NOW + timedelta(minutes=1), actor_id="operator-7")

    def test_provider_failure_allows_later_provider_attempt(self) -> None:
        item = incident()
        service = self.make_service()
        failed = service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        service.complete_attempt(item, failed.attempt_id, at=NOW, actor_id="operator-7",
                                 succeeded=False, reason="unavailable")
        later = service.start_attempt(item, "approved-2", "two", at=NOW, actor_id="operator-7")
        self.assertEqual(later.provider_id, "approved-2")
        self.assertEqual(item.state, IncidentState.PROVIDER_CHECKING)

    def test_stop_conditions_terminate_orchestration(self) -> None:
        item = incident()
        service = self.make_service()
        service.stop(item, StopCondition.OPERATOR_STOPPED, at=NOW, actor_id="operator-7")
        self.assertEqual(item.state, IncidentState.CANCELLED)
        with self.assertRaisesRegex(ValueError, "stopped"):
            service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")

    def test_activity_events_are_in_deterministic_order(self) -> None:
        item = incident()
        service = self.make_service()
        attempt = service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        service.complete_attempt(item, attempt.attempt_id, at=NOW, actor_id="operator-7",
                                 succeeded=False, reason="unavailable")
        service.stop(item, StopCondition.OPERATOR_STOPPED, at=NOW, actor_id="operator-7")
        events = service.activity_for(item)
        self.assertEqual([event.sequence for event in events], [1, 2, 3])
        self.assertEqual([event.kind for event in events], [
            ActivityKind.ATTEMPT_STARTED,
            ActivityKind.ATTEMPT_FAILED,
            ActivityKind.ORCHESTRATION_STOPPED,
        ])

    def test_concurrent_starts_respect_call_ceiling_atomically(self) -> None:
        item = incident()
        service = self.make_service(ceiling=1)
        gate = Barrier(3)
        outcomes: list[object] = []

        def start(provider_id: str, key: str) -> None:
            gate.wait()
            try:
                outcomes.append(service.start_attempt(item, provider_id, key, at=NOW, actor_id="operator-7"))
            except Exception as error:
                outcomes.append(error)

        threads = [Thread(target=start, args=("approved-1", "one")),
                   Thread(target=start, args=("approved-2", "two"))]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(len(service.attempts_for(item)), 1)
        self.assertEqual(sum(not isinstance(result, Exception) for result in outcomes), 1)
        self.assertEqual(sum(isinstance(result, CallCeilingExceeded) for result in outcomes), 1)

    def test_concurrent_same_key_creates_one_attempt_and_replays_once(self) -> None:
        item = incident()
        service = self.make_service()
        gate = Barrier(3)
        outcomes: list[object] = []

        def start() -> None:
            gate.wait()
            outcomes.append(service.start_attempt(item, "approved-1", "same", at=NOW, actor_id="operator-7"))

        threads = [Thread(target=start), Thread(target=start)]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(len(service.attempts_for(item)), 1)
        self.assertEqual({result.attempt_id for result in outcomes}, {"orchestration-1:attempt:1"})
        self.assertEqual([event.kind for event in service.activity_for(item)],
                         [ActivityKind.ATTEMPT_STARTED, ActivityKind.IDEMPOTENT_REPLAY])

    def test_open_provider_has_single_flight_across_keys(self) -> None:
        item = incident()
        service = self.make_service()
        service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        with self.assertRaisesRegex(OrchestrationError, "already has an open"):
            service.start_attempt(item, "approved-1", "two", at=NOW, actor_id="operator-7")
        self.assertEqual(len(service.attempts_for(item)), 1)

    def test_terminal_start_precedes_idempotency_replay_and_late_result_is_ignored(self) -> None:
        item = incident()
        service = self.make_service()
        attempt = service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        service.stop(item, StopCondition.OPERATOR_STOPPED, at=NOW, actor_id="operator-7")
        with self.assertRaises(OrchestrationStopped):
            service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        with self.assertRaises(OrchestrationStopped):
            service.complete_attempt(item, attempt.attempt_id, at=NOW, actor_id="operator-7",
                                     succeeded=True, reason="answered late")
        self.assertEqual(service.attempts_for(item)[0].status.value, "STARTED")
        self.assertEqual([event.kind for event in service.activity_for(item)][-2:],
                         [ActivityKind.ATTEMPT_BLOCKED, ActivityKind.ATTEMPT_BLOCKED])

    def test_human_approval_is_an_audited_terminal_guard_for_provider_orchestration(self) -> None:
        item = incident()
        service = self.make_service()
        attempt = service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        item.transition(IncidentState.OPTIONS_READY, at=NOW, actor_id="operator-7", reason="option ready")
        item.transition(IncidentState.HUMAN_APPROVED, at=NOW, actor_id="operator-7", reason="approved")
        with self.assertRaises(OrchestrationStopped):
            service.start_attempt(item, "approved-2", "two", at=NOW, actor_id="operator-7")
        with self.assertRaises(OrchestrationStopped):
            service.complete_attempt(item, attempt.attempt_id, at=NOW, actor_id="operator-7",
                                     succeeded=True, reason="answered late")
        self.assertEqual(service.attempts_for(item)[0].status.value, "STARTED")
        self.assertEqual([event.kind for event in service.activity_for(item)][-2:],
                         [ActivityKind.ATTEMPT_BLOCKED, ActivityKind.ATTEMPT_BLOCKED])

    def test_derived_stops_are_validated_and_terminal_duplicate_is_audited(self) -> None:
        item = incident()
        service = self.make_service()
        with self.assertRaises(OrchestrationError):
            service.stop(item, StopCondition.DEADLINE, at=NOW, actor_id="operator-7")
        with self.assertRaises(OrchestrationError):
            service.stop(item, StopCondition.PROVIDERS_EXHAUSTED, at=NOW, actor_id="operator-7")
        service.stop(item, StopCondition.OPERATOR_STOPPED, at=NOW, actor_id="operator-7")
        with self.assertRaises(OrchestrationStopped):
            service.stop(item, StopCondition.OPERATOR_STOPPED, at=NOW, actor_id="operator-7")
        self.assertEqual(service.activity_for(item)[-1].kind, ActivityKind.ACTION_BLOCKED)

    def test_providers_exhausted_is_refused_while_provider_attempts_are_open(self) -> None:
        item = incident()
        service = self.make_service()
        for provider_id in ("approved-1", "approved-2"):
            service.start_attempt(item, provider_id, provider_id, at=NOW, actor_id="operator-7")
        with self.assertRaises(OrchestrationError):
            service.stop(item, StopCondition.PROVIDERS_EXHAUSTED, at=NOW, actor_id="operator-7")
        self.assertEqual(item.state, IncidentState.PROVIDER_CHECKING)

    def test_providers_exhausted_is_refused_for_cooldown_ending_before_deadline(self) -> None:
        item = incident()
        service = self.make_service(deadline=NOW + timedelta(hours=1), cooldown=timedelta(minutes=5))
        for provider_id in ("approved-1", "approved-2"):
            attempt = service.start_attempt(item, provider_id, provider_id, at=NOW, actor_id="operator-7")
            service.complete_attempt(item, attempt.attempt_id, at=NOW, actor_id="operator-7",
                                     succeeded=False, reason="unavailable")
        with self.assertRaises(OrchestrationError):
            service.stop(item, StopCondition.PROVIDERS_EXHAUSTED,
                         at=NOW + timedelta(minutes=1), actor_id="operator-7")
        self.assertEqual(item.state, IncidentState.PROVIDER_CHECKING)

    def test_call_ceiling_is_not_relabelled_as_provider_exhaustion(self) -> None:
        item = incident()
        service = self.make_service(ceiling=1)
        service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        with self.assertRaises(OrchestrationError):
            service.stop(item, StopCondition.PROVIDERS_EXHAUSTED, at=NOW, actor_id="operator-7")
        with self.assertRaises(OrchestrationError):
            service.stop(item, StopCondition.CALL_CEILING, at=NOW, actor_id="operator-7")
        attempt = service.attempts_for(item)[0]
        service.complete_attempt(item, attempt.attempt_id, at=NOW, actor_id="operator-7",
                                 succeeded=False, reason="unavailable")
        service.stop(item, StopCondition.CALL_CEILING, at=NOW, actor_id="operator-7")
        self.assertEqual(item.state, IncidentState.CALL_BUDGET_EXHAUSTED)

    def test_deadline_is_not_relabelled_as_provider_exhaustion(self) -> None:
        item = incident()
        service = self.make_service(deadline=NOW)
        with self.assertRaises(OrchestrationError):
            service.stop(item, StopCondition.PROVIDERS_EXHAUSTED, at=NOW, actor_id="operator-7")
        service.stop(item, StopCondition.DEADLINE, at=NOW, actor_id="operator-7")
        self.assertEqual(item.state, IncidentState.DEADLINE_EXPIRED)

    def test_call_ceiling_stop_from_options_ready_is_a_valid_transition(self) -> None:
        item = incident()
        service = self.make_service(ceiling=0)
        item.transition(IncidentState.PROVIDER_CHECKING, at=NOW, actor_id="operator-7", reason="checking")
        item.transition(IncidentState.OPTIONS_READY, at=NOW, actor_id="operator-7", reason="options ready")
        service.stop(item, StopCondition.CALL_CEILING, at=NOW, actor_id="operator-7")
        self.assertEqual(item.state, IncidentState.CALL_BUDGET_EXHAUSTED)

    def test_incident_accounting_and_activity_are_isolated(self) -> None:
        first = incident()
        second = Incident("orchestration-2", NOW, (REQUIREMENT,))
        second.transition(IncidentState.AUTHORIZED, at=NOW, actor_id="operator-7", reason="authorized")
        service = self.make_service()
        service.start_attempt(first, "approved-1", "same-key", at=NOW, actor_id="operator-7")
        service.start_attempt(second, "approved-1", "same-key", at=NOW, actor_id="operator-7")
        self.assertEqual([attempt.incident_id for attempt in service.attempts_for(first)], [first.incident_id])
        self.assertEqual([attempt.incident_id for attempt in service.attempts_for(second)], [second.incident_id])
        self.assertEqual([event.sequence for event in service.activity_for(first)], [1])
        self.assertEqual([event.sequence for event in service.activity_for(second)], [1])

    def test_activity_time_cannot_move_backward_for_start_completion_or_stop(self) -> None:
        item = incident()
        service = self.make_service()
        attempt = service.start_attempt(item, "approved-1", "one", at=NOW, actor_id="operator-7")
        later = NOW + timedelta(seconds=1)
        service.complete_attempt(item, attempt.attempt_id, at=later, actor_id="operator-7",
                                 succeeded=False, reason="unavailable")
        with self.assertRaisesRegex(ValueError, "cannot move backwards"):
            service.start_attempt(item, "approved-2", "two", at=NOW, actor_id="operator-7")
        with self.assertRaisesRegex(ValueError, "cannot move backwards"):
            service.complete_attempt(item, attempt.attempt_id, at=NOW, actor_id="operator-7",
                                     succeeded=False, reason="duplicate old result")
        with self.assertRaisesRegex(ValueError, "cannot move backwards"):
            service.stop(item, StopCondition.OPERATOR_STOPPED, at=NOW, actor_id="operator-7")

    def test_action_cannot_predate_incident_report_or_transition_history(self) -> None:
        report_time = NOW + timedelta(minutes=1)
        reported = Incident("reported-late", report_time, (REQUIREMENT,))
        reported.transition(IncidentState.AUTHORIZED, at=report_time, actor_id="operator-7", reason="authorized")
        service = self.make_service()
        with self.assertRaisesRegex(ValueError, "predate incident history"):
            service.start_attempt(reported, "approved-1", "one", at=NOW, actor_id="operator-7")

        transitioned = incident()
        transition_time = NOW + timedelta(minutes=2)
        transitioned.transition(IncidentState.PROVIDER_CHECKING, at=transition_time,
                                actor_id="operator-7", reason="checking")
        with self.assertRaisesRegex(ValueError, "predate incident history"):
            service.stop(transitioned, StopCondition.OPERATOR_STOPPED,
                         at=NOW + timedelta(minutes=1), actor_id="operator-7")
