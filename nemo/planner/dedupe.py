"""Frontier deduplication utilities."""

from __future__ import annotations

import json

from nemo.planner.models import FrontierItem


def dedupe_frontier(
    new_items: list[FrontierItem],
    existing_keys: set[str],
    recent_insight_keys: set[str],
) -> list[FrontierItem]:
    """
    Remove items where:
    1. dedupe_key matches a recently completed or queued action
    2. dedupe_key matches a key derivable from a recent insight
    3. Exact payload match against existing batch items
    """
    seen_keys = set(existing_keys) | set(recent_insight_keys)
    seen_payloads: set[str] = set()
    deduped: list[FrontierItem] = []

    for item in new_items:
        if not item.dedupe_key or item.dedupe_key in seen_keys:
            continue

        payload_signature = json.dumps(item.payload, sort_keys=True, separators=(",", ":"))
        if payload_signature in seen_payloads:
            continue

        seen_keys.add(item.dedupe_key)
        seen_payloads.add(payload_signature)
        deduped.append(item)

    return deduped
