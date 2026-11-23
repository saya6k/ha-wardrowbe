"""Tests for coordinator-backed button and binary_sensor entities."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_sent_outfit_is_actionable(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """``sent`` (notified, unanswered) counts as pending for every surface."""
    mock_client.async_recent_outfits = AsyncMock(
        return_value=[{"id": "o1", "status": "sent"}]
    )
    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)

    def _entity_id(platform: str, key: str) -> str:
        entity_id = registry.async_get_entity_id(
            platform, "wardrowbe", f"{dev_mode_entry.entry_id}_{key}"
        )
        assert entity_id is not None
        return entity_id

    assert hass.states.get(_entity_id("binary_sensor", "has_pending_outfit")).state == STATE_ON

    button = _entity_id("button", "accept_latest_outfit")
    assert hass.states.get(button).state != "unavailable"
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    mock_client.async_outfit_action.assert_awaited_once_with("o1", "accept")
