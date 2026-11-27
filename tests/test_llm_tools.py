"""Tests for Wardrowbe's opt-in, lazily-loaded LLM API.

The lazy-loading test must run before any other test in this module
imports `custom_components.wardrowbe.llm` (directly or via a submodule),
since Python caches package imports for the life of the process. Every
other test that needs the platform hook or a tool class imports it
locally, inside the test function, to keep that ordering irrelevant in
practice — but the platform-import test itself stays first for clarity.
"""

from __future__ import annotations

import importlib.util
import sys
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm as ha_llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wardrowbe.api import WardrowbeApiError
from custom_components.wardrowbe.const import DOMAIN

_PLATFORM_MODULE = "custom_components.wardrowbe.llm"

# The isolated phacc test stack may pin an older HA release. Only the
# HA 2026.10+ runtime can exercise the ToolResult contract.
requires_llm_integration = pytest.mark.skipif(
    importlib.util.find_spec("homeassistant.components.llm") is None
    or not hasattr(ha_llm, "ToolResult"),
    reason="needs Home Assistant 2026.10 ToolResult",
)


def _api_id(entry_id: str) -> str:
    return f"{DOMAIN}__{entry_id}"


def _llm_context() -> ha_llm.LLMContext:
    return ha_llm.LLMContext(
        platform=DOMAIN,
        context=Context(),
        language="en",
        assistant="conversation",
        device_id=None,
    )


