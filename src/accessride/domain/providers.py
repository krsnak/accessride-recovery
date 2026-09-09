from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Provider:
    provider_id: str
    name: str

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or not self.name.strip():
            raise ValueError("provider id and name are required")


class ApprovedProviderRoster(Protocol):
    def resolve(self, provider_id: str) -> Provider | None: ...


class InMemoryApprovedProviderRoster:
    def __init__(self, providers: tuple[Provider, ...]) -> None:
        self._providers = {provider.provider_id: provider for provider in providers}

    def resolve(self, provider_id: str) -> Provider | None:
        return self._providers.get(provider_id)
