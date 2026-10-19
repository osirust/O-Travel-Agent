"""Небольшие контракты для будущих заменяемых интеграций."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class TripRequest:
    destination: str
    check_in: date
    check_out: date
    adults: int
    max_total_rub: int | None = None

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


@dataclass(frozen=True)
class HotelOffer:
    id: str
    name: str
    destination: str
    total_rub: int
    nights: int
    source: str
    is_demo: bool
    availability: str


class HotelProvider(Protocol):
    """Поиск предложений; контракт намеренно не содержит бронирования."""

    def search(self, request: TripRequest) -> list[HotelOffer]: ...


class LanguageModel(Protocol):
    """Точка подключения LLM. CLI пока принимает только структурированный JSON."""

    def complete(self, *, text: str) -> str: ...
