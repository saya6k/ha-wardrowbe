"""Wardrowbe binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WardrowbeConfigEntry
from .coordinator import WardrowbeData
from .entity import WardrowbeEntity


@dataclass(frozen=True, kw_only=True, slots=True)
class WardrowbeBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[WardrowbeData], bool]


BINARY_SENSORS: tuple[WardrowbeBinarySensorDescription, ...] = (
    WardrowbeBinarySensorDescription(
        key="api_healthy",
        translation_key="api_healthy",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda d: bool(d.healthy),
    ),
    WardrowbeBinarySensorDescription(
        key="has_pending_outfit",
        translation_key="has_pending_outfit",
        icon="mdi:hanger",
        value_fn=lambda d: any(
            (o.get("status") == "pending") for o in d.outfits
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WardrowbeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        WardrowbeBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class WardrowbeBinarySensor(WardrowbeEntity, BinarySensorEntity):
    entity_description: WardrowbeBinarySensorDescription

    def __init__(
        self,
        coordinator: Any,
        description: WardrowbeBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return False
        return self.entity_description.value_fn(self.coordinator.data)
