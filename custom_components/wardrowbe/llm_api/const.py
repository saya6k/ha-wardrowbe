"""Constants for the Wardrowbe LLM API."""

from __future__ import annotations

SOURCE = "wardrowbe"

API_NAME = "Wardrowbe"
API_PROMPT = (
    "You can use Wardrowbe tools to manage outfits and wardrobe items. "
    "Call 'suggest_outfit' when the user asks 'what should I wear' "
    "(occasion / time_of_day / notes are optional hints). Call "
    "'get_latest_outfit' to show the most recent suggestion. Call "
    "'get_recent_outfits' to list outfits as a gallery. Call "
    "'accept_latest_outfit', 'reject_latest_outfit', or "
    "'skip_latest_outfit' to act on a pending suggestion. Call "
    "'get_wardrobe_summary' for stats; 'get_most_worn_items' for the "
    "user's most-worn pieces. Call 'get_items_to_wash' to show what "
    "needs washing; call 'log_wash' (with item_id from that list, or "
    "item_name) when the user says they washed something. Outfit and "
    "item images are rendered as cards automatically — DO NOT repeat "
    "URLs in your reply; speak in 1-2 short sentences naming the key "
    "pieces or stat."
)
