from dataclasses import dataclass
from typing import Protocol


class CalleAdapter(Protocol):
    def prepare_operator_brief(self, incident_id: str, provider_id: str) -> str: ...


@dataclass(frozen=True, slots=True)
class FixtureCalleAdapter:
    """Fixture-only. Produces a brief and never creates or places a call."""

    fixture_label: str = "demo"

    def prepare_operator_brief(self, incident_id: str, provider_id: str) -> str:
        return f"[{self.fixture_label}] operator brief for incident {incident_id}, provider {provider_id}"
