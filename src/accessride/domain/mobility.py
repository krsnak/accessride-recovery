from dataclasses import dataclass
from enum import StrEnum


class RequirementCode(StrEnum):
    """MVP operational compatibility vector; never clinical or diagnostic data."""

    VEHICLE_AVAILABILITY = "vehicle_availability"
    WHEELCHAIR_COMPATIBILITY = "wheelchair_compatibility"
    POWER_WHEELCHAIR_COMPATIBILITY = "power_wheelchair_compatibility"
    BOARDING_METHOD = "boarding_method"
    LIFT = "lift"
    RAMP = "ramp"
    SECUREMENT = "wheelchair_securement"
    WIDTH_CAPACITY = "width_capacity"
    LENGTH_CAPACITY = "length_capacity"
    WEIGHT_CAPACITY = "weight_capacity"
    SERVICE_ANIMAL = "service_animal"
    PICKUP_AREA = "pickup_area"
    SERVICE_AREA = "service_area"
    ETA = "eta"
    ARRIVAL_DEADLINE = "arrival_deadline"
    AUTHORIZATION_ROUTE = "authorization_route"
    PAYMENT_ROUTE = "payment_route"


@dataclass(frozen=True, slots=True)
class MobilityRequirement:
    """Controlled operational requirement key, never a clinical profile or free-text note."""

    code: RequirementCode
    hard_constraint: bool = True

    def __post_init__(self) -> None:
        try:
            RequirementCode(self.code)
        except ValueError as error:
            raise ValueError("mobility requirement code is not an allowed operational key") from error


def transport_disclosure(requirements: tuple[MobilityRequirement, ...]) -> tuple[str, ...]:
    """The only transport-facing disclosure: whitelisted requirement codes."""
    return tuple(RequirementCode(requirement.code).value for requirement in requirements)
