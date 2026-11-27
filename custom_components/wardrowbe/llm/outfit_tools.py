"""Outfit-related LLM tools for Wardrowbe."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from custom_components.wardrowbe.api import WardrowbeApiError, weather_override_payload
from custom_components.wardrowbe.const import (
    ACTIONABLE_OUTFIT_STATUSES,
    EVENT_GROUP_OUTFIT,
    TEMPERATURE_UNITS,
    VALID_OCCASIONS,
    VALID_PRECIPITATION_CHANCE,
    VALID_TEMPERATURE,
    VALID_TIME_OF_DAY,
    WEATHER_CONDITIONS,
)

from .base_tool import BaseWardrowbeTool
from .render import extract_image_url, outfit_to_results


class SuggestOutfitTool(BaseWardrowbeTool):
    name = "wardrowbe__suggest_outfit"
    title = "Suggest outfit"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=False, idempotent=False, open_world=True
    )
    description = (
        "Generate a new outfit recommendation from the wardrobe. Call this "
        "when the user asks what to wear, wants an outfit for an occasion, "
        "or wants an alternative to a previous suggestion. All arguments "
        "are optional hints, not requirements: occasion (e.g. work, "
        "casual, formal) and time_of_day. Wardrowbe always suggests for "
        "today and takes no free-text hints. By default it uses the live "
        "weather at the user's location; to plan for other weather (the user "
        "says it's raining, or a Home Assistant forecast says otherwise), pass "
        "temperature, optionally with "
        f"weather_condition ({', '.join(WEATHER_CONDITIONS)}) and "
        "precipitation_chance (0-100; defaults from the condition). "
        "temperature is read in the user's Wardrowbe unit unless you set "
        "temperature_unit (celsius or fahrenheit) to the unit your number is "
        "in. Weather in the returned outfit is in weather.temperature_unit. "
        "Produces a pending outfit; follow up with "
        "wardrowbe__accept_latest_outfit, wardrowbe__reject_latest_outfit, or "
        "wardrowbe__skip_latest_outfit once the user reacts to it. If it "
        "reports that Wardrowbe's internal AI is disabled, compose the outfit "
        "yourself with wardrowbe__get_wardrobe_items and "
        "wardrowbe__create_outfit_suggestion instead."
    )
    parameters = vol.Schema(
        {
            vol.Optional("occasion"): vol.In(VALID_OCCASIONS),
            vol.Optional("time_of_day"): vol.In(VALID_TIME_OF_DAY),
            vol.Optional("temperature"): VALID_TEMPERATURE,
            vol.Optional("weather_condition"): vol.In(WEATHER_CONDITIONS),
            vol.Optional("precipitation_chance"): VALID_PRECIPITATION_CHANCE,
            vol.Optional("temperature_unit"): vol.In(TEMPERATURE_UNITS),
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        runtime = self.runtime
        if runtime is None:
            return self.error("Wardrowbe is not loaded.")
        payload: dict[str, Any] = {}
        args = tool_input.tool_args
        for key in ("occasion", "time_of_day"):
            val = args.get(key)
            if val is not None:
                payload[key] = val
        weather = weather_override_payload(
            args.get("temperature"),
            args.get("weather_condition"),
            args.get("precipitation_chance"),
        )
        if weather is not None:
            payload["weather_override"] = weather
        elif args.get("weather_condition") or args.get("precipitation_chance") is not None:
            return self.error(
                "Pass temperature (°C) along with weather_condition or precipitation_chance."
            )
        try:
            if weather is not None and args.get("temperature_unit"):
                outfit = await runtime.client.async_suggest_outfit(
                    payload, temperature_unit=args["temperature_unit"]
                )
            else:
                outfit = await runtime.client.async_suggest_outfit(payload)
        except WardrowbeApiError as err:
            if err.status == 503:
                return self.error(
                    "Wardrowbe's internal AI is disabled, so it cannot generate "
                    "suggestions. Compose the outfit yourself: call "
                    "wardrowbe__get_wardrobe_items, pick a coherent set of items, "
                    "then call wardrowbe__create_outfit_suggestion."
                )
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


class CreateOutfitSuggestionTool(BaseWardrowbeTool):
    name = "wardrowbe__create_outfit_suggestion"
    title = "Create outfit suggestion"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=False, idempotent=False, open_world=True
    )
    description = (
        "Save an outfit you composed yourself as a pending suggestion in "
        "Wardrowbe. Works even when Wardrowbe's internal AI is disabled. First "
        "get item ids from wardrowbe__get_wardrobe_items, then pass 1-20 "
        "item_ids (typically top, bottom, shoes, optional outer layer and "
        "accessories) and an occasion. Optional: name, reasoning (why these "
        "pieces work together), notes, scheduled_for (YYYY-MM-DD, defaults to "
        "today). Follow up with wardrowbe__accept_latest_outfit, "
        "wardrowbe__reject_latest_outfit, or wardrowbe__skip_latest_outfit once "
        "the user reacts to it."
    )
    parameters = vol.Schema(
        {
            vol.Required("item_ids"): vol.All([str], vol.Length(min=1, max=20)),
            vol.Required("occasion"): vol.In(VALID_OCCASIONS),
            vol.Optional("name"): str,
            vol.Optional("reasoning"): str,
            vol.Optional("notes"): str,
            vol.Optional("scheduled_for"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        runtime = self.runtime
        if runtime is None:
            return self.error("Wardrowbe is not loaded.")
        args = tool_input.tool_args
        payload: dict[str, Any] = {
            "items": args["item_ids"],
            "occasion": args["occasion"],
        }
        for key in ("name", "reasoning", "notes", "scheduled_for"):
            if args.get(key) is not None:
                payload[key] = args[key]
        try:
            outfit = await runtime.client.async_create_outfit_suggestion(payload)
        except WardrowbeApiError as err:
            return self.error(f"create_outfit_suggestion failed: {err}")
        await runtime.coordinator.async_request_refresh()

        outfit_dict = outfit if isinstance(outfit, dict) else {}
        return self.envelope(
            outfit=outfit_dict,
            results=outfit_to_results(outfit_dict, self.host),
            auto_display=True,
            instruction=(
                "The outfit was saved as a pending suggestion and rendered as "
                "image cards. Tell the user what you put together in 1-2 short "
                "sentences; do not list URLs."
            ),
        )


class GetLatestOutfitTool(BaseWardrowbeTool):
    name = "wardrowbe__get_latest_outfit"
    title = "Get latest outfit"
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    description = (
        "Return the single most recent outfit regardless of its status "
        "(pending, accepted, rejected, or skipped) and render its images. "
        "Call this when the user wants a reminder of what was last "
        "suggested, without generating a new outfit."
    )
    parameters = vol.Schema({})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
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
    name = "wardrowbe__get_recent_outfits"
    title = "Get recent outfits"
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    description = (
        "List recent outfits as an image gallery, most recent first. Call "
        "this when the user wants to browse outfit history rather than "
        "act on the current pending suggestion. Optional status filter "
        "(pending, accepted, rejected, skipped) narrows the list; "
        "optional limit (1-12, default 6) caps how many are returned."
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
    ) -> llm.ToolResult:
        runtime = self.runtime
        if runtime is None or runtime.coordinator.data is None:
            return self.error("Wardrowbe data is not ready.")
        status_filter = tool_input.tool_args.get("status")
        limit = tool_input.tool_args.get("limit") or 6
        outfits = runtime.coordinator.data.outfits
        # "sent" is just "pending" after the server notified the user.
        wanted = (
            ACTIONABLE_OUTFIT_STATUSES if status_filter == "pending" else (status_filter,)
        )
        filtered = [
            outfit
            for outfit in outfits
            if status_filter is None or outfit.get("status") in wanted
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
    ) -> llm.ToolResult:
        runtime = self.runtime
        if runtime is None or runtime.coordinator.data is None:
            return self.error("Wardrowbe data is not ready.")
        outfit_id: str | None = None
        target: dict[str, Any] | None = None
        for outfit in runtime.coordinator.data.outfits:
            if outfit.get("status") in ACTIONABLE_OUTFIT_STATUSES and outfit.get("id") is not None:
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
    name = "wardrowbe__accept_latest_outfit"
    title = "Accept latest outfit"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=True, idempotent=False, open_world=True
    )
    description = (
        "Mark the most recent pending outfit suggestion as accepted, "
        "meaning the user will wear it. Call this after wardrowbe__suggest_outfit "
        "has produced a pending outfit and the user confirms they like "
        "it. Fails if there is no pending outfit."
    )
    action = "accept"


class RejectLatestOutfitTool(_OutfitActionTool):
    name = "wardrowbe__reject_latest_outfit"
    title = "Reject latest outfit"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=True, idempotent=False, open_world=True
    )
    description = (
        "Mark the most recent pending outfit suggestion as rejected, "
        "meaning the user does not want to wear it. Call this when the "
        "user dislikes the suggestion; consider calling wardrowbe__suggest_outfit "
        "again afterwards for a new option. Fails if there is no pending "
        "outfit."
    )
    action = "reject"


class SkipLatestOutfitTool(_OutfitActionTool):
    name = "wardrowbe__skip_latest_outfit"
    title = "Skip latest outfit"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=True, idempotent=False, open_world=True
    )
    description = (
        "Mark the most recent pending outfit suggestion as skipped, "
        "meaning the user is deferring the decision without accepting or "
        "rejecting it. Call this when the user wants to postpone "
        "deciding. Fails if there is no pending outfit."
    )
    action = "skip"


_RATING = vol.All(int, vol.Range(min=1, max=5))
_FEEDBACK_KEYS = ("rating", "comfort_rating", "style_rating", "comment", "worn")


class RateOutfitTool(BaseWardrowbeTool):
    name = "wardrowbe__rate_outfit"
    title = "Rate outfit"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=True, idempotent=False, open_world=True
    )
    description = (
        "Save the user's feedback on an outfit so Wardrowbe learns their taste: "
        "rating, comfort_rating and style_rating (each 1-5), a short comment, "
        "and worn (true once they actually wore it). Pass at least one of "
        "these. Targets outfit_id (e.g. from wardrowbe__get_recent_outfits) or, "
        "if omitted, the most recent outfit. Setting worn true also logs a "
        "wear for every item in the outfit, so do not call "
        "wardrowbe__log_wear for those items as well. Use the accept/reject "
        "tools, not this one, to answer a pending suggestion."
    )
    parameters = vol.Schema(
        {
            vol.Optional("outfit_id"): str,
            vol.Optional("rating"): _RATING,
            vol.Optional("comfort_rating"): _RATING,
            vol.Optional("style_rating"): _RATING,
            vol.Optional("comment"): vol.All(str, vol.Length(max=1000)),
            vol.Optional("worn"): bool,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        runtime = self.runtime
        if runtime is None or runtime.coordinator.data is None:
            return self.error("Wardrowbe data is not ready.")
        args = tool_input.tool_args
        payload = {key: args[key] for key in _FEEDBACK_KEYS if args.get(key) not in (None, "")}
        if not payload:
            return self.error("Provide a rating, comment or worn.")
        outfit_id = args.get("outfit_id")
        if not outfit_id:
            outfits = runtime.coordinator.data.outfits
            if not outfits or outfits[0].get("id") is None:
                return self.error("No outfit to rate.")
            outfit_id = str(outfits[0]["id"])
        try:
            await runtime.client.async_post_outfit_feedback(outfit_id, payload)
        except WardrowbeApiError as err:
            return self.error(f"rate_outfit failed: {err}")
        runtime.coordinator.record_local_event(
            EVENT_GROUP_OUTFIT, "feedback_submitted", {"outfit_id": outfit_id, **payload}
        )
        await runtime.coordinator.async_request_refresh()
        return self.envelope(
            outfit_id=outfit_id,
            feedback=payload,
            instruction="The feedback was saved. Confirm in one short sentence.",
        )
