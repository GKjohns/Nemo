"""Frontier scoring functions."""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from nemo.graph import recall_learnings
from nemo.planner.models import FrontierItem, RerankedFrontier
from nemo.planner.strategist import Notebook, format_notebook


FRONTIER_RERANK_TOP_N = 8


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


def rerank_frontier(
    items: list[FrontierItem],
    notebook: Notebook,
    config,
    client: OpenAI | None,
    *,
    top_n: int = FRONTIER_RERANK_TOP_N,
) -> tuple[list[FrontierItem], list[FrontierItem]]:
    """Re-rank deterministically-scored frontier items with LLM editorial judgment."""
    deterministic = list(items)
    limit = max(1, int(top_n))
    if client is None or len(deterministic) <= 1:
        return deterministic, deterministic[:limit]

    head = deterministic[:limit]
    tail = deterministic[limit:]
    try:
        reranked_head = _rerank_top_candidates(head, notebook, config, client)
    except Exception:  # noqa: BLE001
        reranked_head = head
    return reranked_head + tail, head


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


def _rerank_top_candidates(
    top_candidates: list[FrontierItem],
    notebook: Notebook,
    config,
    client: OpenAI,
) -> list[FrontierItem]:
    payload = {
        "notebook": format_notebook(notebook),
        "candidates": [
            {
                "action_index": idx,
                "action_type": item.action_type,
                "table": str(item.payload.get("table") or ""),
                "target": _target_for_payload(item.payload),
                "deterministic_score": float(item.score),
                "rationale": item.rationale,
                "dedupe_key": item.dedupe_key,
            }
            for idx, item in enumerate(top_candidates)
        ],
    }
    instructions = (
        "You are a senior analytics lead. Re-rank frontier candidates from most to least valuable next step. "
        "Prioritize candidate investigations likely to produce surprising or actionable findings, and use notebook "
        "context to avoid redundant work. Return rankings that reference action_index values from the input."
    )
    parsed = _call_reranker_llm(payload, instructions, config, client)
    return _apply_rankings(top_candidates, parsed)


def _call_reranker_llm(
    payload: dict[str, Any],
    instructions: str,
    config,
    client: OpenAI,
) -> RerankedFrontier:
    model_name = str(getattr(config, "frontier_rerank_model", "gpt-5-nano") or "gpt-5-nano")
    for attempt in range(3):
        try:
            response = client.responses.parse(
                model=model_name,
                instructions=instructions,
                input=json.dumps(payload),
                text_format=RerankedFrontier,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise RuntimeError("frontier rerank parse produced no output")
            return parsed
        except (APIConnectionError, APITimeoutError):
            if attempt == 2:
                raise
            time.sleep(2**attempt)
        except RateLimitError:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("rerank_frontier: exhausted retries")


def _apply_rankings(
    candidates: list[FrontierItem],
    reranked: RerankedFrontier,
) -> list[FrontierItem]:
    by_index = {idx: item for idx, item in enumerate(candidates)}
    selected: list[FrontierItem] = []
    seen: set[int] = set()
    ordered = sorted(reranked.rankings, key=lambda row: int(row.rank))
    for row in ordered:
        idx = int(row.action_index)
        item = by_index.get(idx)
        if item is None or idx in seen:
            continue
        seen.add(idx)
        reason = row.reasoning.strip()
        if reason:
            item = item.model_copy(update={"rationale": reason})
        selected.append(item)
    for idx, item in by_index.items():
        if idx not in seen:
            selected.append(item)
    return selected


def _target_for_payload(payload: dict[str, Any]) -> str:
    target = str(payload.get("metric_col") or payload.get("dimension_col") or payload.get("target_col") or "")
    if target:
        return target
    return str(payload.get("question") or payload.get("table") or "-")
