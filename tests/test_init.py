"""Smoke test for Wardrowbe setup_entry / unload_entry."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_setup_and_unload_dev_mode(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()
    assert dev_mode_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(dev_mode_entry.entry_id)
    await hass.async_block_till_done()
    assert dev_mode_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.usefixtures("mock_client")
async def test_services_registered_after_setup(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
) -> None:
    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service("wardrowbe", "suggest_outfit")
    assert hass.services.has_service("wardrowbe", "log_wear")
