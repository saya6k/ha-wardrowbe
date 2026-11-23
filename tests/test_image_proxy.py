"""Image proxy: path confinement and bounded upstream fetches."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import make_mocked_request
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wardrowbe import api
from custom_components.wardrowbe.api import (
    DevTokenProvider,
    WardrowbeApiError,
    WardrowbeClient,
)
from custom_components.wardrowbe.http_views import (
    WardrowbeImageProxyView,
    normalize_image_path,
)

HOST = "http://wardrowbe.test"
IMG = "/api/v1/images/items/i1.jpg"


def _client(hass: HomeAssistant) -> WardrowbeClient:
    return WardrowbeClient(
        async_get_clientsession(hass), HOST, DevTokenProvider("user-123")
    )


@pytest.mark.parametrize(
    "path",
    [
        "api/v1/images/../../docs",
        "api/v1/images/../auth/config",
        "api/v1/imagesX/a.jpg",
        "files/i1.jpg",
    ],
)
def test_paths_outside_images_tree_rejected(path: str) -> None:
    assert normalize_image_path(path) is None


def test_image_path_allowed() -> None:
    assert normalize_image_path("api/v1/images/items/./i1.jpg") == IMG


async def test_fetch_image_returns_body_and_type(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{HOST}{IMG}?sig=abc",
        content=b"\xff\xd8jpeg",
        headers={"Content-Type": "image/jpeg"},
    )
    body, content_type = await _client(hass).async_fetch_image(IMG, "sig=abc")
    assert body == b"\xff\xd8jpeg"
    assert content_type == "image/jpeg"


async def test_fetch_image_passes_upstream_status(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{HOST}{IMG}", status=403)
    with pytest.raises(WardrowbeApiError) as exc:
        await _client(hass).async_fetch_image(IMG, "")
    assert exc.value.status == 403


async def test_fetch_image_caps_size(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "MAX_IMAGE_BYTES", 10)
    aioclient_mock.get(f"{HOST}{IMG}", content=b"x" * 11)
    with pytest.raises(WardrowbeApiError) as exc:
        await _client(hass).async_fetch_image(IMG, "")
    assert exc.value.status == 413


async def test_fetch_image_timeout_has_no_status(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{HOST}{IMG}", exc=TimeoutError())
    with pytest.raises(WardrowbeApiError) as exc:
        await _client(hass).async_fetch_image(IMG, "")
    assert exc.value.status is None


@pytest.mark.parametrize(
    "upstream_type", ["image/jpeg", "image/svg+xml; charset=utf-8"]
)
async def test_view_passes_upstream_content_type(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    upstream_type: str,
) -> None:
    """A ``; charset=`` parameter must not make aiohttp reject the response."""
    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_fetch_image.return_value = (b"img", upstream_type)

    view = WardrowbeImageProxyView(hass)
    request = make_mocked_request("GET", "/api/wardrowbe/image/x?sig=abc")
    response = await view.get(request, dev_mode_entry.entry_id, IMG.lstrip("/"))

    assert response.status == 200
    assert response.headers["Content-Type"] == upstream_type
    assert response.body == b"img"
    mock_client.async_fetch_image.assert_awaited_with(IMG, "sig=abc")


@pytest.mark.parametrize(
    "upstream_type",
    ["text/html", "text/html; charset=utf-8", "application/javascript", "", "IMAGE"],
)
async def test_view_rejects_non_image_content(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    upstream_type: str,
) -> None:
    """Never serve non-image bytes from HA's origin on this unauthenticated view."""
    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_fetch_image.return_value = (b"<script>", upstream_type)

    view = WardrowbeImageProxyView(hass)
    request = make_mocked_request("GET", "/api/wardrowbe/image/x")
    response = await view.get(request, dev_mode_entry.entry_id, IMG.lstrip("/"))

    assert response.status == 502
    assert response.body is None


async def test_view_sandboxes_images(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """SVG is image/* yet scriptable when opened directly; sandbox it."""
    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_fetch_image.return_value = (b"<svg/>", "Image/SVG+XML")

    view = WardrowbeImageProxyView(hass)
    request = make_mocked_request("GET", "/api/wardrowbe/image/x")
    response = await view.get(request, dev_mode_entry.entry_id, IMG.lstrip("/"))

    assert response.status == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response.headers["Content-Security-Policy"]
    assert "script-src" not in response.headers["Content-Security-Policy"]
