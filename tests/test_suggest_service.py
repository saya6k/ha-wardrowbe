"""Tests for the suggest_outfit AI-capability gating and 503 handling.

* fail-fast / 503-deferred → ServiceValidationError reaches the caller
* any other API error → HomeAssistantError
* a stale config_entry_id is swallowed by ``_guard`` (logged, returns None)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wardrowbe.api import WardrowbeApiError
from custom_components.wardrowbe.const import (
    ATTR_CONFIG_ENTRY_ID,
    DOMAIN,
    SERVICE_SUGGEST_OUTFIT,
)
from custom_components.wardrowbe.services import _text_ai_disabled


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        ({"ai": {"text": False}}, True),  # explicitly off → disabled
        ({"ai": {"text": True}}, False),  # explicitly on
        ({"ai": {"vision": False}}, False),  # text unknown → assume enabled
        ({"ai": {}}, False),
        ({}, False),
        (None, False),  # not polled / server < 1.4.0 → assume enabled
        ("garbage", False),  # malformed → assume enabled
    ],
)
def test_text_ai_disabled(capabilities: object, expected: bool) -> None:
    coordinator = SimpleNamespace(data=SimpleNamespace(capabilities=capabilities))
    assert _text_ai_disabled(coordinator) is expected


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _call(hass: HomeAssistant, entry_id: str) -> None:
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUGGEST_OUTFIT,
        {ATTR_CONFIG_ENTRY_ID: entry_id},
        blocking=True,
    )


async def _call_with_response(hass: HomeAssistant, entry: MockConfigEntry) -> object:
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_SUGGEST_OUTFIT,
        {ATTR_CONFIG_ENTRY_ID: entry.entry_id},
        blocking=True,
        return_response=True,
    )


async def test_suggest_fails_fast_when_text_ai_disabled(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """With text AI reported off, the doomed POST is never attempted."""
    mock_client.async_capabilities = AsyncMock(return_value={"ai": {"text": False}})
    mock_client.async_suggest_outfit = AsyncMock(return_value={"id": "o1"})
    await _setup(hass, dev_mode_entry)

    with pytest.raises(ServiceValidationError, match="internal AI is disabled"):
        await _call_with_response(hass, dev_mode_entry)

    mock_client.async_suggest_outfit.assert_not_called()


async def test_suggest_503_raises_validation_error(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """A server-side 503 (AI off) maps to a ServiceValidationError for the caller."""
    mock_client.async_capabilities = AsyncMock(return_value={"ai": {"text": True}})
    mock_client.async_suggest_outfit = AsyncMock(
        side_effect=WardrowbeApiError("deferred", status=503)
    )
    await _setup(hass, dev_mode_entry)

    with pytest.raises(ServiceValidationError, match="internal AI is disabled"):
        await _call(hass, dev_mode_entry.entry_id)

    mock_client.async_suggest_outfit.assert_called_once()


async def test_suggest_non_503_error_propagates(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """A generic API failure stays a HomeAssistantError (not swallowed)."""
    mock_client.async_capabilities = AsyncMock(return_value={"ai": {"text": True}})
    mock_client.async_suggest_outfit = AsyncMock(
        side_effect=WardrowbeApiError("boom", status=500)
    )
    await _setup(hass, dev_mode_entry)

    with pytest.raises(HomeAssistantError):
        await _call(hass, dev_mode_entry.entry_id)


async def test_suggest_happy_path_returns_result(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    mock_client.async_capabilities = AsyncMock(return_value={"ai": {"text": True}})
    mock_client.async_suggest_outfit = AsyncMock(return_value={"id": "o1"})
    await _setup(hass, dev_mode_entry)

    result = await _call_with_response(hass, dev_mode_entry)

    assert result == {"id": "o1"}
    mock_client.async_suggest_outfit.assert_called_once()


async def test_unknown_entry_is_swallowed(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """A stale config_entry_id from an old automation is logged, not raised."""
    await _setup(hass, dev_mode_entry)

    await _call(hass, "does-not-exist")

    mock_client.async_suggest_outfit.assert_not_called()


@pytest.mark.parametrize("field", ["target_date", "notes"])
async def test_suggest_rejects_fields_wardrowbe_does_not_take(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    field: str,
) -> None:
    """These used to be accepted and then silently dropped by the server."""
    mock_client.async_suggest_outfit = AsyncMock(return_value={"id": "o1"})
    await _setup(hass, dev_mode_entry)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SUGGEST_OUTFIT,
            {ATTR_CONFIG_ENTRY_ID: dev_mode_entry.entry_id, field: "2026-11-28"},
            blocking=True,
        )
    mock_client.async_suggest_outfit.assert_not_called()


async def test_suggest_sends_only_supported_fields(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    mock_client.async_suggest_outfit = AsyncMock(return_value={"id": "o1"})
    await _setup(hass, dev_mode_entry)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUGGEST_OUTFIT,
        {
            ATTR_CONFIG_ENTRY_ID: dev_mode_entry.entry_id,
            "occasion": "office",
            "time_of_day": "morning",
        },
        blocking=True,
        return_response=True,
    )

    mock_client.async_suggest_outfit.assert_awaited_once_with(
        {"occasion": "office", "time_of_day": "morning"}
    )


async def _suggest_with(hass: HomeAssistant, entry: MockConfigEntry, **data: object) -> None:
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUGGEST_OUTFIT,
        {ATTR_CONFIG_ENTRY_ID: entry.entry_id, **data},
        blocking=True,
        return_response=True,
    )


@pytest.mark.parametrize(
    ("data", "override"),
    [
        # A bare condition gets the rain chance Wardrowbe's own UI sends for it.
        (
            {"temperature": 12, "weather_condition": "rainy"},
            {"temperature": 12.0, "condition": "rainy", "precipitation_chance": 80},
        ),
        (
            {"temperature": 25, "weather_condition": "sunny", "precipitation_chance": 40},
            {"temperature": 25.0, "condition": "sunny", "precipitation_chance": 40},
        ),
        ({"temperature": -3}, {"temperature": -3.0}),
    ],
)
async def test_suggest_sends_weather_override(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    data: dict,
    override: dict,
) -> None:
    mock_client.async_suggest_outfit = AsyncMock(return_value={"id": "o1"})
    await _setup(hass, dev_mode_entry)

    await _suggest_with(hass, dev_mode_entry, **data)

    mock_client.async_suggest_outfit.assert_awaited_once_with({"weather_override": override})


@pytest.mark.parametrize(
    "data", [{"weather_condition": "rainy"}, {"precipitation_chance": 50}]
)
async def test_suggest_weather_needs_temperature(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    data: dict,
) -> None:
    mock_client.async_suggest_outfit = AsyncMock(return_value={"id": "o1"})
    await _setup(hass, dev_mode_entry)

    with pytest.raises(vol.Invalid):
        await _suggest_with(hass, dev_mode_entry, **data)
    mock_client.async_suggest_outfit.assert_not_called()
