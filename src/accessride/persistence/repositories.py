from typing import Protocol

from accessride.domain.incidents import Incident


class IncidentRepository(Protocol):
    def get(self, incident_id: str) -> Incident | None: ...
    def save(self, incident: Incident) -> None: ...
