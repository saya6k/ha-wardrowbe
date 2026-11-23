"""HTTP view that proxies Wardrowbe image URLs through HA.

Wardrowbe returns item/outfit image URLs as presigned **relative paths**
(``/api/v1/images/...?expires=...&sig=...``). Browsers loading the
response from a different origin (HA dashboard hostname, Glance,
voice-satellite card) resolve those paths against the wrong host and
404. This view rebroadcasts the bytes from HA's own HTTP server so any
consumer just hits ``/api/wardrowbe/image/{entry_id}/...`` on HA.

The view does not require HA auth: the Wardrowbe signature on the URL
already gates access, and bouncing the request through here without
revealing Wardrowbe's own hostname is the whole point.
"""

from __future__ import annotations

import logging
import posixpath

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .api import WardrowbeApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_ALLOWED_PATH_PREFIX = "/api/v1/images/"


def normalize_image_path(path: str) -> str | None:
    """Return the normalised upstream path, or None if it leaves the images tree."""
    # The upstream URL gets its dot-segments collapsed, so check the collapsed
    # form — otherwise ``images/../../x`` would pass a raw prefix check.
    full_path = posixpath.normpath(f"/{path}")
    if not full_path.startswith(_ALLOWED_PATH_PREFIX):
        return None
    return full_path


class WardrowbeImageProxyView(HomeAssistantView):
    """Proxy Wardrowbe presigned image URLs through HA's HTTP layer."""

    url = "/api/wardrowbe/image/{entry_id}/{path:.*}"
    name = "api:wardrowbe:image"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(
        self, request: web.Request, entry_id: str, path: str
    ) -> web.StreamResponse:
        entry = self._hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return web.Response(status=404)
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None:
            return web.Response(status=503)

        full_path = normalize_image_path(path)
        if full_path is None:
            return web.Response(status=403)

        try:
            body, content_type = await runtime.client.async_fetch_image(
                full_path, request.query_string
            )
        except WardrowbeApiError as err:
            if err.status is None:
                _LOGGER.warning("Wardrowbe image proxy failed for %s: %s", full_path, err)
                return web.Response(status=502)
            return web.Response(status=err.status)
        # Passed as a header, not ``content_type=``: aiohttp rejects that
        # argument when it carries parameters such as ``; charset=``.
        return web.Response(
            body=body,
            headers={
                "Content-Type": content_type,
                "Cache-Control": "private, max-age=3600",
            },
        )
