"""Outfit weather follows the user's Wardrowbe temperature unit, both ways.

Wardrowbe stores and returns every temperature in Celsius; its
temperature_unit preference only picks the display unit. The client converts
outfit weather for display and converts a weather_override back to Celsius.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wardrowbe.api import (
    DevTokenProvider,
    WardrowbeApiError,
    WardrowbeClient,
    _localize_outfit_weather,
)
from custom_components.wardrowbe.const import (
    API_AUTH_SYNC,
    API_OUTFIT_SUGGEST,
    API_OUTFITS,
    API_PREFERENCES,
)

HOST = "http://wardrowbe.test"

WEATHER_C = {
    "temperature": 10,
    "feels_like": 8.5,
    "temp_min": None,
    "temp_max": 20,
    "window_min": 0,
    "window_max": 15,
    "humidity": 70,
    "precipitation_chance": 80,
    "condition": "rain",
}


def _client(hass: HomeAssistant) -> WardrowbeClient:
    return WardrowbeClient(async_get_clientsession(hass), HOST, DevTokenProvider("user-123"))


def _mock(aioclient_mock: AiohttpClientMocker, unit: str | None, **outfit: object) -> None:
    aioclient_mock.post(
        f"{HOST}{API_AUTH_SYNC}", json={"access_token": "jwt", "expires_in": 100000}
    )
    if unit is None:
        aioclient_mock.get(f"{HOST}{API_PREFERENCES}", status=500)
    else:
        aioclient_mock.get(f"{HOST}{API_PREFERENCES}", json={"temperature_unit": unit})
    aioclient_mock.get(
        f"{HOST}{API_OUTFITS}",
        json=[{"id": "o1", "weather": dict(WEATHER_C)}, {"id": "o2", "weather": None}],
    )
    aioclient_mock.post(
        f"{HOST}{API_OUTFIT_SUGGEST}", json={"id": "o3", "weather": dict(WEATHER_C), **outfit}
    )


def _calls(aioclient_mock: AiohttpClientMocker, path: str) -> list:
    return [c for c in aioclient_mock.mock_calls if c[1].path == path]


async def test_fahrenheit_user_sees_fahrenheit(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock(aioclient_mock, "fahrenheit")

    outfits = await _client(hass).async_recent_outfits()

    assert outfits[0]["weather"] == {
        **WEATHER_C,
        "temperature": 50.0,
        "feels_like": 47.3,
        "temp_max": 68.0,
        "window_min": 32.0,
        "window_max": 59.0,
        "temperature_unit": "fahrenheit",
    }
    assert outfits[1]["weather"] is None


@pytest.mark.parametrize(
    "prefs", [{"temperature_unit": "celsius"}, {}, {"temperature_unit": "kelvin"}]
)
async def test_celsius_or_unknown_unit_stays_celsius(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, prefs: dict
) -> None:
    aioclient_mock.post(
        f"{HOST}{API_AUTH_SYNC}", json={"access_token": "jwt", "expires_in": 100000}
    )
    aioclient_mock.get(f"{HOST}{API_PREFERENCES}", json=prefs)
    aioclient_mock.get(f"{HOST}{API_OUTFITS}", json=[{"id": "o1", "weather": dict(WEATHER_C)}])

    outfits = await _client(hass).async_recent_outfits()

    assert outfits[0]["weather"] == {**WEATHER_C, "temperature_unit": "celsius"}


async def test_unit_is_cached_between_polls(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock(aioclient_mock, "fahrenheit")
    client = _client(hass)

    await client.async_recent_outfits()
    await client.async_recent_outfits()

    assert len(_calls(aioclient_mock, API_PREFERENCES)) == 1


async def test_unreadable_unit_shows_labelled_celsius(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock(aioclient_mock, None)

    outfits = await _client(hass).async_recent_outfits()

    assert outfits[0]["weather"] == {**WEATHER_C, "temperature_unit": "celsius"}


async def test_override_in_fahrenheit_is_sent_in_celsius(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock(aioclient_mock, "fahrenheit")

    result = await _client(hass).async_suggest_outfit(
        {"weather_override": {"temperature": 50, "condition": "rainy"}}
    )

    body = _calls(aioclient_mock, API_OUTFIT_SUGGEST)[0][2]
    assert body == {"weather_override": {"temperature": 10.0, "condition": "rainy"}}
    assert result["weather"]["temperature"] == 50.0
    assert result["weather"]["temperature_unit"] == "fahrenheit"


async def test_explicit_unit_overrides_the_users_unit(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock(aioclient_mock, "fahrenheit")

    await _client(hass).async_suggest_outfit(
        {"weather_override": {"temperature": 12}}, temperature_unit="celsius"
    )

    body = _calls(aioclient_mock, API_OUTFIT_SUGGEST)[0][2]
    assert body == {"weather_override": {"temperature": 12}}


async def test_override_with_unreadable_unit_is_not_sent(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Guessing Celsius here could send 50 °F as 50 °C."""
    _mock(aioclient_mock, None)

    with pytest.raises(WardrowbeApiError):
        await _client(hass).async_suggest_outfit({"weather_override": {"temperature": 50}})

    assert not _calls(aioclient_mock, API_OUTFIT_SUGGEST)


async def test_suggest_without_override_does_not_need_the_unit_up_front(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock(aioclient_mock, None, weather=None)

    result = await _client(hass).async_suggest_outfit({"occasion": "office"})

    assert result["id"] == "o3"
    assert not _calls(aioclient_mock, API_PREFERENCES)


async def test_unit_is_reread_after_the_cache_expires(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    from homeassistant.util import dt as dt_util

    _mock(aioclient_mock, "fahrenheit")
    client = _client(hass)
    now = dt_util.utcnow()

    with patch("custom_components.wardrowbe.api.datetime") as clock:
        clock.now.return_value = now
        await client.async_recent_outfits()
        clock.now.return_value = now + timedelta(minutes=31)
        await client.async_recent_outfits()

    assert len(_calls(aioclient_mock, API_PREFERENCES)) == 2


async def test_override_conversion_rereads_a_cached_unit(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A unit switched in Wardrowbe a minute ago must not convert the input."""
    _mock(aioclient_mock, "fahrenheit")
    client = _client(hass)
    await client.async_recent_outfits()
    assert len(_calls(aioclient_mock, API_PREFERENCES)) == 1

    await client.async_suggest_outfit({"weather_override": {"temperature": 50}})

    assert len(_calls(aioclient_mock, API_PREFERENCES)) == 2


def test_localizing_twice_does_not_convert_twice() -> None:
    weather = {"temperature": 10}
    _localize_outfit_weather(weather, "fahrenheit")
    _localize_outfit_weather(weather, "fahrenheit")
    assert weather == {"temperature": 50.0, "temperature_unit": "fahrenheit"}
