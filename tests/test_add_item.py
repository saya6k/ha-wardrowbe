"""add_item uploads a photo from an allowed file or a URL as a new item."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.wardrowbe.api import DevTokenProvider, WardrowbeApiError, WardrowbeClient
from custom_components.wardrowbe.const import (
    API_AUTH_SYNC,
    API_ITEMS,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_IMAGE_PATH,
    ATTR_IMAGE_URL,
    ATTR_NAME,
    ATTR_SKIP_AI,
    ATTR_TYPE,
    DOMAIN,
    SERVICE_ADD_ITEM,
)

PNG = b"\x89PNG fake image"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, mock_client: AsyncMock) -> None:
    mock_client.async_create_item = AsyncMock(return_value={"id": "new"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _add(hass: HomeAssistant, entry: MockConfigEntry, **data: object) -> object:
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_ITEM,
        {ATTR_CONFIG_ENTRY_ID: entry.entry_id, **data},
        blocking=True,
        return_response=True,
    )


async def test_url_photo_is_uploaded_with_its_fields(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A generic content type falls back to the URL's extension."""
    aioclient_mock.get(
        "https://img.test/a/shirt.png?v=2",
        content=PNG,
        headers={"Content-Type": "application/octet-stream"},
    )
    await _setup(hass, dev_mode_entry, mock_client)

    result = await _add(
        hass,
        dev_mode_entry,
        **{
            ATTR_IMAGE_URL: "https://img.test/a/shirt.png?v=2",
            ATTR_TYPE: "shirt",
            ATTR_NAME: "Oxford",
            ATTR_SKIP_AI: False,
        },
    )

    assert result == {"id": "new"}
    mock_client.async_create_item.assert_awaited_once_with(
        PNG, "shirt.png", "image/png", {"type": "shirt", "name": "Oxford", "skip_ai": "false"}
    )


