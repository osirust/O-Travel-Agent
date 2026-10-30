"""Immutable geographic snapshots. Structural validation never proves API availability.

Only server code supplies these snapshots to domain edits. Persistent provenance
verification belongs to the catalog boundary, outside this pure module.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_LOCATION_REFS = 32  # Engineering storage bound, not a world coverage limit.


def location_label(value: str) -> str:
    """A bounded exact display label, never a resolver or alias lookup."""
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 160:
        raise ValueError("Название места должно содержать от 1 до 160 символов без внешних пробелов.")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("Название места содержит управляющие символы.")
    return value


def openmeteo_place_id(external_id: int) -> str:
    if type(external_id) is not int or not 1 <= external_id <= 2_147_483_647:
        raise ValueError("Open-Meteo location ID должен быть положительным Int32.")
    return f"geo:openmeteo:{external_id}"


class FrozenPlaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, revalidate_instances="always")


class OpenMeteoSource(FrozenPlaceModel):
    provider: Literal["openmeteo"] = "openmeteo"
    external_id: int = Field(gt=0, le=2_147_483_647, strict=True)


class AdministrativeArea(FrozenPlaceModel):
    level: int = Field(ge=1, le=4, strict=True)
    external_id: int = Field(gt=0, le=2_147_483_647, strict=True)
    name: str

    _name = field_validator("name")(location_label)


class LiteAPICityBinding(FrozenPlaceModel):
    country_iso2: str = Field(pattern=r"^[A-Z]{2}$", strict=True)
    city_name: str

    _name = field_validator("city_name")(location_label)


class DuffelPlaceBinding(FrozenPlaceModel):
    country_iso2: str = Field(pattern=r"^[A-Z]{2}$", strict=True)
    external_id: str = Field(min_length=1, max_length=160, strict=True)
    place_type: Literal["city", "airport"]
    iata_code: str | None = Field(default=None, pattern=r"^[A-Z]{3}$", strict=True)

    _external_id = field_validator("external_id")(location_label)


class ProviderPlaceBindings(FrozenPlaceModel):
    liteapi: LiteAPICityBinding | None = None
    duffel: DuffelPlaceBinding | None = None


class CityRefSnapshot(FrozenPlaceModel):
    place_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9:._/-]*$", strict=True)
    ref_version: int = Field(ge=1, strict=True)
    canonical_name: str
    country_iso2: str = Field(pattern=r"^[A-Z]{2}$", strict=True)
    country_name: str | None = None
    geographic_source: OpenMeteoSource
    latitude: float = Field(ge=-90, le=90, strict=True)
    longitude: float = Field(ge=-180, le=180, strict=True)
    timezone: str | None = None
    # PPL* is populated-place geography. PCLI is a country and must not pass.
    feature_code: str = Field(pattern=r"^PPL[A-Z0-9]{0,6}$", strict=True)
    administrative_areas: tuple[AdministrativeArea, ...] = Field(default=(), max_length=4)
    provider_bindings: ProviderPlaceBindings = Field(default_factory=ProviderPlaceBindings)
    source_fact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)

    _name = field_validator("canonical_name")(location_label)

    @field_validator("country_name")
    @classmethod
    def country_label(cls, value: str | None) -> str | None:
        return location_label(value) if value is not None else None

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            if not isinstance(value, str) or len(value) > 100 or value != value.strip():
                raise ValueError("Timezone должна быть точным именем IANA.")
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Timezone не найдена в IANA tzdb.") from None
        return value

    @model_validator(mode="after")
    def independent_bindings(self) -> CityRefSnapshot:
        if self.place_id != openmeteo_place_id(self.geographic_source.external_id):
            raise ValueError("place_id не совпадает с проверяемой географической идентичностью.")
        levels = [area.level for area in self.administrative_areas]
        if len(levels) != len(set(levels)):
            raise ValueError("Административный уровень повторяется.")
        for binding in (self.provider_bindings.liteapi, self.provider_bindings.duffel):
            if binding is not None and binding.country_iso2 != self.country_iso2:
                raise ValueError("Страна provider binding не совпадает с географическим снимком.")
        return self

    def snapshot_sha256(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
