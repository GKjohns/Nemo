"""Frontier scheduling and saturation checks."""

from __future__ import annotations

import json

from nemo.planner.models import FrontierItem
from nemo.store import NemoStore


def select_next(
    store: NemoStore,
    config,
) -> FrontierItem | None:
    """
    Return the highest-scoring eligible queued action, or None.

    Eligibility filters:
    - thread budget (`max_actions_per_thread`) for items that specify `thread_id`
    - estimated runtime budget (`max_query_runtime_ms`) if provided in payload
    - saturation threshold (top eligible score must be >= threshold)
    """
    rows = store.get_frontier_queue(status="queued", limit=500)
    if not rows:
        return None

    thread_counts = _executed_thread_counts(store)
    candidates: list[FrontierItem] = []
    for row in rows:
        item = FrontierItem.from_store_row(row)
        if not _eligible_for_thread_budget(item, thread_counts, config.max_actions_per_thread):
            continue
        if not _eligible_for_runtime_budget(item, config.max_query_runtime_ms):
            continue
        candidates.append(item)

    if not candidates:
        return None

    best = max(candidates, key=lambda item: (item.score, item.created_at))
    if best.score < float(config.saturation_threshold):
        return None
    return best


def is_saturated(store: NemoStore, config) -> bool:
    """
    Check if exploration has reached saturation:
    - frontier is empty, or
    - all queued frontier items score below saturation threshold
    """
    rows = store.get_frontier_queue(status="queued", limit=1000)
    if not rows:
        return True
    return max(float(row.get("score") or 0.0) for row in rows) < float(config.saturation_threshold)


def _executed_thread_counts(store: NemoStore) -> dict[str, int]:
    rows = store.execute(
        """
        SELECT thread_id, COUNT(*) AS cnt
        FROM frontier
        WHERE thread_id IS NOT NULL
          AND status IN ('running', 'done')
        GROUP BY thread_id
        """
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _eligible_for_thread_budget(item: FrontierItem, thread_counts: dict[str, int], max_per_thread: int) -> bool:
    if not item.thread_id:
        return True
    if max_per_thread <= 0:
        return False
    return thread_counts.get(item.thread_id, 0) < int(max_per_thread)


def _eligible_for_runtime_budget(item: FrontierItem, max_query_runtime_ms: int) -> bool:
    payload = item.payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return True
    if not isinstance(payload, dict):
        return True
    estimated = payload.get("estimated_runtime_ms")
    if estimated is None:
        return True
    return int(estimated) <= int(max_query_runtime_ms)
