"""Tests for the coordinator's change diffing."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wardrowbe.const import EVENT_GROUP_OUTFIT, EVENT_GROUP_WASH
from custom_components.wardrowbe.coordinator import WardrowbeCoordinator

ITEM = {"id": "i1", "name": "Shirt", "type": "top"}


async def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry
) -> WardrowbeCoordinator:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data.coordinator


async def test_local_wash_suppresses_matching_server_change(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    coordinator = await _coordinator(hass, dev_mode_entry)
    coordinator._diff([], [], [ITEM])
    coordinator.record_local_event(EVENT_GROUP_WASH, "logged", {"item_id": "i1"})
    assert coordinator._diff([], [], []) == []


async def test_unmatched_local_wash_marker_expires(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A wash logged for an item not on the needs-wash list must not hide a later one."""
    coordinator = await _coordinator(hass, dev_mode_entry)
    coordinator._diff([], [], [])
    coordinator.record_local_event(EVENT_GROUP_WASH, "logged", {"item_id": "i1"})
    for _ in range(3):
        coordinator._diff([], [], [])

    coordinator._diff([], [], [ITEM])
    events = coordinator._diff([], [], [])
    assert [(e.group, e.event_type) for e in events] == [(EVENT_GROUP_WASH, "logged")]


async def test_unmatched_local_feedback_marker_expires(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    coordinator = await _coordinator(hass, dev_mode_entry)
    outfit = {"id": "o1", "status": "accepted"}
    coordinator._diff([outfit], [], [])
    coordinator.record_local_event(
        EVENT_GROUP_OUTFIT, "feedback_submitted", {"outfit_id": "o1"}
    )
    # The server never echoes the feedback fields back.
    for _ in range(3):
        coordinator._diff([outfit], [], [])

    events = coordinator._diff([{**outfit, "rating": 5}], [], [])
    assert [(e.group, e.event_type) for e in events] == [
        (EVENT_GROUP_OUTFIT, "feedback_submitted")
    ]
