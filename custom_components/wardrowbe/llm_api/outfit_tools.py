"""Outfit-related LLM tools for Wardrowbe."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from ..api import WardrowbeApiError
from ..const import VALID_OCCASIONS, VALID_TIME_OF_DAY
from .base_tool import BaseWardrowbeTool
from .render import extract_image_url, outfit_to_results


class SuggestOutfitTool(BaseWardrowbeTool):
    name = "suggest_outfit"
    description = (
        "Generate a new outfit recommendation. Optional hints: occasion, "
        "time_of_day, target_date (YYYY-MM-DD), notes."
    )
    parameters = vol.Schema(
        {
            vol.Optional("occasion"): vol.In(VALID_OCCASIONS),
            vol.Optional("time_of_day"): vol.In(VALID_TIME_OF_DAY),
            vol.Optional("target_date"): str,
            vol.Optional("notes"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        runtime = self.runtime
        if runtime is None:
            return self.error("Wardrowbe is not loaded.")
        payload: dict[str, Any] = {}
        for key in ("occasion", "time_of_day", "target_date", "notes"):
            val = tool_input.tool_args.get(key)
            if val is not None:
                payload[key] = val
        try:
            outfit = await runtime.client.async_suggest_outfit(payload)
        except WardrowbeApiError as err:
            return self.error(f"suggest_outfit failed: {err}")
        await runtime.coordinator.async_request_refresh()

        outfit_dict = outfit if isinstance(outfit, dict) else {}
        results = outfit_to_results(outfit_dict, self.host)
        return self.envelope(
            outfit=outfit_dict,
            results=results,
            auto_display=True,
            instruction=(
                "An outfit suggestion has been rendered as image cards. "
                "Tell the user what you suggested in 1-2 short sentences "
                "naming the key pieces; do not list URLs."
            ),
        )


class GetLatestOutfitTool(BaseWardrowbeTool):
    name = "get_latest_outfit"
    description = (
        "Return the most recent outfit (any status) and render its images."
    )
    parameters = vol.Schema({})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        runtime = self.runtime
        if runtime is None or runtime.coordinator.data is None:
            return self.error("Wardrowbe data is not ready.")
        outfits = runtime.coordinator.data.outfits
        if not outfits:
            return self.envelope(
                outfit=None,
                results=[],
                instruction="No outfits in history yet.",
            )
        outfit = outfits[0]
        results = outfit_to_results(outfit, self.host)
        return self.envelope(
            outfit=outfit,
            results=results,
            auto_display=True,
            instruction=(
                "The latest outfit's images are shown. Summarise it in "
                "1-2 sentences."
            ),
        )


class GetRecentOutfitsTool(BaseWardrowbeTool):
    name = "get_recent_outfits"
    description = (
        "List recent outfits as an image gallery. Optional status filter "
        "(pending, accepted, rejected, skipped) and limit (1-12)."
    )
    parameters = vol.Schema(
        {
            vol.Optional("status"): vol.In(
                ("pending", "accepted", "rejected", "skipped")
            ),
            vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=12)),
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        runtime = self.runtime
        if runtime is None or runtime.coordinator.data is None:
            return self.error("Wardrowbe data is not ready.")
        status_filter = tool_input.tool_args.get("status")
        limit = tool_input.tool_args.get("limit") or 6
        outfits = runtime.coordinator.data.outfits
        filtered = [
            outfit
            for outfit in outfits
            if status_filter is None or outfit.get("status") == status_filter
        ][:limit]
        results: list[dict[str, Any]] = []
        for outfit in filtered:
            img = extract_image_url(outfit, self.host)
            if not img:
                continue
            results.append(
                {
                    "image_url": img,
                    "thumbnail_url": img,
                    "title": _short_label(outfit),
                }
            )
        return self.envelope(
            count=len(filtered),
            outfits=filtered,
            results=results,
            auto_display=True,
            instruction=(
                "Recent outfits are shown as a gallery. Mention how many "
                "and the latest one's status; keep it short."
            ),
        )


def _short_label(outfit: dict[str, Any]) -> str:
    bits: list[str] = []
    occ = outfit.get("occasion")
    status = outfit.get("status")
    if occ:
        bits.append(str(occ).title())
    if status:
        bits.append(str(status))
    return " · ".join(bits) or "Outfit"


class _OutfitActionTool(BaseWardrowbeTool):
    """Apply accept/reject/skip to the most recent pending outfit."""

    action: str = ""
    parameters = vol.Schema({})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        runtime = self.runtime
        if runtime is None or runtime.coordinator.data is None:
            return self.error("Wardrowbe data is not ready.")
        outfit_id: str | None = None
        target: dict[str, Any] | None = None
        for outfit in runtime.coordinator.data.outfits:
            if outfit.get("status") == "pending" and outfit.get("id") is not None:
                outfit_id = str(outfit.get("id"))
                target = outfit
                break
        if outfit_id is None:
            return self.error("No pending outfit to act on.")
        try:
            await runtime.client.async_outfit_action(outfit_id, self.action)
        except WardrowbeApiError as err:
            return self.error(f"{self.action}_outfit failed: {err}")
        await runtime.coordinator.async_request_refresh()
        return self.envelope(
            action=self.action,
            outfit_id=outfit_id,
            outfit=target,
            instruction=(
                f"The outfit was {self.action}ed. Confirm in one short "
                "sentence."
            ),
        )


class AcceptLatestOutfitTool(_OutfitActionTool):
    name = "accept_latest_outfit"
    description = "Accept the most recent pending outfit suggestion."
    action = "accept"


class RejectLatestOutfitTool(_OutfitActionTool):
    name = "reject_latest_outfit"
    description = "Reject the most recent pending outfit suggestion."
    action = "reject"


class SkipLatestOutfitTool(_OutfitActionTool):
    name = "skip_latest_outfit"
    description = "Skip the most recent pending outfit suggestion."
    action = "skip"
