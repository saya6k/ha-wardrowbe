#!/usr/bin/env python3
"""Fail if the vocabulary mirrored in const.py drifts from upstream Wardrowbe.

Upstream has no API for its garment vocabulary, so const.py copies it from
backend/app/data/garment_vocabulary.json. Standard library only, so the
scheduled workflow needs no Home Assistant install.
"""

from __future__ import annotations

import ast
import json
import sys
import urllib.request
from pathlib import Path

UPSTREAM = (
    "https://raw.githubusercontent.com/Anyesh/wardrowbe/main/"
    "backend/app/data/garment_vocabulary.json"
)
CONST = Path(__file__).resolve().parent.parent / "custom_components/wardrowbe/const.py"

# const.py name -> upstream vocabulary key
MIRRORS = {
    "VALID_OCCASIONS": "occasions",
    "ITEM_COLORS": "colors",
    "ITEM_SEASONS": "seasons",
    "ITEM_FORMALITY": "formality",
}


def _values(entries: list) -> set[str]:
    return {
        e if isinstance(e, str) else e.get("id") or e.get("name") or e.get("value")
        for e in entries
    }


def _const_tuples() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for node in ast.parse(CONST.read_text()).body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in MIRRORS
        ):
            found[node.target.id] = set(ast.literal_eval(node.value))
    return found


def main() -> int:
    with urllib.request.urlopen(UPSTREAM, timeout=30) as resp:
        upstream = json.load(resp)
    ours = _const_tuples()
    drift = False
    for name, key in MIRRORS.items():
        theirs = _values(upstream[key])
        mine = ours[name]
        if mine != theirs:
            drift = True
            print(f"{name} differs from upstream '{key}':")
            print(f"  missing here: {sorted(theirs - mine)}")
            print(f"  not upstream: {sorted(mine - theirs)}")
        else:
            print(f"{name}: in sync ({len(mine)} values)")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
