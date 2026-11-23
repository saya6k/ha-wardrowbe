"""Tests for composing outfits outside Wardrowbe (works with internal AI off)."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wardrowbe.api import (
    DevTokenProvider,
    WardrowbeApiError,
    WardrowbeClient,
)
from custom_components.wardrowbe.const import (
    API_AUTH_SYNC,
    API_ITEM_TYPES,
    API_ITEMS,
    API_OUTFIT_SUGGESTIONS,
    DOMAIN,
    SERVICE_CREATE_OUTFIT_SUGGESTION,
)

HOST = "http://wardrowbe.test"


async def test_client_list_items_and_create_suggestion(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    client = WardrowbeClient(
        async_get_clientsession(hass), HOST, DevTokenProvider("user-123")
    )
    aioclient_mock.post(
        f"{HOST}{API_AUTH_SYNC}", json={"access_token": "jwt", "expires_in": 100000}
    )
    aioclient_mock.get(
        f"{HOST}{API_ITEMS}",
        json={"items": [{"id": "i1", "type": "shirt", "image_path": "/files/i1.jpg"}]},
    )
    aioclient_mock.post(
        f"{HOST}{API_OUTFIT_SUGGESTIONS}", json={"id": "o1", "status": "pending"}
    )
    aioclient_mock.get(f"{HOST}{API_ITEM_TYPES}", json=[{"type": "shirt", "count": 3}])

    items = await client.async_list_items(
        item_type="shirt", colors=["navy", "black"], favorite=False, limit=50
    )
    assert [i["id"] for i in items] == ["i1"]
    _, url, _, _ = aioclient_mock.mock_calls[-1]
    assert url.query["type"] == "shirt"
    assert url.query["colors"] == "navy,black"
    assert url.query["favorite"] == "false"
    assert "subtype" not in url.query
    assert url.query["needs_wash"] == "false"
    assert url.query["is_archived"] == "false"
    assert url.query["page_size"] == "50"

    result = await client.async_create_outfit_suggestion(
        {"items": ["i1", "i2"], "occasion": "casual"}
    )
    assert result["id"] == "o1"
    _, url, body, _ = aioclient_mock.mock_calls[-1]
    assert url.path == API_OUTFIT_SUGGESTIONS
    assert body == {"items": ["i1", "i2"], "occasion": "casual"}

    assert await client.async_item_types() == [{"type": "shirt", "count": 3}]


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_create_outfit_suggestion_service(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    mock_client.async_create_outfit_suggestion = AsyncMock(
        return_value={"id": "o1", "status": "pending"}
    )
    await _setup(hass, dev_mode_entry)

    result = await hass.services.async_call(
        DOMAIN,
        SERVICE_CREATE_OUTFIT_SUGGESTION,
        {
            "config_entry_id": dev_mode_entry.entry_id,
            "item_ids": ["i1", "i2"],
            "occasion": "office",
            "scheduled_for": "2026-11-24",
            "reasoning": "Navy and grey for a client meeting",
        },
        blocking=True,
        return_response=True,
    )

    assert result == {"id": "o1", "status": "pending"}
    mock_client.async_create_outfit_suggestion.assert_awaited_once_with(
        {
            "items": ["i1", "i2"],
            "occasion": "office",
            "scheduled_for": date(2026, 11, 24).isoformat(),
            "reasoning": "Navy and grey for a client meeting",
        }
    )


async def test_create_outfit_suggestion_service_api_error(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    mock_client.async_create_outfit_suggestion = AsyncMock(
        side_effect=WardrowbeApiError("forbidden", status=403)
    )
    await _setup(hass, dev_mode_entry)

    with pytest.raises(HomeAssistantError, match="create_outfit_suggestion failed"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_OUTFIT_SUGGESTION,
            {
                "config_entry_id": dev_mode_entry.entry_id,
                "item_ids": ["someone-elses-item"],
                "occasion": "casual",
            },
            blocking=True,
        )
