import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from accessride.domain.compatibility import evaluate_compatibility
from accessride.domain.incidents import Incident
from accessride.domain.mobility import MobilityRequirement, RequirementCode
from accessride.domain.providers import InMemoryApprovedProviderRoster, Provider
from accessride.domain.states import AssertionState, CompatibilityStatus, IncidentState
from accessride.integrations.calle import (
    CalleCallAuthorization, CalleCallAuthorizationGate, CalleCallRun, CalleFixtureError,
    CalleRunStatus, CalleTransportError, CalleVerificationAssertion, CalleVerificationRequest,
    FixtureCalleAdapter, InMemoryCalleTransport, LiveCalleAdapter, LiveCalleUnavailableError, load_fixture_runs,
)
from accessride.services.orchestration import CallePlanAuthority, OrchestrationPolicy, RecoveryOrchestrator


NOW = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)
EVALUATED = NOW + timedelta(minutes=30)
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "calle_provider_verification.json"
HARD_REQUIREMENTS = tuple(MobilityRequirement(code) for code in (
    RequirementCode.VEHICLE_AVAILABILITY, RequirementCode.LIFT,
    RequirementCode.BOARDING_METHOD, RequirementCode.SECUREMENT,
))
DEMO_REQUIREMENTS = HARD_REQUIREMENTS + tuple(MobilityRequirement(code, hard_constraint=False) for code in (
    RequirementCode.ETA, RequirementCode.PICKUP_AREA, RequirementCode.SERVICE_AREA,
    RequirementCode.AUTHORIZATION_ROUTE, RequirementCode.PAYMENT_ROUTE,
))


class FixtureCalleVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        roster = InMemoryApprovedProviderRoster(tuple(Provider(f"provider-{letter}", letter) for letter in "abc"))
        self.orchestrator = RecoveryOrchestrator(OrchestrationPolicy(NOW + timedelta(hours=2), 10), roster)
        self.adapter = FixtureCalleAdapter(load_fixture_runs(FIXTURE_PATH), self.orchestrator)

    def _authorized_request(self, provider_id: str) -> tuple[Incident, CalleVerificationRequest, object]:
        incident = Incident("demo-001", NOW - timedelta(minutes=1), DEMO_REQUIREMENTS)
        incident.transition(IncidentState.AUTHORIZED, at=NOW - timedelta(seconds=30), actor_id="operator", reason="review")
        # The demo fixture represents one incident's ordered A/B/C attempts.
        # Start earlier approved attempts so the selected provider receives the
        # same immutable attempt correlation as the fixture vertical slice.
        for earlier in ("provider-a", "provider-b"):
            if earlier == provider_id:
                break
            self.orchestrator.start_attempt(incident, earlier, f"{earlier}-key", at=NOW, actor_id="operator")
        attempt = self.orchestrator.start_attempt(incident, provider_id, f"{provider_id}-key", at=NOW, actor_id="operator")
        authority = self.orchestrator.authorize_calle_plan(incident, attempt.attempt_id, at=NOW)
        return incident, CalleVerificationRequest(incident.incident_id, provider_id, attempt.attempt_id,
            attempt.idempotency_key, incident.requirements, NOW), authority

    def test_plan_run_evidence_lifecycle_is_deterministic(self) -> None:
        _, item, authority = self._authorized_request("provider-c")
        planned = self.adapter.plan_call(item, authority)
        self.assertEqual(planned.disclosure, tuple(entry.code.value for entry in DEMO_REQUIREMENTS))
        run = self.adapter.run_call(planned)
        self.assertIs(run, self.adapter.run_call(planned))
        self.assertIs(run, self.adapter.get_call_run(run.run_id))
        self.assertTrue(self.adapter.evidence_for(run, item))

    def test_unrostered_or_forged_attempt_cannot_plan_or_mint_evidence(self) -> None:
        incident, item, _ = self._authorized_request("provider-c")
        forged = CallePlanAuthority(item.incident_id, item.provider_id, item.provider_attempt_id,
            item.idempotency_key, item.requirements, item.requested_at)
        with self.assertRaisesRegex(PermissionError, "not issued"):
            self.adapter.plan_call(item, forged)
        fake_run = CalleCallRun("forged", "forged-plan", item.incident_id, item.provider_id,
            item.provider_attempt_id, item.idempotency_key, CalleRunStatus.COMPLETED, (), "fixture-calle:provider-c",
            NOW, NOW)
        with self.assertRaisesRegex(CalleFixtureError, "adapter-issued"):
            self.adapter.evidence_for(fake_run, item)
        with self.assertRaisesRegex(CalleFixtureError, "adapter-issued"):
            fake_run.to_evidence(item)
        # This is the former forged-run-to-handoff route: without adapter-issued
        # evidence there is no compatibility decision or handoff transition.
        self.assertIsNot(incident.state, IncidentState.HANDOFF)

    def test_stale_plan_authority_is_rechecked_against_current_attempt_and_incident(self) -> None:
        for succeeded in (True, False):
            self.setUp()
            incident, request, authority = self._authorized_request("provider-c")
            self.orchestrator.complete_attempt(incident, request.provider_attempt_id, at=NOW,
                                               actor_id="operator", succeeded=succeeded, reason="resolved")
            with self.assertRaisesRegex(PermissionError, "no longer actionable"):
                self.adapter.plan_call(request, authority)

        self.setUp()
        incident, request, authority = self._authorized_request("provider-c")
        self.orchestrator.stop(incident, self._stop_condition(), at=NOW, actor_id="operator")
        with self.assertRaisesRegex(PermissionError, "no longer actionable"):
            self.adapter.plan_call(request, authority)

        self.setUp()
        incident, request, authority = self._authorized_request("provider-c")
        incident.transition(IncidentState.OPTIONS_READY, at=NOW, actor_id="operator", reason="option ready")
        incident.transition(IncidentState.HUMAN_APPROVED, at=NOW, actor_id="operator", reason="approved")
        with self.assertRaisesRegex(PermissionError, "no longer actionable"):
            self.adapter.plan_call(request, authority)

    @staticmethod
    def _stop_condition():
        from accessride.services.orchestration import StopCondition
        return StopCondition.OPERATOR_STOPPED

    def test_substituted_authority_adapter_cannot_mint_evidence_for_handoff_path(self) -> None:
        class AllowAll:
            def validate_calle_plan_authority(self, authority, request) -> None:
                pass

        incident, request, _ = self._authorized_request("provider-c")
        # Previously this substituted validator could accept a constructed
        # authority, issue a fixture plan/run, and feed a handoff decision.
        # Construction now fails before any evidence can be issued.
        with self.assertRaisesRegex(TypeError, "concrete RecoveryOrchestrator"):
            FixtureCalleAdapter(load_fixture_runs(FIXTURE_PATH), AllowAll())
        self.assertIsNot(incident.state, IncidentState.HANDOFF)

    def test_request_snapshot_and_assertion_subset_are_enforced(self) -> None:
        _, item, authority = self._authorized_request("provider-c")
        altered = CalleVerificationRequest(item.incident_id, item.provider_id, item.provider_attempt_id,
            item.idempotency_key, HARD_REQUIREMENTS, item.requested_at)
        with self.assertRaisesRegex(PermissionError, "exactly match"):
            self.adapter.plan_call(altered, authority)
        _, short_request, short_authority = self._authorized_request("provider-b")
        short = CalleVerificationRequest(short_request.incident_id, short_request.provider_id,
            short_request.provider_attempt_id, short_request.idempotency_key, HARD_REQUIREMENTS, short_request.requested_at)
        # The actual authority snapshot, not merely matching IDs, rejects changed requirements.
        with self.assertRaisesRegex(PermissionError, "exactly match"):
            self.adapter.plan_call(short, short_authority)
        _, full_request, full_authority = self._authorized_request("provider-a")
        plan = self.adapter.plan_call(full_request, full_authority)
        extra = CalleVerificationAssertion(RequirementCode.RAMP, AssertionState.VERIFIED, True,
            NOW + timedelta(minutes=3), NOW + timedelta(hours=1))
        source = next(run for run in load_fixture_runs(FIXTURE_PATH) if run.provider_id == "provider-a")
        extra_run = replace(source, plan_id=plan.plan_id, provider_attempt_id=full_request.provider_attempt_id,
                            assertions=source.assertions + (extra,))
        extra_adapter = FixtureCalleAdapter((extra_run,), self.orchestrator)
        plan = extra_adapter.plan_call(full_request, full_authority)
        with self.assertRaisesRegex(CalleFixtureError, "subset"):
            extra_adapter.run_call(plan)

    def test_demo_outcomes_remain_incompatible_unknown_and_verified(self) -> None:
        statuses = {}
        for provider_id in ("provider-a", "provider-b", "provider-c"):
            self.setUp()
            _, item, authority = self._authorized_request(provider_id)
            run = self.adapter.run_call(self.adapter.plan_call(item, authority))
            statuses[provider_id] = evaluate_compatibility(Provider(provider_id, provider_id), item.requirements,
                self.adapter.evidence_for(run, item), at=EVALUATED)
        self.assertEqual(statuses, {"provider-a": CompatibilityStatus.INCOMPATIBLE,
                                   "provider-b": CompatibilityStatus.UNKNOWN,
                                   "provider-c": CompatibilityStatus.VERIFIED_COMPATIBLE})

    def test_semantic_and_chronology_validation(self) -> None:
        with self.assertRaisesRegex(CalleFixtureError, "inconsistent"):
            CalleVerificationAssertion(RequirementCode.LIFT, AssertionState.VERIFIED, False, NOW, NOW + timedelta(minutes=1))
        with self.assertRaisesRegex(CalleFixtureError, "positive or negative"):
            CalleVerificationAssertion(RequirementCode.ETA, AssertionState.UNKNOWN, 15, NOW, NOW + timedelta(minutes=1))
        with self.assertRaisesRegex(CalleFixtureError, "numeric"):
            CalleVerificationAssertion(RequirementCode.ETA, AssertionState.VERIFIED, "soon", NOW, NOW + timedelta(minutes=1))

    def test_assertions_must_be_observed_during_actual_run(self) -> None:
        _, request, authority = self._authorized_request("provider-c")
        plan = self.adapter.plan_call(request, authority)
        source = next(run for run in load_fixture_runs(FIXTURE_PATH) if run.provider_id == "provider-c")
        too_early = replace(source, plan_id=plan.plan_id, provider_attempt_id=request.provider_attempt_id,
                            started_at=NOW + timedelta(minutes=4))
        adapter = FixtureCalleAdapter((too_early,), self.orchestrator)
        plan = adapter.plan_call(request, authority)
        with self.assertRaisesRegex(CalleFixtureError, "actual run lifecycle"):
            adapter.run_call(plan)

    def test_fixture_adapter_is_thread_safe_for_same_plan(self) -> None:
        _, item, authority = self._authorized_request("provider-c")
        def exercise() -> tuple[object, object]:
            plan = self.adapter.plan_call(item, authority)
            return plan, self.adapter.run_call(plan)
        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(lambda _: exercise(), range(32)))
        self.assertEqual(len({id(plan) for plan, _ in outcomes}), 1)
        self.assertEqual(len({id(run) for _, run in outcomes}), 1)

    def test_live_contract_requires_separate_issued_call_authorization(self) -> None:
        _, item, authority = self._authorized_request("provider-c")
        plan = self.adapter.plan_call(item, authority)
        gate = CalleCallAuthorizationGate()
        live = LiveCalleAdapter(gate)
        fake = CalleCallAuthorization("fake", plan.incident_id, plan.provider_id, plan.provider_attempt_id,
            plan.plan_id, "operator", NOW, NOW + timedelta(minutes=1))
        with self.assertRaisesRegex(PermissionError, "not issued"):
            live.run_call(plan, fake, at=NOW)
        approved = gate.approve_call(plan, "operator", at=NOW)
        with self.assertRaisesRegex(LiveCalleUnavailableError, "no external call was attempted"):
            live.run_call(plan, approved, at=NOW)
        with self.assertRaisesRegex(PermissionError, "used or expired"):
            gate.validate(approved, plan, at=NOW)
        retry = gate.approve_call(plan, "operator", at=NOW)
        with self.assertRaisesRegex(LiveCalleUnavailableError, "no external call was attempted"):
            live.run_call(plan, retry, at=NOW)

    def test_call_authorization_consume_is_atomic_under_forced_interleaving(self) -> None:
        _, request, authority = self._authorized_request("provider-c")
        plan = self.adapter.plan_call(request, authority)
        gate = CalleCallAuthorizationGate()
        approved = gate.approve_call(plan, "operator", at=NOW)
        from threading import Barrier
        barrier = Barrier(3)

        def consume() -> object:
            barrier.wait()
            try:
                gate.consume(approved, plan, at=NOW)
                return True
            except PermissionError:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(consume), executor.submit(consume)]
            barrier.wait()
            outcomes = [future.result() for future in futures]
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(outcomes.count(False), 1)


