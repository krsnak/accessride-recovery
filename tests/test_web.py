import importlib.util
import re
import unittest


_WEB_AVAILABLE = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None


@unittest.skipUnless(_WEB_AVAILABLE, "FastAPI/httpx are project runtime/dev dependencies, unavailable in this local interpreter")
class WebEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from accessride.api.app import create_app
        self.client = TestClient(create_app())

    @staticmethod
    def _confirmation(response) -> tuple[str, str]:
        decision = re.search(r'name="decision_id" value="([^"]+)"', response.text)
        token = re.search(r'name="confirmation_token" value="([^"]+)"', response.text)
        assert decision is not None and token is not None
        return decision.group(1), token.group(1)

    def test_demo_renders_failure_context_evidence_and_only_verified_approval(self) -> None:
        response = self.client.get("/demo")
        self.assertEqual(response.status_code, 200)
        for text in ("masked pickup-zone placeholder", "Approved-provider boundary", "Provider A", "Provider B",
                     "Provider C", "INCOMPATIBLE", "UNKNOWN", "VERIFIED_COMPATIBLE", "Read-only activity"):
            self.assertIn(text, response.text)
        self.assertEqual(response.text.count("Human approve referral handoff"), 1)

    def test_forged_or_noncompatible_web_approval_cannot_mutate_state(self) -> None:
        app = self.client.app
        for decision_id in ("forged", app.state.demo.options["provider-a"].decision_id,
                            app.state.demo.options["provider-b"].decision_id):
            response = self.client.post("/demo/incidents/demo-001/approve", data={"decision_id": decision_id})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(app.state.demo.incident.state, "OPTIONS_READY")

    def test_missing_forged_replayed_and_cross_origin_confirmations_are_refused(self) -> None:
        app = self.client.app
        decision, token = self._confirmation(self.client.get("/demo"))
        for data in ({"decision_id": decision}, {"decision_id": decision, "confirmation_token": "forged"}):
            response = self.client.post("/demo/incidents/demo-001/approve", data=data)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(app.state.demo.incident.state, "OPTIONS_READY")
        hostile = self.client.post("/demo/incidents/demo-001/approve", data={"decision_id": decision,
                                   "confirmation_token": token}, headers={"Origin": "https://hostile.example"})
        self.assertEqual(hostile.status_code, 403)
        accepted = self.client.post("/demo/incidents/demo-001/approve", data={"decision_id": decision,
                                    "confirmation_token": token})
        self.assertEqual(accepted.status_code, 200)
        replay = self.client.post("/demo/incidents/demo-001/approve", data={"decision_id": decision,
                                  "confirmation_token": token})
        self.assertEqual(replay.status_code, 400)

    def test_only_registry_decision_endpoint_can_create_packet_once(self) -> None:
        app = self.client.app
        decision_id, token = self._confirmation(self.client.get("/demo"))
        response = self.client.post("/demo/incidents/demo-001/approve", data={"decision_id": decision_id,
                                                                                  "confirmation_token": token,
                                                                                  "provider_id": "forged"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("No booking was made", response.text)
        self.assertEqual(app.state.demo.packet.provider_id, "provider-c")
        repeated = self.client.post("/demo/incidents/demo-001/approve", data={"decision_id": decision_id,
                                                                                  "confirmation_token": token})
        self.assertEqual(repeated.status_code, 400)

    def test_expired_decision_cannot_be_approved_through_endpoint(self) -> None:
        from datetime import UTC, datetime

        from accessride.api.app import create_app
        from accessride.services.demo import DemoRecoveryService
        from fastapi.testclient import TestClient

        current = datetime(2026, 9, 9, 10, 0, tzinfo=UTC)
        client = TestClient(create_app(DemoRecoveryService(now=lambda: current)))
        current = datetime(2026, 9, 9, 11, 1, tzinfo=UTC)
        decision = client.app.state.demo.options["provider-c"]
        response = client.post("/demo/incidents/demo-001/approve", data={"decision_id": decision.decision_id})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(client.app.state.demo.incident.state, "OPTIONS_READY")

    def test_unknown_incident_and_direct_handoff_route_are_not_available(self) -> None:
        self.assertEqual(self.client.post("/demo/incidents/forged/approve", data={"decision_id": "x"}).status_code, 404)
        self.assertEqual(self.client.post("/demo/incidents/demo-001/handoff", data={}).status_code, 404)
