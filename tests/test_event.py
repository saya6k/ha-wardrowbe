"""Tests for the Wardrowbe event entities."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wardrowbe.api import WardrowbeApiError
from custom_components.wardrowbe.const import EVENT_GROUP_WASH


async def test_failed_refresh_does_not_refire_events(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """A failed refresh re-notifies listeners with stale data; don't fire twice."""
    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = er.async_get(hass).async_get_entity_id(
        "event", "wardrowbe", f"{dev_mode_entry.entry_id}_wash"
    )
    assert entity_id is not None
    assert hass.states.get(entity_id).state == STATE_UNKNOWN

    coordinator = dev_mode_entry.runtime_data.coordinator
    coordinator.record_local_event(EVENT_GROUP_WASH, "logged", {"item_id": "i1"})
    await hass.async_block_till_done()
    fired_at = hass.states.get(entity_id).state
    assert fired_at != STATE_UNKNOWN

    mock_client.async_analytics.side_effect = WardrowbeApiError("boom")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success

    mock_client.async_analytics.side_effect = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success

    assert hass.states.get(entity_id).state == fired_at
