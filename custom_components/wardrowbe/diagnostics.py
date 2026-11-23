"""Diagnostics support for Wardrowbe."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import WardrowbeConfigEntry
from .api import IMAGE_URL_KEYS

REDACT_KEYS = {
    "client_secret",
    "external_id",
    "token",
    "id_token",
    "access_token",
    "refresh_token",
    "user_id",
    "user_name",
    "email",
    # Self-hosted server and IdP hostnames are often internal.
    "host",
    "issuer_url",
    "authorize_url",
    "token_url",
    # Presigned: usable by anyone holding them until they expire.
    *IMAGE_URL_KEYS,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WardrowbeConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), REDACT_KEYS),
        "options": dict(entry.options),
        "healthy": getattr(data, "healthy", None),
        "capabilities": getattr(data, "capabilities", None),
        "analytics": async_redact_data(getattr(data, "analytics", None) or {}, REDACT_KEYS),
        "outfits_count": len(getattr(data, "outfits", []) or []),
        "notifications_count": len(getattr(data, "notifications", []) or []),
    }
