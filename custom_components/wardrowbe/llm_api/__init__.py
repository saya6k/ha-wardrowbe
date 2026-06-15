"""LLM API registration for Wardrowbe.

One ``llm.API`` is registered per config entry so multi-account installs
each appear as a discrete tool source. Tool result envelopes follow the
voice-satellite-card convention (``results: [{image_url, ...}]``,
``featured_image``, ``auto_display``, ``instruction``) so the
voice-satellite Lovelace card auto-renders the rich content.
"""

from __future__ import annotations

import logging
from typing import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from ..const import DOMAIN
from .const import API_NAME, API_PROMPT
from .tools import TOOL_FACTORIES

_LOGGER = logging.getLogger(__name__)


def _api_id(entry_id: str) -> str:
    return f"{DOMAIN}__{entry_id}"


class _WardrowbeAPI(llm.API):
    """An ``llm.API`` bound to a single Wardrowbe config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass=hass,
            id=_api_id(entry.entry_id),
            name=f"{API_NAME} — {entry.title}",
        )
        self._entry_id = entry.entry_id

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        tools = [factory(self.hass, self._entry_id) for factory in TOOL_FACTORIES]
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=tools,
        )


async def async_setup_llm_api(
    hass: HomeAssistant, entry: ConfigEntry
) -> Callable[[], None]:
    """Register an llm.API for this entry; return its unregister callable."""
    api = _WardrowbeAPI(hass, entry)
    unreg = llm.async_register_api(hass, api)
    _LOGGER.info("Registered Wardrowbe LLM API %s", api.id)
    return unreg


def async_cleanup_llm_api(unregister: Callable[[], None] | None) -> None:
    if unregister is None:
        return
    try:
        unregister()
    except Exception as err:  # pragma: no cover
        _LOGGER.debug("Error unregistering Wardrowbe LLM API: %s", err)


__all__ = [
    "async_setup_llm_api",
    "async_cleanup_llm_api",
]