class LiveCalleTransportContractTests(unittest.TestCase):
    def setUp(self) -> None:
        FixtureCalleVerificationTests.setUp(self)

    def _authorized_request(self, provider_id: str):
        return FixtureCalleVerificationTests._authorized_request(self, provider_id)

    def _envelope(self, request, *, plan_id="live-plan", run_id="live-run", status="IN_PROGRESS",
                  assertions=(), completed_at=None, provider_id=None):
        plan = {"plan_id": plan_id, "incident_id": request.incident_id,
                "provider_id": provider_id or request.provider_id, "provider_attempt_id": request.provider_attempt_id,
                "idempotency_key": request.idempotency_key, "requirements": [item.code.value for item in request.requirements],
                "disclosure": [item.code.value for item in request.requirements], "requested_at": request.requested_at.isoformat(),
                "planned_at": request.requested_at.isoformat()}
        run = {**plan, "run_id": run_id, "status": status, "assertions": list(assertions),
               "provenance": "calle-live:contract-test", "started_at": request.requested_at.isoformat(),
               "completed_at": completed_at}
        return plan, run

    def _live(self, request, plan_response, run_response, statuses=()):
        transport = InMemoryCalleTransport([plan_response], [run_response], {run_response["run_id"]: list(statuses)})
        return LiveCalleAdapter(CalleCallAuthorizationGate(), self.orchestrator, transport), transport

    def test_transport_happy_path_requires_authority_and_single_use_approval(self):
        _, request, authority = self._authorized_request("provider-c")
        assertion = {"requirement_code": RequirementCode.LIFT.value, "assertion": "VERIFIED", "value": True,
                     "observed_at": NOW.isoformat(), "expires_at": (NOW + timedelta(hours=1)).isoformat()}
        plan_response, run_response = self._envelope(request, status="COMPLETED", assertions=(assertion,), completed_at=NOW.isoformat())
        live, transport = self._live(request, plan_response, run_response)
        plan = live.plan_call(request, authority)
        approval = live._call_authorizations.approve_call(plan, "operator", at=NOW)
        run = live.run_call(plan, approval, at=NOW)
        self.assertEqual(run.status, CalleRunStatus.COMPLETED)
        self.assertEqual(len(live.evidence_for(run, request)), 1)
        self.assertEqual(len(transport.plan_requests), 1)
        self.assertIs(run, live.run_call(plan, approval, at=NOW))

    def test_malformed_correlation_and_unknown_status_fail_closed_before_evidence(self):
        _, request, authority = self._authorized_request("provider-c")
        plan_response, run_response = self._envelope(request, provider_id="provider-a")
        live, _ = self._live(request, plan_response, run_response)
        with self.assertRaisesRegex(CalleTransportError, "provider_id"):
            live.plan_call(request, authority)
        plan_response, run_response = self._envelope(request, status="SURPRISE")
        live, _ = self._live(request, plan_response, run_response)
        plan = live.plan_call(request, authority)
        approval = live._call_authorizations.approve_call(plan, "operator", at=NOW)
        with self.assertRaisesRegex(CalleTransportError, "unexpected"):
            live.run_call(plan, approval, at=NOW)

        plan_response, run_response = self._envelope(request, status="COMPLETED", completed_at=NOW.isoformat(),
            assertions=({"requirement_code": RequirementCode.RAMP.value, "assertion": "VERIFIED", "value": True,
                         "observed_at": NOW.isoformat(), "expires_at": (NOW + timedelta(hours=1)).isoformat()},))
        live, _ = self._live(request, plan_response, run_response)
        plan = live.plan_call(request, authority)
        approval = live._call_authorizations.approve_call(plan, "operator", at=NOW)
        with self.assertRaisesRegex(CalleTransportError, "outside the request"):
            live.run_call(plan, approval, at=NOW)

    def test_status_rejects_terminal_regression_and_requirement_mismatch(self):
        _, request, authority = self._authorized_request("provider-c")
        plan_response, run_response = self._envelope(request)
        completed = {**run_response, "status": "COMPLETED", "completed_at": NOW.isoformat()}
        regressed = {**run_response, "status": "IN_PROGRESS"}
        live, _ = self._live(request, plan_response, run_response, (completed, regressed))
        plan = live.plan_call(request, authority)
        approval = live._call_authorizations.approve_call(plan, "operator", at=NOW)
        run = live.run_call(plan, approval, at=NOW)
        self.assertEqual(live.get_call_run(run.run_id).status, CalleRunStatus.COMPLETED)
        with self.assertRaisesRegex(CalleTransportError, "terminal"):
            live.get_call_run(run.run_id)
        self.setUp()
        _, request, authority = self._authorized_request("provider-c")
        plan_response, run_response = self._envelope(request)
        plan_response["requirements"] = ["LIFT"]
        live, _ = self._live(request, plan_response, run_response)
        with self.assertRaisesRegex(CalleTransportError, "requirements"):
            live.plan_call(request, authority)

    def test_monitoring_is_bounded_by_poll_count_and_deadline(self):
        _, request, authority = self._authorized_request("provider-c")
        plan_response, run_response = self._envelope(request)
        statuses = ({**run_response}, {**run_response})
        live, _ = self._live(request, plan_response, run_response, statuses)
        plan = live.plan_call(request, authority)
        approval = live._call_authorizations.approve_call(plan, "operator", at=NOW)
        run = live.run_call(plan, approval, at=NOW)
        self.assertEqual(len(live.monitor_call_run(run.run_id, deadline_at=NOW + timedelta(minutes=1),
                                                  max_polls=2, now=lambda: NOW)), 2)
        self.assertEqual(live.monitor_call_run(run.run_id, deadline_at=NOW, max_polls=2, now=lambda: NOW), ())
