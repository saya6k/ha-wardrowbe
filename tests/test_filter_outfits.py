"""Unit tests for the get_summary status filter helper.

The helper is pure data — no HA dependencies — so we exercise it in isolation
without the heavy ``pytest_homeassistant_custom_component`` plumbing.
"""

from __future__ import annotations

import pytest

from custom_components.wardrowbe.services import _filter_outfits


@pytest.fixture
def outfits() -> list[dict]:
    return [
        {"id": "o1", "status": "rejected"},
        {"id": "o2", "status": "pending"},
        {"id": "o3", "status": "accepted"},
        {"id": "o4", "status": "rejected"},
        {"id": "o5", "status": "skipped"},
    ]


def _ids(outfits: list[dict]) -> list[str]:
    return [o["id"] for o in outfits]


def test_no_filter_returns_input_untouched(outfits):
    assert _filter_outfits(outfits, include=None, exclude=None) is outfits


def test_exclude_rejected_only(outfits):
    assert _ids(_filter_outfits(outfits, include=None, exclude=["rejected"])) == [
        "o2",
        "o3",
        "o5",
    ]


def test_exclude_rejected_and_skipped(outfits):
    assert _ids(
        _filter_outfits(outfits, include=None, exclude=["rejected", "skipped"])
    ) == ["o2", "o3"]


def test_include_pending_only(outfits):
    assert _ids(_filter_outfits(outfits, include=["pending"], exclude=None)) == ["o2"]


def test_include_and_exclude_intersect(outfits):
    assert _ids(
        _filter_outfits(
            outfits,
            include=["pending", "accepted"],
            exclude=["accepted"],
        )
    ) == ["o2"]


def test_handles_missing_or_empty_status():
    rough = [
        {"id": "a", "status": "pending"},
        {"id": "b", "status": None},
        {"id": "c"},
        {"id": "d", "status": ""},
    ]
    # 'b', 'c', 'd' all coerce to "" — not in exclude set so they pass through.
    assert _ids(_filter_outfits(rough, include=None, exclude=["rejected"])) == [
        "a",
        "b",
        "c",
        "d",
    ]
    # When include is given, empty-status items are dropped.
    assert _ids(_filter_outfits(rough, include=["pending"], exclude=None)) == ["a"]


def test_user_glance_scenario_rejected_at_top():
    """The Glance widget case: most-recent outfit is rejected.

    Without the filter, ``outfits[0]`` would be rejected and the dashboard
    keeps showing it. With ``exclude=['rejected']`` the next non-rejected
    outfit floats to index 0.
    """
    outfits = [
        {"id": "fresh-reject", "status": "rejected"},
        {"id": "older-pending", "status": "pending"},
    ]
    filtered = _filter_outfits(outfits, include=None, exclude=["rejected"])
    assert filtered[0]["id"] == "older-pending"
