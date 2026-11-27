"""The feedback and wear services send the field names Wardrowbe reads.

Wardrowbe's request models ignore unknown keys, so a misnamed field is
dropped without an error. These tests pin the wire names.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wardrowbe.const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DATE,
    ATTR_ITEM_ID,
    ATTR_NOTES,
    ATTR_OUTFIT_ID,
    ATTR_RATING,
    ATTR_WORE,
    DOMAIN,
    SERVICE_LOG_WEAR,
    SERVICE_SUBMIT_FEEDBACK,
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_submit_feedback_sends_worn_and_comment(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    mock_client.async_post_outfit_feedback = AsyncMock(return_value={})
    await _setup(hass, dev_mode_entry)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUBMIT_FEEDBACK,
        {
            ATTR_CONFIG_ENTRY_ID: dev_mode_entry.entry_id,
            ATTR_OUTFIT_ID: "o1",
            ATTR_RATING: 4,
            ATTR_WORE: True,
            ATTR_NOTES: "too warm",
        },
        blocking=True,
    )

    mock_client.async_post_outfit_feedback.assert_awaited_once_with(
        "o1", {"rating": 4, "worn": True, "comment": "too warm"}
    )


async def test_log_wear_sends_worn_at(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    mock_client.async_log_wear = AsyncMock(return_value={})
    await _setup(hass, dev_mode_entry)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_LOG_WEAR,
        {
            ATTR_CONFIG_ENTRY_ID: dev_mode_entry.entry_id,
            ATTR_ITEM_ID: "i1",
            ATTR_DATE: date(2026, 11, 26),
        },
        blocking=True,
    )

    mock_client.async_log_wear.assert_awaited_once_with("i1", {"worn_at": "2026-11-26"})
