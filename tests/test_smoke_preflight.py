import unittest
from pathlib import Path

from accessride.domain.mobility import RequirementCode
from accessride.services.smoke_preflight import SmokeScenario, scenario_from_env, validate_preflight


ROOT = Path(__file__).parents[1]


def valid(**changes: object) -> SmokeScenario:
    values: dict[str, object] = {
        "recipient_e164": "+15551234567", "recipient_control_confirmed": True,
        "operator_id": "operator-test", "call_authorization": "APPROVED_FOR_ONE_CALL",
        "objective": "Verify accessibility availability only; do not arrange anything.",
        "disclosure": "Operational mobility verification only.",
        "requirements": (RequirementCode.VEHICLE_AVAILABILITY, RequirementCode.LIFT, RequirementCode.SECUREMENT),
        "max_calls": 1, "max_duration_seconds": 180, "deadline_seconds": 240,
        "max_polls": 6, "poll_interval_seconds": 10,
    }
    values.update(changes)
    return SmokeScenario(**values)  # type: ignore[arg-type]


class SmokePreflightTests(unittest.TestCase):
    def report(self, **changes: object):
        return validate_preflight(valid(**changes), repo_root=ROOT, one_shot_operator_flag=True,
                                  git_status=lambda _: "")

    def test_valid_scenario_is_only_locally_armed(self) -> None:
        report = self.report()
        self.assertTrue(report.ok)
        self.assertTrue(report.live_execution_enabled)

    def test_missing_recipient_and_invalid_e164_are_rejected(self) -> None:
        self.assertIn("recipient must use E.164 placeholder format", self.report(recipient_e164="").diagnostics)
        self.assertIn("recipient must use E.164 placeholder format", self.report(recipient_e164="555-1234").diagnostics)

    def test_missing_environment_diagnostics_name_keys_not_values(self) -> None:
        scenario = scenario_from_env({"ACCESSRIDE_SMOKE_RECIPIENT_E164": "+15551234567"})
        report = validate_preflight(scenario, repo_root=ROOT, one_shot_operator_flag=True, git_status=lambda _: "")
        message = next(item for item in report.diagnostics if item.startswith("missing required configuration:"))
        self.assertIn("ACCESSRIDE_SMOKE_OPERATOR_ID", message)

    def test_one_shot_authorization_and_operator_flag_are_required(self) -> None:
        self.assertIn("explicit one-shot call authorization is required", self.report(call_authorization="").diagnostics)
        report = validate_preflight(valid(), repo_root=ROOT, git_status=lambda _: "")
        self.assertFalse(report.live_execution_enabled)
        self.assertIn("live execution remains disabled without --operator-one-shot-approval", report.diagnostics)

    def test_booking_and_medical_text_are_rejected(self) -> None:
        self.assertIn("booking-like objective is prohibited", self.report(objective="Book an accessible ride").diagnostics)
        self.assertIn("medical or diagnosis disclosure is prohibited", self.report(disclosure="The rider has a medical condition").diagnostics)

    def test_requirement_allowlist_and_call_limit_are_enforced(self) -> None:
        self.assertIn("requirement allowlist is enforced", self.report(requirements=()).diagnostics)
        self.assertIn("smoke-test requirement keys must be unique", self.report(
            requirements=(RequirementCode.LIFT, RequirementCode.LIFT)).diagnostics)
        self.assertIn("first smoke test max_calls must equal 1", self.report(max_calls=2).diagnostics)

    def test_deadline_and_poll_bounds_are_enforced(self) -> None:
        self.assertIn("deadline must be 60..600 seconds", self.report(deadline_seconds=30).diagnostics)
        self.assertIn("poll bounds must be max_polls 1..10 and interval 5..30 seconds", self.report(max_polls=11).diagnostics)
        self.assertIn("poll schedule must fit inside deadline", self.report(deadline_seconds=60, max_polls=10, poll_interval_seconds=10).diagnostics)

    def test_dirty_repository_and_fixture_failure_are_rejected(self) -> None:
        report = validate_preflight(valid(), repo_root=ROOT, one_shot_operator_flag=True, git_status=lambda _: " M file")
        self.assertIn("repository must be clean before a smoke test", report.diagnostics)
        report = validate_preflight(valid(), repo_root=ROOT / "missing", one_shot_operator_flag=True, git_status=lambda _: "")
        self.assertIn("fixture/demo health check failed", report.diagnostics)

    def test_diagnostics_never_echo_sensitive_values(self) -> None:
        secret = "super-secret-value"
        report = self.report(recipient_e164=secret, operator_id=secret, call_authorization=secret,
                             objective="book " + secret, disclosure="medical " + secret)
        self.assertNotIn(secret, "\n".join(report.diagnostics))
