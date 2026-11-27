"""The wash todo list shows each item's care instructions."""

from __future__ import annotations

from custom_components.wardrowbe.todo import _item_description


def test_description_includes_care_instructions() -> None:
    raw = {
        "wears_since_wash": 4,
        "wash_interval": 4,
        "tags": {"care_instructions": "Machine wash cold, no tumble dryer"},
    }
    assert _item_description(raw) == (
        "4 wears since last wash · interval 4\nMachine wash cold, no tumble dryer"
    )


def test_description_with_only_care_instructions() -> None:
    assert _item_description({"tags": {"care_instructions": "Hand wash"}}) == "Hand wash"


def test_description_without_care_instructions() -> None:
    assert _item_description({"wears_since_wash": 2, "tags": {"fit": "regular"}}) == (
        "2 wears since last wash"
    )
    assert _item_description({}) is None
    assert _item_description({"tags": {"care_instructions": "  "}}) is None