async def test_url_filename_follows_the_served_type(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Wardrowbe reads the format from the name, so it must match the bytes."""
    aioclient_mock.get(
        "https://img.test/photo", content=PNG, headers={"Content-Type": "image/jpeg"}
    )
    await _setup(hass, dev_mode_entry, mock_client)

    await _add(hass, dev_mode_entry, **{ATTR_IMAGE_URL: "https://img.test/photo"})

    mock_client.async_create_item.assert_awaited_once_with(PNG, "photo.jpg", "image/jpeg", {})


@pytest.mark.parametrize(
    ("headers", "url"),
    [({"Content-Type": "image/gif"}, "https://img.test/a.gif"), ({}, "https://img.test/a")],
)
async def test_url_that_is_not_a_supported_image_is_refused(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
    headers: dict,
    url: str,
) -> None:
    aioclient_mock.get(url, content=b"GIF89a", headers=headers)
    await _setup(hass, dev_mode_entry, mock_client)

    with pytest.raises(ServiceValidationError):
        await _add(hass, dev_mode_entry, **{ATTR_IMAGE_URL: url})
    mock_client.async_create_item.assert_not_awaited()


async def test_url_download_is_capped(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.get("https://img.test/big.png", content=PNG * 10)
    await _setup(hass, dev_mode_entry, mock_client)

    with (
        patch("custom_components.wardrowbe.item_images.MAX_UPLOAD_BYTES", len(PNG)),
        pytest.raises(ServiceValidationError),
    ):
        await _add(hass, dev_mode_entry, **{ATTR_IMAGE_URL: "https://img.test/big.png"})
    mock_client.async_create_item.assert_not_awaited()


async def test_url_error_status_is_reported(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.get("https://img.test/gone.png", status=404)
    await _setup(hass, dev_mode_entry, mock_client)

    with pytest.raises(HomeAssistantError, match="404"):
        await _add(hass, dev_mode_entry, **{ATTR_IMAGE_URL: "https://img.test/gone.png"})


async def test_allowed_file_is_uploaded(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock, tmp_path: Path
) -> None:
    photo = tmp_path / "Coat.JPEG"
    photo.write_bytes(PNG)
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    await _setup(hass, dev_mode_entry, mock_client)

    await _add(hass, dev_mode_entry, **{ATTR_IMAGE_PATH: str(photo)})

    mock_client.async_create_item.assert_awaited_once_with(PNG, "Coat.JPEG", "image/jpeg", {})


async def test_file_outside_the_allowlist_is_refused(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock, tmp_path: Path
) -> None:
    photo = tmp_path / "coat.jpg"
    photo.write_bytes(PNG)
    hass.config.allowlist_external_dirs = set()
    await _setup(hass, dev_mode_entry, mock_client)

    with pytest.raises(ServiceValidationError, match="allowlist_external_dirs"):
        await _add(hass, dev_mode_entry, **{ATTR_IMAGE_PATH: str(photo)})
    mock_client.async_create_item.assert_not_awaited()


async def test_file_that_is_too_large_or_missing_is_refused(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock, tmp_path: Path
) -> None:
    photo = tmp_path / "coat.png"
    photo.write_bytes(PNG * 10)
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    await _setup(hass, dev_mode_entry, mock_client)

    with (
        patch("custom_components.wardrowbe.item_images.MAX_UPLOAD_BYTES", len(PNG)),
        pytest.raises(ServiceValidationError, match="20 MB"),
    ):
        await _add(hass, dev_mode_entry, **{ATTR_IMAGE_PATH: str(photo)})
    with pytest.raises(ServiceValidationError, match="Cannot read"):
        await _add(hass, dev_mode_entry, **{ATTR_IMAGE_PATH: str(tmp_path / "nope.png")})
    mock_client.async_create_item.assert_not_awaited()


@pytest.mark.parametrize(
    "data",
    [{}, {ATTR_IMAGE_PATH: "/a.jpg", ATTR_IMAGE_URL: "https://img.test/a.jpg"}],
)
async def test_exactly_one_image_source_is_required(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock, data: dict
) -> None:
    await _setup(hass, dev_mode_entry, mock_client)

    with pytest.raises(vol.Invalid):
        await _add(hass, dev_mode_entry, **data)


async def test_duplicate_photo_is_a_validation_error(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.get("https://img.test/a.png", content=PNG)
    await _setup(hass, dev_mode_entry, mock_client)
    mock_client.async_create_item.side_effect = WardrowbeApiError("dup", status=409)

    with pytest.raises(ServiceValidationError, match="already in your wardrobe"):
        await _add(hass, dev_mode_entry, **{ATTR_IMAGE_URL: "https://img.test/a.png"})


async def test_client_sends_the_photo_as_multipart(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    host = "http://wardrowbe.test"
    aioclient_mock.post(
        f"{host}{API_AUTH_SYNC}", json={"access_token": "jwt", "expires_in": 100000}
    )
    aioclient_mock.post(f"{host}{API_ITEMS}", json={"id": "new"})
    client = WardrowbeClient(async_get_clientsession(hass), host, DevTokenProvider("u"))

    result = await client.async_create_item(PNG, "a.png", "image/png", {"name": "Tee"})

    assert result == {"id": "new"}
    sent = next(c[2] for c in aioclient_mock.mock_calls if c[1].path == API_ITEMS)
    fields = {f[0]["name"]: (f[0].get("filename"), f[2]) for f in sent._fields}
    assert fields == {"image": ("a.png", PNG), "name": (None, "Tee")}



async def test_upload_is_rebuilt_when_retried_after_a_401(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A FormData can be sent only once, so the retry needs a fresh one."""
    host = "http://wardrowbe.test"
    aioclient_mock.post(
        f"{host}{API_AUTH_SYNC}", json={"access_token": "jwt", "expires_in": 100000}
    )
    replies = iter([AiohttpClientMockResponse("POST", URL(f"{host}{API_ITEMS}"), status=401)])

    async def _reply(method: str, url: URL, data: object) -> AiohttpClientMockResponse:
        return next(
            replies, AiohttpClientMockResponse(method, url, json={"id": "new"}, status=201)
        )

    aioclient_mock.post(f"{host}{API_ITEMS}", side_effect=_reply)
    client = WardrowbeClient(async_get_clientsession(hass), host, DevTokenProvider("u"))

    assert await client.async_create_item(PNG, "a.png", "image/png", {}) == {"id": "new"}

    sent = [c[2] for c in aioclient_mock.mock_calls if c[1].path == API_ITEMS]
    assert len(sent) == 2
    assert sent[0] is not sent[1]
