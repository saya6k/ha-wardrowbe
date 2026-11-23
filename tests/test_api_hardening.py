"""Path-id encoding, /auth/sync response hardening, and shared 401 re-sync."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wardrowbe.api import (
    DevTokenProvider,
    WardrowbeApiError,
    WardrowbeAuthError,
    WardrowbeClient,
)
from custom_components.wardrowbe.const import API_AUTH_SESSION, API_AUTH_SYNC

HOST = "http://wardrowbe.test"


def _client(hass: HomeAssistant) -> WardrowbeClient:
    return WardrowbeClient(
        async_get_clientsession(hass), HOST, DevTokenProvider("user-123")
    )


def _sync_ok(aioclient_mock: AiohttpClientMocker, **body: object) -> None:
    aioclient_mock.post(
        f"{HOST}{API_AUTH_SYNC}",
        json={"access_token": "jwt", "expires_in": 100000, **body},
    )


async def test_item_id_cannot_escape_its_path_segment(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An id like ``../outfits/X/reject?`` must not reach another endpoint."""
    _sync_ok(aioclient_mock)
    aioclient_mock.post(
        f"{HOST}/api/v1/items/..%2Foutfits%2FX%2Freject%3F/wash", json={}
    )

    await _client(hass).async_log_wash("../outfits/X/reject?", {})

    urls = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert urls[-1] == f"{HOST}/api/v1/items/..%2Foutfits%2FX%2Freject%3F/wash"


@pytest.mark.parametrize("bad_id", ["", ".", ".."])
async def test_dot_segment_ids_rejected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, bad_id: str
) -> None:
    with pytest.raises(WardrowbeApiError) as exc:
        await _client(hass).async_log_wash(bad_id, {})
    assert exc.value.status == 400
    assert not aioclient_mock.mock_calls


async def test_auth_sync_non_object_body_is_api_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{HOST}{API_AUTH_SYNC}", json=["not", "a", "dict"])
    with pytest.raises(WardrowbeApiError) as exc:
        await _client(hass).async_session_info()
    assert not isinstance(exc.value, WardrowbeAuthError)


async def test_auth_sync_invalid_json_is_api_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{HOST}{API_AUTH_SYNC}", text="<html>proxy error</html>")
    with pytest.raises(WardrowbeApiError) as exc:
        await _client(hass).async_session_info()
    assert not isinstance(exc.value, WardrowbeAuthError)


@pytest.mark.parametrize("expires_in", [None, "soon", ""])
async def test_auth_sync_bad_expires_in_falls_back_to_default(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, expires_in: object
) -> None:
    _sync_ok(aioclient_mock, expires_in=expires_in)
    aioclient_mock.get(f"{HOST}{API_AUTH_SESSION}", json={"id": "u"})
    client = _client(hass)

    await client.async_session_info()
    await client.async_session_info()

    syncs = [c for c in aioclient_mock.mock_calls if str(c[1]).endswith(API_AUTH_SYNC)]
    assert len(syncs) == 1


async def test_short_lived_jwt_is_reused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A TTL under the 1h leeway must not force a re-sync on every request."""
    _sync_ok(aioclient_mock, expires_in=600)
    aioclient_mock.get(f"{HOST}{API_AUTH_SESSION}", json={"id": "u"})
    client = _client(hass)

    await client.async_session_info()
    await client.async_session_info()

    syncs = [c for c in aioclient_mock.mock_calls if str(c[1]).endswith(API_AUTH_SYNC)]
    assert len(syncs) == 1


async def test_stale_generation_resyncs_only_once(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Requests 401'd on the same JWT share one re-sync instead of one each."""
    _sync_ok(aioclient_mock)
    client = _client(hass)

    _, first = await client._ensure_jwt()
    # The first 401'd request re-syncs; the others see a newer generation.
    _, second = await client._ensure_jwt(stale=first)
    _, third = await client._ensure_jwt(stale=first)

    assert second == third != first
    syncs = [c for c in aioclient_mock.mock_calls if str(c[1]).endswith(API_AUTH_SYNC)]
    assert len(syncs) == 2
