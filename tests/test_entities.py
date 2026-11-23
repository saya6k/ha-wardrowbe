"""Tests for coordinator-backed entities."""

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
    """``sent`` (notified, unanswered) counts as a pending outfit."""
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


async def test_setup_removes_legacy_button_entities(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Button entities from before the platform was removed are cleaned up."""
    dev_mode_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "button",
        "wardrowbe",
        f"{dev_mode_entry.entry_id}_accept_latest_outfit",
        config_entry=dev_mode_entry,
    )

    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(legacy.entity_id) is None
    assert registry.async_get_entity_id(
        "binary_sensor", "wardrowbe", f"{dev_mode_entry.entry_id}_has_pending_outfit"
    )
