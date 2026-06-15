"""Wardrowbe buttons — accept / skip / reject the latest pending outfit."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WardrowbeConfigEntry
from .api import WardrowbeApiError
from .coordinator import WardrowbeCoordinator
from .entity import WardrowbeEntity


@dataclass(frozen=True, kw_only=True, slots=True)
class WardrowbeButtonDescription(ButtonEntityDescription):
    """Describes an outfit-action button."""

    action: str


BUTTONS: tuple[WardrowbeButtonDescription, ...] = (
    WardrowbeButtonDescription(
        key="accept_latest_outfit",
        translation_key="accept_latest_outfit",
        icon="mdi:check-circle-outline",
        action="accept",
    ),
    WardrowbeButtonDescription(
        key="skip_latest_outfit",
        translation_key="skip_latest_outfit",
        icon="mdi:debug-step-over",
        action="skip",
    ),
    WardrowbeButtonDescription(
        key="reject_latest_outfit",
        translation_key="reject_latest_outfit",
        icon="mdi:close-circle-outline",
        action="reject",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WardrowbeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        WardrowbeOutfitButton(coordinator, description) for description in BUTTONS
    )


class WardrowbeOutfitButton(WardrowbeEntity, ButtonEntity):
    """Apply an action to the most recent pending outfit."""

    entity_description: WardrowbeButtonDescription

    def __init__(
        self,
        coordinator: WardrowbeCoordinator,
        description: WardrowbeButtonDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return _latest_pending_outfit_id(self.coordinator) is not None

    async def async_press(self) -> None:
        outfit_id = _latest_pending_outfit_id(self.coordinator)
        if outfit_id is None:
            raise HomeAssistantError("No pending outfit to act on.")
        runtime = self.coordinator.config_entry.runtime_data
        action = self.entity_description.action
        try:
            await runtime.client.async_outfit_action(outfit_id, action)
        except WardrowbeApiError as err:
            raise HomeAssistantError(f"{action}_outfit failed: {err}") from err
        await self.coordinator.async_request_refresh()


def _latest_pending_outfit_id(coordinator: WardrowbeCoordinator) -> str | None:
    data = coordinator.data
    if data is None:
        return None
    for outfit in data.outfits:
        if outfit.get("status") == "pending" and outfit.get("id") is not None:
            return str(outfit["id"])
    return None