async def test_setup_does_not_import_llm_platform_eagerly(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Setting up an entry must not import the llm/ tool platform package."""
    was_imported = _PLATFORM_MODULE in sys.modules

    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()

    assert (_PLATFORM_MODULE in sys.modules) == was_imported


async def test_setup_registers_per_entry_api_unload_unregisters(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()

    apis = {api.id: api for api in ha_llm.async_get_apis(hass)}
    api_id = _api_id(dev_mode_entry.entry_id)
    assert api_id in apis
    assert apis[api_id].name == f"Wardrowbe — {dev_mode_entry.title}"

    assert await hass.config_entries.async_unload(dev_mode_entry.entry_id)
    await hass.async_block_till_done()

    apis_after = {api.id for api in ha_llm.async_get_apis(hass)}
    assert api_id not in apis_after


@requires_llm_integration
async def test_hook_opts_out_of_assist_and_unknown_ids(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm import async_get_tools

    llm_context = _llm_context()
    assert async_get_tools(hass, llm_context, "assist") is None
    assert async_get_tools(hass, llm_context, "some_other_domain__xyz") is None
    assert async_get_tools(hass, llm_context, f"{DOMAIN}__unknown-entry") is None


@requires_llm_integration
async def test_hook_returns_tool_set_for_loaded_entry_none_after_unload(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    from custom_components.wardrowbe.llm import async_get_tools

    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()

    llm_context = _llm_context()
    api_id = _api_id(dev_mode_entry.entry_id)
    result = async_get_tools(hass, llm_context, api_id)
    assert result is not None
    assert {tool.name for tool in result.tools} == {
        "wardrowbe__suggest_outfit",
        "wardrowbe__create_outfit_suggestion",
        "wardrowbe__get_latest_outfit",
        "wardrowbe__get_recent_outfits",
        "wardrowbe__accept_latest_outfit",
        "wardrowbe__reject_latest_outfit",
        "wardrowbe__skip_latest_outfit",
        "wardrowbe__get_wardrobe_summary",
        "wardrowbe__get_wardrobe_items",
        "wardrowbe__get_most_worn_items",
        "wardrowbe__get_items_to_wash",
        "wardrowbe__log_wash",
        "wardrowbe__get_body_measurements",
        "wardrowbe__log_wear",
        "wardrowbe__rate_outfit",
        "wardrowbe__get_style_insights",
        "wardrowbe__get_item_pairings",
    }

    assert await hass.config_entries.async_unload(dev_mode_entry.entry_id)
    await hass.async_block_till_done()

    assert async_get_tools(hass, llm_context, api_id) is None


@requires_llm_integration
async def test_api_instance_end_to_end(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    assert await async_setup_component(hass, "llm", {})

    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()

    instance = await ha_llm.async_get_api(
        hass, _api_id(dev_mode_entry.entry_id), _llm_context()
    )

    # 12 wardrowbe tools + HA core's own built-in GetDateTimeTool, which
    # async_get_api injects into every API instance.
    assert len(instance.tools) == 18
    assert "wardrobe" in instance.api_prompt.lower()


@requires_llm_integration
async def test_suggest_outfit_tool_happy_path_and_error(
    hass: HomeAssistant,
    dev_mode_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    from custom_components.wardrowbe.llm.outfit_tools import (
        SuggestOutfitTool,
    )

    dev_mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(dev_mode_entry.entry_id)
    await hass.async_block_till_done()

    tool = SuggestOutfitTool(hass, dev_mode_entry.entry_id)
    llm_context = _llm_context()

    mock_client.async_suggest_outfit = AsyncMock(
        return_value={"id": "outfit-1", "occasion": "work"}
    )
    result = await tool.async_call(
        hass, ha_llm.ToolInput(tool_name=tool.name, tool_args={}), llm_context
    )
    assert isinstance(result, ha_llm.ToolResult)
    assert not result.error
    assert result.data["source"] == "wardrowbe"
    assert "error" not in result.data
    assert result.data["outfit"]["id"] == "outfit-1"

    mock_client.async_suggest_outfit = AsyncMock(side_effect=WardrowbeApiError("boom"))
    error_result = await tool.async_call(
        hass, ha_llm.ToolInput(tool_name=tool.name, tool_args={}), llm_context
    )
    assert error_result.error
    assert "boom" in error_result.data["error"]


@requires_llm_integration
async def test_tool_metadata_distinguishes_reads_and_writes(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.tools import TOOL_FACTORIES

    for factory in TOOL_FACTORIES:
        tool = factory(hass, "test-entry")
        assert tool.integration == DOMAIN
        assert tool.title
        assert tool.name.startswith(f"{DOMAIN}__")
        is_read = tool.name.startswith(f"{DOMAIN}__get_")
        assert tool.annotations.read_only == is_read
        assert tool.annotations.open_world == (not is_read)
        if not is_read:
            assert not tool.annotations.idempotent


@requires_llm_integration
async def test_no_outfit_is_successful_empty_result(hass: HomeAssistant) -> None:
    from types import SimpleNamespace
    from unittest.mock import PropertyMock, patch

    from custom_components.wardrowbe.llm.outfit_tools import GetLatestOutfitTool

    tool = GetLatestOutfitTool(hass, "test-entry")
    runtime = SimpleNamespace(coordinator=SimpleNamespace(data=SimpleNamespace(outfits=[])))
    with patch.object(GetLatestOutfitTool, "runtime", new_callable=PropertyMock) as prop:
        prop.return_value = runtime
        result = await tool.async_call(
            hass, ha_llm.ToolInput(tool_name=tool.name, tool_args={}), _llm_context()
        )
    assert not result.error
    assert result.data["outfit"] is None
    assert result.data["results"] == []


@requires_llm_integration
async def test_outfit_action_tool_acts_on_sent_outfit(hass: HomeAssistant) -> None:
    """A ``sent`` outfit (notified but not answered) is still actionable."""
    from types import SimpleNamespace
    from unittest.mock import PropertyMock, patch

    from custom_components.wardrowbe.llm.outfit_tools import AcceptLatestOutfitTool

    tool = AcceptLatestOutfitTool(hass, "test-entry")
    client = SimpleNamespace(async_outfit_action=AsyncMock())
    coordinator = SimpleNamespace(
        data=SimpleNamespace(outfits=[{"id": "o1", "status": "sent"}]),
        async_request_refresh=AsyncMock(),
    )
    runtime = SimpleNamespace(client=client, coordinator=coordinator)
    with patch.object(AcceptLatestOutfitTool, "runtime", new_callable=PropertyMock) as prop:
        prop.return_value = runtime
        result = await tool.async_call(
            hass, ha_llm.ToolInput(tool_name=tool.name, tool_args={}), _llm_context()
        )
    assert not result.error
    client.async_outfit_action.assert_awaited_once_with("o1", "accept")


def _tool_runtime(**client_methods: AsyncMock) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        client=SimpleNamespace(**client_methods),
        coordinator=SimpleNamespace(data=None, async_request_refresh=AsyncMock()),
    )


async def _call_tool(hass: HomeAssistant, cls: type, runtime: object, args: dict) -> object:
    from unittest.mock import PropertyMock, patch

    tool = cls(hass, "test-entry")
    with patch.object(cls, "runtime", new_callable=PropertyMock) as prop:
        prop.return_value = runtime
        return await tool.async_call(
            hass, ha_llm.ToolInput(tool_name=tool.name, tool_args=args), _llm_context()
        )


@requires_llm_integration
async def test_suggest_tool_503_points_to_external_authoring(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.outfit_tools import SuggestOutfitTool

    runtime = _tool_runtime(
        async_suggest_outfit=AsyncMock(side_effect=WardrowbeApiError("off", status=503))
    )
    result = await _call_tool(hass, SuggestOutfitTool, runtime, {})
    assert result.error
    assert "wardrowbe__create_outfit_suggestion" in result.data["error"]


@requires_llm_integration
async def test_get_wardrobe_items_tool_compacts_items(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.wardrobe_tools import GetWardrobeItemsTool

    list_items = AsyncMock(
        return_value=[
            {
                "id": "i1",
                "type": "shirt",
                "colors": ["navy"],
                "style": [],
                "tags": {"size": "M", "fit": "slim", "care_instructions": "cold"},
                "image_path": "/files/i1.jpg",
                "ai_raw_response": {"big": "blob"},
            }
        ]
    )
    runtime = _tool_runtime(
        async_list_items=list_items,
        async_item_types=AsyncMock(return_value=[{"type": "shirt", "count": 3}]),
    )
    result = await _call_tool(hass, GetWardrobeItemsTool, runtime, {"type": "shirt"})
    assert not result.error
    assert result.data["items"] == [
        {"id": "i1", "type": "shirt", "colors": ["navy"], "size": "M", "fit": "slim"}
    ]
    assert result.data["available_types"] == [{"type": "shirt", "count": 3}]
    list_items.assert_awaited_once_with(
        item_type="shirt", subtype=None, favorite=None, search=None, limit=60
    )


@requires_llm_integration
async def test_create_outfit_suggestion_tool(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.outfit_tools import CreateOutfitSuggestionTool

    create = AsyncMock(return_value={"id": "o1", "status": "pending", "items": []})
    runtime = _tool_runtime(async_create_outfit_suggestion=create)
    result = await _call_tool(
        hass,
        CreateOutfitSuggestionTool,
        runtime,
        {"item_ids": ["i1", "i2"], "occasion": "casual", "reasoning": "Easy weekend look"},
    )
    assert not result.error
    assert result.data["outfit"]["id"] == "o1"
    create.assert_awaited_once_with(
        {"items": ["i1", "i2"], "occasion": "casual", "reasoning": "Easy weekend look"}
    )
    runtime.coordinator.async_request_refresh.assert_awaited_once()


@requires_llm_integration
async def test_get_wardrobe_items_tool_gallery_on_request(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.wardrobe_tools import GetWardrobeItemsTool

    list_items = AsyncMock(
        return_value=[
            {"id": "i1", "name": "Navy shirt", "image_url": "/api/wardrowbe/image/e/files/i1.jpg"},
            {"id": "i2", "type": "pants"},
        ]
    )
    runtime = _tool_runtime(
        async_list_items=list_items, async_item_types=AsyncMock(return_value=[])
    )
    plain = await _call_tool(hass, GetWardrobeItemsTool, runtime, {})
    assert "results" not in plain.data

    shown = await _call_tool(hass, GetWardrobeItemsTool, runtime, {"auto_display": True})
    assert shown.data["auto_display"] is True
    assert [r["item_id"] for r in shown.data["results"]] == ["i1"]
    assert shown.data["results"][0]["image_url"].endswith("/files/i1.jpg")


@requires_llm_integration
async def test_get_wardrobe_items_tool_filters(hass: HomeAssistant) -> None:
    """Server filters pass through; season/formality filter locally before the limit."""
    from custom_components.wardrowbe.llm.wardrobe_tools import GetWardrobeItemsTool

    list_items = AsyncMock(
        return_value=[
            {"id": "coat", "season": ["winter"], "formality": "formal", "colors": ["navy"]},
            {"id": "tee", "season": ["summer"], "formality": "casual", "colors": ["navy"]},
            # primary_color and colors disagree, as seen on real Wardrowbe data
            {
                "id": "jeans",
                "season": ["all-season"],
                "formality": "Casual",
                "primary_color": "Navy",
                "colors": ["blue"],
            },
            {
                "id": "parka",
                "season": ["Winter", "fall"],
                "formality": "casual",
                "colors": ["black"],
            },
            {"id": "boots", "season": ["winter"], "formality": "casual", "colors": ["brown"]},
        ]
    )
    runtime = _tool_runtime(
        async_list_items=list_items, async_item_types=AsyncMock(return_value=[])
    )
    result = await _call_tool(
        hass,
        GetWardrobeItemsTool,
        runtime,
        {
            "colors": ["navy", "black"],
            "favorite": True,
            "subtype": "parka",
            "season": "winter",
            "formality": "casual",
            "limit": 5,
        },
    )
    assert [i["id"] for i in result.data["items"]] == ["jeans", "parka"]
    list_items.assert_awaited_once_with(
        item_type=None,
        subtype="parka",
        favorite=True,
        search=None,
        limit=100,
    )


@requires_llm_integration
async def test_get_body_measurements_tool(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.wardrobe_tools import GetBodyMeasurementsTool

    runtime = _tool_runtime(
        async_user_profile=AsyncMock(
            return_value={
                "id": "u1",
                "email": "someone@example.com",
                "body_measurements": {"height": 178, "waist": None, "shoe_size": "270"},
            }
        )
    )
    result = await _call_tool(hass, GetBodyMeasurementsTool, runtime, {})
    assert not result.error
    assert result.data["body_measurements"] == {"height": 178, "shoe_size": "270"}
    assert "email" not in result.data

    runtime = _tool_runtime(async_user_profile=AsyncMock(return_value={"body_measurements": None}))
    result = await _call_tool(hass, GetBodyMeasurementsTool, runtime, {})
    assert not result.error
    assert result.data["body_measurements"] == {}

    runtime = _tool_runtime(async_user_profile=AsyncMock(side_effect=WardrowbeApiError("boom")))
    result = await _call_tool(hass, GetBodyMeasurementsTool, runtime, {})
    assert result.error
    assert "boom" in result.data["error"]


def _write_runtime(outfits: list[dict] | None = None, **client_methods: AsyncMock) -> object:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    return SimpleNamespace(
        client=SimpleNamespace(**client_methods),
        coordinator=SimpleNamespace(
            data=SimpleNamespace(outfits=outfits or []),
            async_request_refresh=AsyncMock(),
            record_local_event=MagicMock(),
        ),
    )


@requires_llm_integration
async def test_log_wear_tool_by_id_sends_server_field_names(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.wardrobe_tools import LogWearTool

    log_wear = AsyncMock(return_value={"name": "Navy shirt", "wear_count": 4})
    runtime = _write_runtime(async_log_wear=log_wear)
    result = await _call_tool(
        hass, LogWearTool, runtime, {"item_id": "i1", "worn_at": "2026-11-26", "occasion": "office"}
    )
    assert not result.error
    log_wear.assert_awaited_once_with("i1", {"worn_at": "2026-11-26", "occasion": "office"})
    runtime.coordinator.record_local_event.assert_called_once_with(
        "wear", "logged", {"item_id": "i1", "name": "Navy shirt", "wear_count": 4}
    )


@requires_llm_integration
async def test_log_wear_tool_resolves_item_name(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.wardrobe_tools import LogWearTool

    log_wear = AsyncMock(return_value={})
    list_items = AsyncMock(
        return_value=[
            {"id": "i1", "name": "Navy shirt"},
            {"id": "i2", "name": "Navy shirt dress"},
        ]
    )
    runtime = _write_runtime(async_log_wear=log_wear, async_list_items=list_items)
    result = await _call_tool(hass, LogWearTool, runtime, {"item_name": "navy shirt"})
    assert not result.error
    log_wear.assert_awaited_once_with("i1", {})
    list_items.assert_awaited_once_with(search="navy shirt", include_needs_wash=True, limit=20)


@requires_llm_integration
async def test_log_wear_tool_refuses_to_guess_between_items(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.wardrobe_tools import LogWearTool

    log_wear = AsyncMock()
    list_items = AsyncMock(
        return_value=[{"id": "i1", "name": "Navy shirt"}, {"id": "i2", "name": "Navy tee"}]
    )
    runtime = _write_runtime(async_log_wear=log_wear, async_list_items=list_items)
    result = await _call_tool(hass, LogWearTool, runtime, {"item_name": "navy"})
    assert result.error
    assert "i1" in result.data["error"] and "i2" in result.data["error"]
    log_wear.assert_not_awaited()

    list_items.return_value = []
    result = await _call_tool(hass, LogWearTool, runtime, {"item_name": "navy"})
    assert result.error
    log_wear.assert_not_awaited()


@requires_llm_integration
async def test_rate_outfit_tool_targets_latest_or_given_outfit(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.outfit_tools import RateOutfitTool

    feedback = AsyncMock(return_value={})
    runtime = _write_runtime(
        outfits=[{"id": "o2", "status": "accepted"}, {"id": "o1"}],
        async_post_outfit_feedback=feedback,
    )
    result = await _call_tool(
        hass, RateOutfitTool, runtime, {"rating": 2, "comment": "too warm", "worn": True}
    )
    assert not result.error
    feedback.assert_awaited_once_with("o2", {"rating": 2, "comment": "too warm", "worn": True})
    runtime.coordinator.record_local_event.assert_called_once_with(
        "outfit",
        "feedback_submitted",
        {"outfit_id": "o2", "rating": 2, "comment": "too warm", "worn": True},
    )

    feedback.reset_mock()
    result = await _call_tool(hass, RateOutfitTool, runtime, {"outfit_id": "o1", "style_rating": 5})
    assert not result.error
    feedback.assert_awaited_once_with("o1", {"style_rating": 5})


@requires_llm_integration
async def test_rate_outfit_tool_needs_feedback_and_an_outfit(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.outfit_tools import RateOutfitTool

    feedback = AsyncMock()
    runtime = _write_runtime(outfits=[{"id": "o1"}], async_post_outfit_feedback=feedback)
    result = await _call_tool(hass, RateOutfitTool, runtime, {})
    assert result.error

    runtime = _write_runtime(async_post_outfit_feedback=feedback)
    result = await _call_tool(hass, RateOutfitTool, runtime, {"rating": 4})
    assert result.error
    feedback.assert_not_awaited()


@requires_llm_integration
async def test_suggest_outfit_tool_takes_only_supported_hints(hass: HomeAssistant) -> None:
    import voluptuous as vol

    from custom_components.wardrowbe.llm.outfit_tools import SuggestOutfitTool

    for field in ("target_date", "notes"):
        with pytest.raises(vol.Invalid):
            SuggestOutfitTool.parameters({field: "x"})

    suggest = AsyncMock(return_value={"id": "o1"})
    runtime = _tool_runtime(async_suggest_outfit=suggest)
    result = await _call_tool(
        hass, SuggestOutfitTool, runtime, {"occasion": "office", "time_of_day": "evening"}
    )
    assert not result.error
    suggest.assert_awaited_once_with({"occasion": "office", "time_of_day": "evening"})


@requires_llm_integration
async def test_get_style_insights_tool_trims_payload(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.wardrobe_tools import GetStyleInsightsTool

    item = {"id": "i1", "name": "Navy shirt", "type": "shirt", "thumbnail_path": "/t.jpg"}
    profile = {"has_learning_data": True, "feedback_count": 12, "average_rating": 4.1}
    runtime = _tool_runtime(
        async_learning_insights=AsyncMock(
            return_value={
                "profile": profile,
                "best_pairs": [
                    {
                        "item1": item,
                        "item2": {"id": "i2", "name": "Chinos", "thumbnail_url": "x"},
                        "compatibility_score": 0.8,
                        "times_paired": 5,
                        "times_accepted": 4,
                    }
                ],
                "insights": [
                    {
                        "id": "n1",
                        "title": "Blue works",
                        "description": "You accept blue outfits more often.",
                        "category": "color",
                        "confidence": 0.7,
                        "message_key": "insight.color",
                        "message_params": {},
                    }
                ],
                "preference_suggestions": {"updated": False},
            }
        )
    )
    result = await _call_tool(hass, GetStyleInsightsTool, runtime, {})
    assert not result.error
    assert result.data["profile"] == profile
    assert result.data["best_pairs"] == [
        {
            "item1": {"id": "i1", "name": "Navy shirt", "type": "shirt"},
            "item2": {"id": "i2", "name": "Chinos"},
            "compatibility_score": 0.8,
            "times_paired": 5,
            "times_accepted": 4,
        }
    ]
    assert result.data["insights"] == [
        {
            "title": "Blue works",
            "description": "You accept blue outfits more often.",
            "category": "color",
            "confidence": 0.7,
        }
    ]
    assert result.data["preference_suggestions"] == {"updated": False}

    runtime = _tool_runtime(
        async_learning_insights=AsyncMock(side_effect=WardrowbeApiError("boom"))
    )
    result = await _call_tool(hass, GetStyleInsightsTool, runtime, {})
    assert result.error


@requires_llm_integration
async def test_get_item_pairings_tool(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.wardrobe_tools import GetItemPairingsTool

    pair_suggestions = AsyncMock(
        return_value=[
            {
                "item": {"id": "i2", "name": "Chinos", "type": "pants", "thumbnail_path": "/t"},
                "compatibility_score": 0.9,
            },
            {"item": None, "compatibility_score": 0.1},
            "not-a-dict",
        ]
    )
    runtime = _tool_runtime(async_item_pair_suggestions=pair_suggestions)
    result = await _call_tool(hass, GetItemPairingsTool, runtime, {"item_id": "i1", "limit": 3})
    assert not result.error
    assert result.data["pairings"] == [
        {"id": "i2", "name": "Chinos", "type": "pants", "compatibility_score": 0.9}
    ]
    pair_suggestions.assert_awaited_once_with("i1", limit=3)

    runtime = _tool_runtime(
        async_item_pair_suggestions=AsyncMock(side_effect=WardrowbeApiError("boom"))
    )
    result = await _call_tool(hass, GetItemPairingsTool, runtime, {"item_id": "i1"})
    assert result.error


@requires_llm_integration
async def test_suggest_outfit_tool_weather_override(hass: HomeAssistant) -> None:
    from custom_components.wardrowbe.llm.outfit_tools import SuggestOutfitTool

    suggest = AsyncMock(return_value={"id": "o1"})
    runtime = _tool_runtime(async_suggest_outfit=suggest)
    result = await _call_tool(
        hass, SuggestOutfitTool, runtime, {"temperature": 9, "weather_condition": "rainy"}
    )
    assert not result.error
    suggest.assert_awaited_once_with(
        {"weather_override": {"temperature": 9.0, "condition": "rainy", "precipitation_chance": 80}}
    )

    suggest.reset_mock()
    result = await _call_tool(hass, SuggestOutfitTool, runtime, {"weather_condition": "rainy"})
    assert result.error
    suggest.assert_not_awaited()
