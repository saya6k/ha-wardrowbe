"""Wardrobe-stat / item LLM tools for Wardrowbe."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm

from custom_components.wardrowbe.api import WardrowbeApiError
from custom_components.wardrowbe.const import (
    ATTR_IMAGE_PATH,
    ATTR_IMAGE_URL,
    EVENT_GROUP_WASH,
    EVENT_GROUP_WEAR,
    ITEM_COLORS,
    ITEM_FORMALITY,
    ITEM_SEASONS,
    VALID_OCCASIONS,
)
from custom_components.wardrowbe.item_images import async_add_item

from .base_tool import BaseWardrowbeTool
from .render import extract_image_url, svg_summary


class GetWardrobeSummaryTool(BaseWardrowbeTool):
    name = "wardrowbe__get_wardrobe_summary"
    title = "Get wardrobe summary"
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    description = (
        "Return overall wardrobe statistics: item counts by status "
        "(ready, processing, archived), total and recent outfit counts, "
        "outfit acceptance rate, average outfit rating, and total wears. "
        "Call this when the user asks general questions about their "
        "wardrobe or outfit habits rather than about one specific outfit "
        "or item."
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
        analytics = runtime.coordinator.data.analytics or {}
        wardrobe = analytics.get("wardrobe") or {}
        items_by_status = wardrobe.get("items_by_status") or {}

        def _num(value: Any) -> str:
            if value is None:
                return "—"
            if isinstance(value, float):
                return f"{value:.1f}"
            return str(value)

        lines: list[tuple[str, str]] = [
            ("Total items", _num(wardrobe.get("total_items"))),
            ("Ready", _num(items_by_status.get("ready"))),
            ("Processing", _num(items_by_status.get("processing"))),
            ("Archived", _num(items_by_status.get("archived"))),
            ("Total outfits", _num(wardrobe.get("total_outfits"))),
            ("This week", _num(wardrobe.get("outfits_this_week"))),
            ("This month", _num(wardrobe.get("outfits_this_month"))),
            ("Acceptance %", _num(wardrobe.get("acceptance_rate"))),
            ("Avg rating", _num(wardrobe.get("average_rating"))),
            ("Total wears", _num(wardrobe.get("total_wears"))),
        ]
        entry = self.entry
        subtitle = entry.title if entry is not None else None
        card = svg_summary("Wardrobe summary", lines, subtitle=subtitle)
        return self.envelope(
            wardrobe=wardrobe,
            featured_image=card,
            results=[],
            auto_display=True,
            instruction=(
                "A summary card is shown. Mention 1-2 stats the user "
                "would most likely care about; keep it short."
            ),
        )


class GetMostWornItemsTool(BaseWardrowbeTool):
    name = "wardrowbe__get_most_worn_items"
    title = "Get most worn items"
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    description = (
        "List the user's most-worn wardrobe items as an image gallery, "
        "ranked by wear count. Call this when the user asks what they "
        "wear most often or which pieces get the most use. Optional "
        "limit (1-10, default 5) caps how many items are returned."
    )
    parameters = vol.Schema(
        {vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=10))}
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
        limit = tool_input.tool_args.get("limit") or 5
        analytics = runtime.coordinator.data.analytics or {}
        most_worn = analytics.get("most_worn") or []
        if not isinstance(most_worn, list):
            most_worn = []

        results: list[dict[str, Any]] = []
        for item in most_worn[:limit]:
            if not isinstance(item, dict):
                continue
            img = extract_image_url(item, self.host)
            if not img:
                continue
            title = item.get("name") or item.get("category") or "Item"
            wears = item.get("wear_count") or item.get("wears")
            full_title = f"{title} · {wears}" if wears is not None else str(title)
            results.append(
                {
                    "image_url": img,
                    "thumbnail_url": img,
                    "title": full_title,
                }
            )

        return self.envelope(
            count=len(results),
            most_worn=most_worn[:limit],
            results=results,
            auto_display=True,
            instruction=(
                "Top items are shown as a gallery. Mention the top 1-2 "
                "by name; keep it brief."
            ),
        )


# Enough for the model to pick a coherent outfit without the full item payload.
_ITEM_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "type",
    "subtype",
    "primary_color",
    "colors",
    "pattern",
    "material",
    "style",
    "formality",
    "season",
    "favorite",
    "wear_count",
    "last_worn_at",
)
# User-entered size and AI-tagged fit only exist inside the raw tags dict.
_ITEM_TAG_FIELDS: tuple[str, ...] = ("size", "fit")
# Wardrowbe's web UI has no size field, so users keep sizes and the like in
# the free-text notes; cap them so a long memo can't crowd out the item list.
_ITEM_NOTES_MAX = 200


class GetWardrobeItemsTool(BaseWardrowbeTool):
    name = "wardrowbe__get_wardrobe_items"
    title = "Get wardrobe items"
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    description = (
        "List the user's clean, non-archived wardrobe items with their ids and "
        "tags (type, colors, style, formality, season, size, fit, wear history) and "
        "the user's free-text notes, where they often keep sizes. Call this "
        "to compose an outfit yourself before wardrowbe__create_outfit_suggestion, "
        "or when the user asks what clothes they have. Optional filters, all "
        "combinable: type and subtype (exact values; the result's available_types "
        "lists the user's real types, so retry with one of those if nothing "
        "matched), colors (list, matches any; use these exact values: "
        f"{', '.join(ITEM_COLORS)}), favorite, season ({', '.join(ITEM_SEASONS)}; "
        "all-season items are included), formality (exact, one of "
        f"{', '.join(ITEM_FORMALITY)}), "
        "search (free text in name/brand), limit (1-100, default 60). Set "
        "auto_display true only when the user asked to see their clothes; leave "
        "it false when you are just picking items for an outfit."
    )
    parameters = vol.Schema(
        {
            vol.Optional("type"): str,
            vol.Optional("subtype"): str,
            vol.Optional("colors"): [str],
            vol.Optional("favorite"): bool,
            vol.Optional("season"): str,
            vol.Optional("formality"): str,
            vol.Optional("search"): str,
            vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=100)),
            vol.Optional("auto_display"): bool,
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
        limit = args.get("limit") or 60
        season = (args.get("season") or "").casefold()
        formality = (args.get("formality") or "").casefold()
        colors = {c.casefold() for c in args.get("colors") or []}
        local_filter = bool(season or formality or colors)
        try:
            items = await runtime.client.async_list_items(
                item_type=args.get("type"),
                subtype=args.get("subtype"),
                favorite=args.get("favorite"),
                search=args.get("search"),
                # Season/formality have no server-side filter, and the server's
                # color filter ignores primary_color: fetch the maximum page,
                # filter here, then apply the limit.
                limit=100 if local_filter else limit,
            )
            types = await runtime.client.async_item_types()
        except WardrowbeApiError as err:
            return self.error(f"get_wardrobe_items failed: {err}")
        items = [
            item
            for item in items
            if isinstance(item, dict)
            and (not colors or _matches_colors(item, colors))
            and (not season or _matches_season(item, season))
            and (not formality or str(item.get("formality") or "").casefold() == formality)
        ][:limit]
        available_types = [
            {"type": t.get("type"), "count": t.get("count")}
            for t in types
            if isinstance(t, dict) and t.get("type")
        ]
        compact = [_compact_item(item) for item in items]
        if not args.get("auto_display"):
            # Intermediate lookup for outfit composition: no gallery, so the
            # satellite card only shows the outfit that gets created from it.
            return self.envelope(
                count=len(compact), items=compact, available_types=available_types
            )
        results: list[dict[str, Any]] = []
        for item in items:
            if img := extract_image_url(item, self.host):
                title = item.get("name") or item.get("type") or "Item"
                results.append(
                    {
                        "image_url": img,
                        "thumbnail_url": img,
                        "title": str(title),
                        "item_id": str(item.get("id")),
                    }
                )
        return self.envelope(
            count=len(compact),
            items=compact,
            available_types=available_types,
            results=results,
            auto_display=True,
            instruction=(
                "The items are shown as an image gallery. Say how many there "
                "are in one short sentence; do not list them or their URLs."
            ),
        )


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    out = {key: item[key] for key in _ITEM_FIELDS if item.get(key) not in (None, [], "")}
    tags = item.get("tags")
    if isinstance(tags, dict):
        out.update({key: tags[key] for key in _ITEM_TAG_FIELDS if tags.get(key) not in (None, "")})
    notes = item.get("notes")
    if isinstance(notes, str) and notes.strip():
        notes = notes.strip()
        out["notes"] = notes if len(notes) <= _ITEM_NOTES_MAX else notes[:_ITEM_NOTES_MAX] + "…"
    return out


def _matches_colors(item: dict[str, Any], wanted: set[str]) -> bool:
    # Wardrowbe's colors list and primary_color can disagree (e.g. primary
    # "navy", colors ["blue"]), so match either.
    have = {str(c).casefold() for c in item.get("colors") or []}
    if item.get("primary_color"):
        have.add(str(item["primary_color"]).casefold())
    return not wanted.isdisjoint(have)


def _matches_season(item: dict[str, Any], season: str) -> bool:
    seasons = {str(s).casefold() for s in item.get("season") or []}
    return season in seasons or "all-season" in seasons


class GetItemsToWashTool(BaseWardrowbeTool):
    name = "wardrowbe__get_items_to_wash"
    title = "Get items to wash"
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    description = (
        "List wardrobe items the server has flagged as needing a wash "
        "(needs_wash=true), as an image gallery with item ids. Call this "
        "when the user asks what needs washing or laundry. Optional "
        "limit (1-20, default 8). Use the returned item_id with wardrowbe__log_wash "
        "once the user says they've washed something."
    )
    parameters = vol.Schema(
        {vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=20))}
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
        limit = tool_input.tool_args.get("limit") or 8
        items = runtime.coordinator.data.items_to_wash[:limit]

        results: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            img = extract_image_url(item, self.host)
            title = item.get("name") or item.get("type") or "Item"
            wears = item.get("wears_since_wash")
            full_title = f"{title} · {wears} wears" if wears is not None else str(title)
            entry: dict[str, Any] = {"title": full_title}
            if img:
                entry["image_url"] = img
                entry["thumbnail_url"] = img
            entry["item_id"] = str(item.get("id")) if item.get("id") is not None else None
            results.append(entry)

        return self.envelope(
            count=len(items),
            items=items,
            results=results,
            auto_display=True,
            instruction=(
                "Items needing wash are shown. Tell the user how many and "
                "name the top 1-2; offer to mark one washed if they ask."
            ),
        )


class LogWashTool(BaseWardrowbeTool):
    name = "wardrowbe__log_wash"
    title = "Log wash"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=True, idempotent=False, open_world=True
    )
    description = (
        "Mark a wardrobe item as washed and reset its wear-since-wash "
        "counter. Call this when the user says they washed, cleaned, or "
        "laundered an item. Pass item_id (preferred, e.g. from "
        "wardrowbe__get_items_to_wash) or item_name to match by name against the "
        "current items-to-wash list. Fails if neither is given or no "
        "match is found."
    )
    parameters = vol.Schema(
        {
            vol.Optional("item_id"): str,
            vol.Optional("item_name"): str,
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
        item_id = args.get("item_id")
        if not item_id:
            name = (args.get("item_name") or "").strip().lower()
            if not name:
                return self.error("Provide item_id or item_name.")
            for candidate in runtime.coordinator.data.items_to_wash:
                cand_name = str(candidate.get("name") or "").strip().lower()
                if cand_name and (cand_name == name or name in cand_name):
                    item_id = str(candidate.get("id")) if candidate.get("id") is not None else None
                    break
            if not item_id:
                return self.error(
                    f"No item matching '{args.get('item_name')}' in the wash list."
                )

        try:
            result = await runtime.client.async_log_wash(str(item_id), {})
        except WardrowbeApiError as err:
            return self.error(f"log_wash failed: {err}")
        payload: dict[str, Any] = {"item_id": str(item_id)}
        if isinstance(result, dict):
            for key in ("name", "wears_since_wash", "last_washed_at"):
                if key in result:
                    payload[key] = result[key]
        runtime.coordinator.record_local_event(EVENT_GROUP_WASH, "logged", payload)
        await runtime.coordinator.async_request_refresh()
        return self.envelope(
            item_id=str(item_id),
            instruction=(
                "The item was logged as washed. Confirm in one short "
                "sentence."
            ),
        )


class GetBodyMeasurementsTool(BaseWardrowbeTool):
    name = "wardrowbe__get_body_measurements"
    title = "Get body measurements"
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    description = (
        "Return the body measurements and clothing sizes the user saved in "
        "Wardrowbe. Numeric keys are always metric regardless of the user's "
        "display setting: height, chest, waist, hips and inseam in cm, weight "
        "in kg. Size keys (shirt_size, pants_size, dress_size, shoe_size) are "
        "free text as the user typed them. Keys the user never filled in are "
        "absent; an empty object means nothing was entered. Call this when the "
        "user asks about their own measurements or sizes, or whether an item "
        "would fit them (compare with the size and fit of items from "
        "wardrowbe__get_wardrobe_items)."
    )
    parameters = vol.Schema({})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        runtime = self.runtime
        if runtime is None:
            return self.error("Wardrowbe is not loaded.")
        try:
            profile = await runtime.client.async_user_profile()
        except WardrowbeApiError as err:
            return self.error(f"get_body_measurements failed: {err}")
        raw = profile.get("body_measurements") if isinstance(profile, dict) else None
        if not isinstance(raw, dict):
            raw = {}
        measurements = {key: value for key, value in raw.items() if value not in (None, "")}
        return self.envelope(body_measurements=measurements)


class LogWearTool(BaseWardrowbeTool):
    name = "wardrowbe__log_wear"
    title = "Log wear"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=True, idempotent=False, open_world=True
    )
    description = (
        "Record that the user wore a wardrobe item, which updates its wear "
        "count and wash tracking. Call this when the user says they wore or "
        "are wearing a specific item. Pass item_id (preferred, e.g. from "
        "wardrowbe__get_wardrobe_items) or item_name to search all items by "
        "name. Optional worn_at (YYYY-MM-DD, default today in the user's "
        f"timezone), occasion ({', '.join(VALID_OCCASIONS)}) and notes. For a "
        "whole outfit the user wore, use wardrowbe__rate_outfit with worn "
        "true instead."
    )
    parameters = vol.Schema(
        {
            vol.Optional("item_id"): str,
            vol.Optional("item_name"): str,
            vol.Optional("worn_at"): vol.Match(r"^\d{4}-\d{2}-\d{2}$"),
            vol.Optional("occasion"): vol.In(VALID_OCCASIONS),
            vol.Optional("notes"): str,
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
        item_id = args.get("item_id")
        try:
            if not item_id:
                name = (args.get("item_name") or "").strip()
                if not name:
                    return self.error("Provide item_id or item_name.")
                candidates = await runtime.client.async_list_items(
                    search=name, include_needs_wash=True, limit=20
                )
                match = _pick_by_name(candidates, name)
                if isinstance(match, str):
                    return self.error(match)
                item_id = str(match["id"])
            payload = {
                key: args[key]
                for key in ("worn_at", "occasion", "notes")
                if args.get(key) not in (None, "")
            }
            result = await runtime.client.async_log_wear(str(item_id), payload)
        except WardrowbeApiError as err:
            return self.error(f"log_wear failed: {err}")
        event: dict[str, Any] = {"item_id": str(item_id)}
        if isinstance(result, dict):
            for key in ("name", "wear_count", "last_worn_at", "wears_since_wash"):
                if key in result:
                    event[key] = result[key]
        runtime.coordinator.record_local_event(EVENT_GROUP_WEAR, "logged", event)
        await runtime.coordinator.async_request_refresh()
        return self.envelope(
            item_id=str(item_id),
            item_name=event.get("name"),
            instruction="The wear was logged. Confirm in one short sentence.",
        )


def _pick_by_name(candidates: list[dict[str, Any]], name: str) -> dict[str, Any] | str:
    """The single item the name means, or an error message for the model."""
    items = [c for c in candidates if isinstance(c, dict) and c.get("id") is not None]
    wanted = name.casefold()
    exact = [c for c in items if str(c.get("name") or "").strip().casefold() == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(items) == 1:
        return items[0]
    if not items:
        return f"No item matching '{name}'."
    # Guessing here would log a wear on the wrong garment.
    names = ", ".join(f"{c.get('name') or c.get('type')} ({c['id']})" for c in items[:5])
    return f"Several items match '{name}': {names}. Ask which one, then pass its item_id."


_PAIR_ITEM_FIELDS = ("id", "name", "type", "primary_color")
_INSIGHT_FIELDS = ("title", "description", "category", "confidence")


class GetStyleInsightsTool(BaseWardrowbeTool):
    name = "wardrowbe__get_style_insights"
    title = "Get style insights"
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    description = (
        "Return what Wardrowbe has learned from the user's outfit feedback: "
        "acceptance rate and average ratings, liked and disliked colors and "
        "styles (score -1 to 1), per-occasion and per-weather patterns, the "
        "item pairs that work best together, written style insights, and "
        "suggested colors to add to their favorites or avoid list. Call this "
        "when the user asks about their taste, style habits, or which of "
        "their clothes go well together. has_learning_data false means there "
        "is not enough rated feedback yet."
    )
    parameters = vol.Schema({})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        runtime = self.runtime
        if runtime is None:
            return self.error("Wardrowbe is not loaded.")
        try:
            data = await runtime.client.async_learning_insights()
        except WardrowbeApiError as err:
            return self.error(f"get_style_insights failed: {err}")
        if not isinstance(data, dict):
            data = {}
        # Thumbnail paths and translation keys only cost the model tokens.
        best_pairs = [
            {
                "item1": _pick(pair.get("item1"), _PAIR_ITEM_FIELDS),
                "item2": _pick(pair.get("item2"), _PAIR_ITEM_FIELDS),
                "compatibility_score": pair.get("compatibility_score"),
                "times_paired": pair.get("times_paired"),
                "times_accepted": pair.get("times_accepted"),
            }
            for pair in data.get("best_pairs") or []
            if isinstance(pair, dict)
        ]
        insights = [
            _pick(insight, _INSIGHT_FIELDS)
            for insight in data.get("insights") or []
            if isinstance(insight, dict)
        ]
        return self.envelope(
            profile=data.get("profile") or {},
            best_pairs=best_pairs,
            insights=insights,
            preference_suggestions=data.get("preference_suggestions") or {},
        )


def _pick(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: source[key] for key in keys if source.get(key) is not None}


class GetItemPairingsTool(BaseWardrowbeTool):
    name = "wardrowbe__get_item_pairings"
    title = "Get item pairings"
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    description = (
        "List the items that have gone well with one wardrobe item in outfits "
        "the user rated, best first, with a compatibility score. Call this "
        "when the user asks what goes with a specific item, or before "
        "wardrowbe__create_outfit_suggestion to build around one item. Needs "
        "item_id (from wardrowbe__get_wardrobe_items); optional limit (1-20, "
        "default 5). An empty list means there is no rated history for that "
        "item yet, so pick partners from wardrowbe__get_wardrobe_items instead."
    )
    parameters = vol.Schema(
        {
            vol.Required("item_id"): str,
            vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=20)),
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
        item_id = str(args["item_id"])
        try:
            pairs = await runtime.client.async_item_pair_suggestions(
                item_id, limit=args.get("limit") or 5
            )
        except WardrowbeApiError as err:
            return self.error(f"get_item_pairings failed: {err}")
        pairings = []
        for pair in pairs:
            item = pair.get("item") if isinstance(pair, dict) else None
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            compact = {
                key: item[key]
                for key in ("id", "name", "type", "primary_color")
                if item.get(key) is not None
            }
            compact["compatibility_score"] = pair.get("compatibility_score")
            pairings.append(compact)
        return self.envelope(item_id=item_id, count=len(pairings), pairings=pairings)



class AddWardrobeItemTool(BaseWardrowbeTool):
    name = "wardrowbe__add_wardrobe_item"
    title = "Add wardrobe item"
    annotations = llm.ToolAnnotations(
        read_only=False, destructive=False, idempotent=False, open_world=True
    )
    description = (
        "Add a clothing item to the user's wardrobe from a photo. Call this "
        "only when the user gives you the photo: pass exactly one of "
        "image_url (an http or https link to it) or image_path (a file on the "
        "Home Assistant host, in a folder allowed by allowlist_external_dirs). "
        "Never make up a URL or path. The photo must be JPEG, PNG, WebP or "
        "HEIC, up to 20 MB. Optional type (e.g. shirt, jeans), name, brand, "
        f"primary_color ({', '.join(ITEM_COLORS)}) and notes: pass only what "
        "the user said, since Wardrowbe's AI tags the photo and fills in the "
        "rest. skip_ai true adds it untagged for the user to edit later."
    )
    parameters = vol.Schema(
        {
            vol.Optional(ATTR_IMAGE_URL): str,
            vol.Optional(ATTR_IMAGE_PATH): str,
            vol.Optional("type"): str,
            vol.Optional("name"): str,
            vol.Optional("brand"): str,
            vol.Optional("primary_color"): vol.In(ITEM_COLORS),
            vol.Optional("notes"): str,
            vol.Optional("skip_ai"): bool,
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
        url, path = args.get(ATTR_IMAGE_URL), args.get(ATTR_IMAGE_PATH)
        if bool(url) == bool(path):
            return self.error("Pass exactly one of image_url or image_path.")
        if url:
            try:
                cv.url(url)
            except vol.Invalid:
                return self.error(f"{url} is not an http or https URL.")
        try:
            item = await async_add_item(hass, runtime.client, args)
        except HomeAssistantError as err:
            return self.error(str(err))
        await runtime.coordinator.async_request_refresh()
        return self.envelope(
            item={key: item.get(key) for key in ("id", "name", "type", "status")},
            instruction=(
                "The item was added. Confirm in one short sentence; if its "
                "status is processing, add that Wardrowbe's AI is still "
                "tagging it."
            ),
        )

