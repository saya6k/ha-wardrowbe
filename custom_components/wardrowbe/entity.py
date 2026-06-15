"""Shared entity base for Wardrowbe."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WardrowbeCoordinator


class WardrowbeEntity(CoordinatorEntity[WardrowbeCoordinator]):
    """Base class linking entities to a Wardrowbe service."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WardrowbeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Wardrowbe",
            name=entry.title,
            configuration_url=coordinator.client.host,
            entry_type=DeviceEntryType.SERVICE,
        )
