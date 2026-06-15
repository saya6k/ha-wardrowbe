"""Wardrowbe event entities — emit state for outfit/notification/wear/wash events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WardrowbeConfigEntry
from .const import (
    EVENT_GROUP_NOTIFICATION,
    EVENT_GROUP_OUTFIT,
    EVENT_GROUP_WASH,
    EVENT_GROUP_WEAR,
    EVENT_TYPES_NOTIFICATION,
    EVENT_TYPES_OUTFIT,
    EVENT_TYPES_WASH,
    EVENT_TYPES_WEAR,
)
from .coordinator import WardrowbeCoordinator
from .entity import WardrowbeEntity


@dataclass(frozen=True, kw_only=True, slots=True)
class WardrowbeEventDescription(EventEntityDescription):
    group: str


EVENTS: tuple[WardrowbeEventDescription, ...] = (
    WardrowbeEventDescription(
        key="outfit",
        translation_key="outfit",
        group=EVENT_GROUP_OUTFIT,
        event_types=list(EVENT_TYPES_OUTFIT),
        icon="mdi:hanger",
    ),
    WardrowbeEventDescription(
        key="notification",
        translation_key="notification",
        group=EVENT_GROUP_NOTIFICATION,
        event_types=list(EVENT_TYPES_NOTIFICATION),
        icon="mdi:bell-outline",
    ),
    WardrowbeEventDescription(
        key="wear",
        translation_key="wear",
        group=EVENT_GROUP_WEAR,
        event_types=list(EVENT_TYPES_WEAR),
        icon="mdi:tshirt-crew-outline",
    ),
    WardrowbeEventDescription(
        key="wash",
        translation_key="wash",
        group=EVENT_GROUP_WASH,
        event_types=list(EVENT_TYPES_WASH),
        icon="mdi:washing-machine",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WardrowbeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        WardrowbeEvent(coordinator, description) for description in EVENTS
    )


class WardrowbeEvent(WardrowbeEntity, EventEntity):
    """Triggers HA event entity firings whenever the coordinator surfaces a new event."""

    entity_description: WardrowbeEventDescription

    def __init__(
        self,
        coordinator: WardrowbeCoordinator,
        description: WardrowbeEventDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_event_types = list(description.event_types)

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        if data is not None:
            for ev in data.pending_events:
                if ev.group == self.entity_description.group:
                    self._trigger_event(
                        ev.event_type, _safe_attrs(ev.payload)
                    )
        super()._handle_coordinator_update()


def _safe_attrs(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip values that HA's state machine cannot serialise cleanly."""
    return {
        k: v
        for k, v in payload.items()
        if isinstance(v, (str, int, float, bool, list, dict)) or v is None
    }
