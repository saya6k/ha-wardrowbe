"""Wardrowbe todo list — items that need to be washed.

The list is read-only except for completion: when a user marks an entry
done, the integration POSTs ``/api/v1/items/{id}/wash`` (which resets the
wear counter and clears ``needs_wash`` server-side), then refreshes.
Add/delete/move are not supported because membership is derived from
Wardrowbe's own state.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WardrowbeConfigEntry
from .api import WardrowbeApiError
from .const import EVENT_GROUP_WASH
from .coordinator import WardrowbeCoordinator
from .entity import WardrowbeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WardrowbeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities([WardrowbeWashTodo(coordinator)])


class WardrowbeWashTodo(WardrowbeEntity, TodoListEntity):
    """A todo list of items that need to be washed."""

    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM
    _attr_translation_key = "to_wash"
    _attr_icon = "mdi:washing-machine"

    def __init__(self, coordinator: WardrowbeCoordinator) -> None:
        super().__init__(coordinator, "to_wash")

    @property
    def todo_items(self) -> list[TodoItem] | None:
        data = self.coordinator.data
        if data is None:
            return None
        items: list[TodoItem] = []
        for raw in data.items_to_wash:
            uid = _item_uid(raw)
            if uid is None:
                continue
            items.append(
                TodoItem(
                    uid=uid,
                    summary=_item_summary(raw),
                    status=TodoItemStatus.NEEDS_ACTION,
                    description=_item_description(raw),
                )
            )
        return items

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.status != TodoItemStatus.COMPLETED or not item.uid:
            return
        runtime = self.coordinator.config_entry.runtime_data
        try:
            result = await runtime.client.async_log_wash(item.uid, {})
        except WardrowbeApiError as err:
            raise HomeAssistantError(f"log_wash failed: {err}") from err
        payload: dict[str, Any] = {"item_id": item.uid}
        if isinstance(result, dict):
            for key in ("wears_since_wash", "last_washed_at", "name"):
                if key in result:
                    payload[key] = result[key]
        self.coordinator.record_local_event(EVENT_GROUP_WASH, "logged", payload)
        await self.coordinator.async_request_refresh()


def _item_uid(raw: dict[str, Any]) -> str | None:
    rid = raw.get("id")
    return None if rid is None else str(rid)


def _item_summary(raw: dict[str, Any]) -> str:
    name = raw.get("name") or raw.get("title")
    if name:
        return str(name)
    parts = [str(raw.get(k)) for k in ("type", "subtype") if raw.get(k)]
    return " ".join(parts) or "Item"


def _item_description(raw: dict[str, Any]) -> str | None:
    bits: list[str] = []
    wears = raw.get("wears_since_wash")
    if isinstance(wears, int):
        bits.append(f"{wears} wears since last wash")
    interval = raw.get("effective_wash_interval") or raw.get("wash_interval")
    if interval:
        bits.append(f"interval {interval}")
    last = raw.get("last_washed_at")
    if last:
        bits.append(f"last washed {last}")
    return " · ".join(bits) or None
