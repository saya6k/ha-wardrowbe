"""Diagnostics must not leak credentials, identities, hosts or signed URLs."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wardrowbe.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redacts_sensitive_fields(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
    mock_analytics: dict[str, object],
) -> None:
    mock_analytics["most_worn"] = [
        {"name": "Shirt", "image_url": "/api/v1/images/i1.jpg?sig=secret"}
    ]
    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, dev_mode_entry)

    for key in ("host", "external_id", "user_id", "user_name"):
        assert diag["entry"][key] == "**REDACTED**"
    assert diag["analytics"]["most_worn"][0]["image_url"] == "**REDACTED**"
    assert diag["analytics"]["most_worn"][0]["name"] == "Shirt"
