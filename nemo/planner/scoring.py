"""Frontier scoring functions."""

from __future__ import annotations

import json
from collections import Counter

from nemo.graph import recall_learnings
from nemo.planner.models import FrontierItem


def score_item(
    item: FrontierItem,
    ctx,
    learnings: list[dict] | None = None,
) -> float:
    """
    Deterministic utility score for a frontier item.

    Score = weighted info_gain + impact + novelty + feasibility + diversity + learning adjustment.
    """
    learnings = learnings or []
    table = str(item.payload.get("table", ""))
    metric_col = str(item.payload.get("metric_col", ""))

    info_gain = _info_gain_proxy(table, ctx.recent_insights)
    impact = _impact_proxy(table, metric_col, ctx.profiles)
    novelty = _novelty_proxy(item, ctx.recent_insights)
    feasibility = _feasibility_proxy(item, table, ctx)
    diversity = _diversity_bonus(item, ctx.recent_insights)
    learning_adjustment = _learning_adjustment(item, learnings)

    score = (
        (ctx.config.weight_info_gain * info_gain)
        + (ctx.config.weight_impact * impact)
        + (ctx.config.weight_novelty * novelty)
        + (ctx.config.weight_feasibility * feasibility)
        + (ctx.config.weight_diversity * diversity)
        + learning_adjustment
    )
    return round(max(0.0, min(score, 1.0)), 6)


def score_frontier(items: list[FrontierItem], ctx) -> list[FrontierItem]:
    """Score all items, sort descending, and return."""
    learnings: list[dict] = []
    if getattr(ctx.config, "use_learnings", True):
        learnings = _load_learnings(ctx)

    scored = [item.model_copy(update={"score": score_item(item, ctx, learnings=learnings)}) for item in items]
    return sorted(scored, key=lambda item: (-item.score, item.created_at, item.dedupe_key))


def _info_gain_proxy(table: str, recent_insights: list[dict]) -> float:
    if not table:
        return 0.5
    count = 0
    for insight in recent_insights:
        source = str(insight.get("source_tables_json") or "")
        if table in source:
            count += 1
    return 1.0 / (1.0 + count)


def _impact_proxy(table: str, metric_col: str, profiles) -> float:
    for profile in profiles:
        if profile.name != table:
            continue
        for col in profile.columns:
            if metric_col and col.name != metric_col:
                continue
            cardinality = min(1.0, float(col.cardinality_ratio))
            spread = 0.0 if col.stddev is None else min(1.0, abs(float(col.stddev)) / 100.0)
            return max(cardinality, spread, 0.35)
    return 0.4


def _novelty_proxy(item: FrontierItem, recent_insights: list[dict]) -> float:
    dedupe = item.dedupe_key.lower()
    for insight in recent_insights:
        detail = " ".join(
            [
                str(insight.get("title") or ""),
                str(insight.get("question") or ""),
                str(insight.get("claim") or ""),
            ]
        ).lower()
        if dedupe and dedupe in detail:
            return 0.1
    return 1.0


def _feasibility_proxy(item: FrontierItem, table: str, ctx) -> float:
    runtime_budget = max(1, int(ctx.config.max_query_runtime_ms))
    estimated_runtime = int(item.payload.get("estimated_runtime_ms", runtime_budget // 2))
    runtime_score = 1.0 - min(1.0, estimated_runtime / runtime_budget)

    row_penalty = 0.0
    for profile in ctx.profiles:
        if profile.name == table and profile.row_count > 0:
            row_penalty = min(0.5, profile.row_count / 5_000_000)
            break
    return max(0.0, runtime_score - row_penalty)


def _diversity_bonus(item: FrontierItem, recent_insights: list[dict]) -> float:
    type_counts = Counter(str(insight.get("title") or "").split(":", 1)[0] for insight in recent_insights)
    count = type_counts.get(item.action_type, 0)
    return max(0.0, min(1.0, 1.0 - (0.15 * count)))


def _learning_adjustment(item: FrontierItem, learnings: list[dict]) -> float:
    if not learnings:
        return 0.0
    delta = 0.0
    dedupe_key = item.dedupe_key.lower()
    for learning in learnings:
        subject = str(learning.get("subject") or "").lower()
        detail = str(learning.get("detail") or "").lower()
        confidence = float(learning.get("confidence", 0.5) or 0.5)
        if subject and subject in dedupe_key:
            delta += 0.05 * confidence
        if "noisy" in detail and item.action_type.lower() in detail:
            delta -= 0.05 * confidence
    return max(-0.2, min(delta, 0.2))


def _load_learnings(ctx) -> list[dict]:
    context = {
        "tables": [profile.name for profile in ctx.profiles],
        "columns": [column.name for profile in ctx.profiles for column in profile.columns],
        "action_types": [
            str(item.get("action_type") or "") for item in ctx.store.get_frontier_queue(status="queued", limit=50)
        ],
    }
    rows = recall_learnings(ctx.store, context)
    if rows:
        return rows
    return []


def derive_recent_insight_keys(recent_insights: list[dict]) -> set[str]:
    """Best-effort extraction of dedupe-like keys from recent insight metadata."""
    keys: set[str] = set()
    for insight in recent_insights:
        explicit = insight.get("dedupe_key")
        if isinstance(explicit, str) and explicit:
            keys.add(explicit)

        summary_raw = insight.get("result_summary_json")
        if isinstance(summary_raw, str):
            try:
                parsed = json.loads(summary_raw)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                dedupe = parsed.get("dedupe_key")
                if isinstance(dedupe, str) and dedupe:
                    keys.add(dedupe)
    return keys
