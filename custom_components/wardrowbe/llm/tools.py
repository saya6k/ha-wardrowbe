"""Tool registry for the Wardrowbe LLM API."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .outfit_tools import (
    AcceptLatestOutfitTool,
    CreateOutfitSuggestionTool,
    GetLatestOutfitTool,
    GetRecentOutfitsTool,
    RateOutfitTool,
    RejectLatestOutfitTool,
    SkipLatestOutfitTool,
    SuggestOutfitTool,
)
from .wardrobe_tools import (
    GetBodyMeasurementsTool,
    GetItemsToWashTool,
    GetMostWornItemsTool,
    GetStyleInsightsTool,
    GetWardrobeItemsTool,
    GetWardrobeSummaryTool,
    LogWashTool,
    LogWearTool,
)

ToolFactory = Callable[[HomeAssistant, str], llm.Tool]


def _factory(cls: type) -> ToolFactory:
    def make(hass: HomeAssistant, entry_id: str) -> llm.Tool:
        return cls(hass, entry_id)

    return make


TOOL_FACTORIES: list[ToolFactory] = [
    _factory(SuggestOutfitTool),
    _factory(CreateOutfitSuggestionTool),
    _factory(GetLatestOutfitTool),
    _factory(GetRecentOutfitsTool),
    _factory(AcceptLatestOutfitTool),
    _factory(RejectLatestOutfitTool),
    _factory(SkipLatestOutfitTool),
    _factory(GetWardrobeSummaryTool),
    _factory(GetWardrobeItemsTool),
    _factory(GetMostWornItemsTool),
    _factory(GetItemsToWashTool),
    _factory(LogWashTool),
    _factory(GetBodyMeasurementsTool),
    _factory(LogWearTool),
    _factory(RateOutfitTool),
    _factory(GetStyleInsightsTool),
]
