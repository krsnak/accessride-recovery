from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias

from .states import AssertionState
from .mobility import RequirementCode

EvidenceValue: TypeAlias = str | int | float | bool


@dataclass(frozen=True, slots=True)
class CapabilityEvidence:
    """Immutable, attributable assertion about one provider capability field."""

    evidence_id: str
    provider_id: str
    requirement_code: RequirementCode
    assertion: AssertionState
    value: EvidenceValue | None
    observed_at: datetime
    expires_at: datetime
    source: str

    def __post_init__(self) -> None:
        for value, label in ((self.evidence_id, "evidence id"), (self.provider_id, "provider id"),
                             (self.requirement_code, "requirement code"), (self.source, "source")):
            if not value.strip():
                raise ValueError(f"{label} is required")
        try:
            RequirementCode(self.requirement_code)
        except ValueError as error:
            raise ValueError("evidence requirement code is not an allowed operational key") from error
        for value, label in ((self.observed_at, "observed_at"), (self.expires_at, "expires_at")):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label} must be timezone-aware")
        if self.observed_at >= self.expires_at:
            raise ValueError("observed_at must precede expires_at")
        if self.value is not None and not isinstance(self.value, (str, int, float, bool)):
            raise ValueError("evidence value must be scalar text or number")

    def is_current(self, at: datetime) -> bool:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("evaluation time must be timezone-aware")
        # An assertion cannot be current before it was observed.
        return self.observed_at <= at < self.expires_at
