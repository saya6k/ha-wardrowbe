"""Tests for the coordinator's change diffing."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.wardrowbe.api import WardrowbeApiError, WardrowbeAuthError
from custom_components.wardrowbe.const import (
    EVENT_GROUP_OUTFIT,
    EVENT_GROUP_WASH,
    bus_event_name,
)
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


async def test_truncated_wash_page_does_not_infer_washes(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A full page may be cut off, so items missing from it weren't washed."""
    coordinator = await _coordinator(hass, dev_mode_entry)
    coordinator._diff([], [], [ITEM])
    assert coordinator._diff([], [], [], wash_truncated=True) == []


async def test_archive_suppresses_wash_inference(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    mock_client.async_items_needing_wash.return_value = [ITEM]
    coordinator = await _coordinator(hass, dev_mode_entry)
    washes = async_capture_events(hass, bus_event_name(EVENT_GROUP_WASH, "logged"))

    # The archived item drops off the needs-wash list on the next poll.
    mock_client.async_items_needing_wash.return_value = []
    await hass.services.async_call(
        "wardrowbe",
        "archive_item",
        {"config_entry_id": dev_mode_entry.entry_id, "item_id": "i1"},
        blocking=True,
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert washes == []


async def test_auth_error_wins_over_other_poll_failures(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Polls run concurrently; any auth failure must still trigger reauth."""
    coordinator = await _coordinator(hass, dev_mode_entry)
    mock_client.async_analytics.side_effect = WardrowbeApiError("boom", status=500)
    mock_client.async_recent_notifications.side_effect = WardrowbeAuthError("401")

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    assert any(
        flow["context"].get("source") == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )
