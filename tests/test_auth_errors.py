"""Transient outages must not be classified as auth failures (no reauth)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
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
from custom_components.wardrowbe.oauth2 import OIDCTokenProvider

HOST = "http://wardrowbe.test"


def _client(hass: HomeAssistant) -> WardrowbeClient:
    return WardrowbeClient(
        async_get_clientsession(hass), HOST, DevTokenProvider("user-123")
    )


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_auth_sync_outage_is_api_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    aioclient_mock.post(f"{HOST}{API_AUTH_SYNC}", status=status)
    with pytest.raises(WardrowbeApiError) as exc:
        await _client(hass).async_session_info()
    assert not isinstance(exc.value, WardrowbeAuthError)
    assert exc.value.status == status


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_auth_sync_rejection_is_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    aioclient_mock.post(f"{HOST}{API_AUTH_SYNC}", status=status)
    with pytest.raises(WardrowbeAuthError):
        await _client(hass).async_session_info()
    assert aioclient_mock.mock_calls[-1][1].path == API_AUTH_SYNC
    assert not any(c[1].path == API_AUTH_SESSION for c in aioclient_mock.mock_calls)


def _oidc_provider(side_effect: BaseException) -> OIDCTokenProvider:
    provider = OIDCTokenProvider.__new__(OIDCTokenProvider)
    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock(side_effect=side_effect)
    provider._oauth_session = session
    return provider


def _response_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(MagicMock(), (), status=status)


@pytest.mark.parametrize(
    "err",
    [
        _response_error(503),
        _response_error(429),
        aiohttp.ClientConnectionError(),
        TimeoutError(),
    ],
)
async def test_oidc_refresh_outage_is_api_error(err: BaseException) -> None:
    with pytest.raises(WardrowbeApiError) as exc:
        await _oidc_provider(err).async_get_sync_payload()
    assert not isinstance(exc.value, WardrowbeAuthError)


@pytest.mark.parametrize("status", [400, 401])
async def test_oidc_refresh_rejected_is_auth_error(status: int) -> None:
    with pytest.raises(WardrowbeAuthError):
        await _oidc_provider(_response_error(status)).async_get_sync_payload()
