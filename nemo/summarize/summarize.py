"""Result summarization into InsightDraft artifacts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel
from nemo.config import NemoConfig
from nemo.executor.run import ExecutionResult
from nemo.ingest.profile import TableProfile
from nemo.planner.models import FrontierItem
from nemo.summarize.canonicalize import canonicalize_claim, canonicalize_hypothesis


@dataclass
class InsightDraft:
    title: str
    question: str
    claim: str
    confidence: float
    effect_size: float | None
    tags: list[str]
    hypothesis_struct: dict
    claim_struct: dict
    result_summary: dict
    result_sample: list[dict]
    reasoning: str | None = None


class SummaryExtraction(BaseModel):
    title: str
    claim: str
    confidence: float
    effect_size: float | None
    tags: list[str]


def make_client(config: NemoConfig) -> OpenAI | None:
    """Create one OpenAI client for the run when key is available."""
    if not config.openai_api_key:
        return None
    return OpenAI(api_key=config.openai_api_key, max_retries=3)


async def summarize_result(
    action: FrontierItem,
    result: ExecutionResult,
    profiles: list[TableProfile],
    recent_insights: list[dict],
    config: NemoConfig,
    client: OpenAI | None = None,
) -> InsightDraft:
    """
    Build a structured insight from one execution result.

    The v0 implementation is deterministic and local-first so tests and offline
    execution stay stable. LLM upgrades can replace this function later.
    """
    _ = profiles
    question = _question_for_action(action)
    if result.error:
        claim = f"Query failed for {action.action_type}: {result.error}"
        confidence = 0.1
        tags = ["error", action.action_type.lower()]
        title = f"{action.action_type}: {action.payload.get('table', 'analysis')}"
        effect_size = None
    elif result.row_count == 0:
        claim = f"No rows returned for {action.action_type} on this slice."
        confidence = 0.25
        tags = ["empty", action.action_type.lower()]
        title = f"{action.action_type}: {action.payload.get('table', 'analysis')}"
        effect_size = None
    else:
        title = f"{action.action_type}: {action.payload.get('table', 'analysis')}"
        effect_size = None
        llm_summary = await _summarize_with_llm(
            action=action,
            result=result,
            recent_insights=recent_insights,
            model=config.model,
            client=client,
        )
        if llm_summary is not None:
            title = llm_summary["title"]
            claim = llm_summary["claim"]
            confidence = llm_summary["confidence"]
            effect_size = llm_summary.get("effect_size")
            tags = llm_summary["tags"]
        else:
            claim = _fallback_claim(action, result)
            confidence = min(0.95, max(0.35, 0.35 + (result.row_count / 100)))
            tags = ["ok", action.action_type.lower()]

    result_summary = {
        "row_count": result.row_count,
        "columns": result.column_names,
        "truncated": result.truncated,
        "cost_ms": result.cost_ms,
        "error": result.error,
        "dedupe_key": action.dedupe_key,
    }
    hypothesis_struct = canonicalize_hypothesis(question, config, client=client)
    claim_struct = canonicalize_claim(claim, config, client=client)
    return InsightDraft(
        title=title,
        question=question,
        claim=claim,
        confidence=round(confidence, 3),
        effect_size=effect_size if effect_size is not None else claim_struct.get("magnitude"),
        tags=tags,
        hypothesis_struct=hypothesis_struct,
        claim_struct=claim_struct,
        result_summary=result_summary,
        result_sample=result.rows[:10],
    )


def _fallback_claim(action: FrontierItem, result: ExecutionResult) -> str:
    """Deterministic claim text that still yields structured edge diversity."""
    first_row = result.rows[0]
    preview = ", ".join(f"{key}={value}" for key, value in list(first_row.items())[:2])
    metric_name = str(action.payload.get("metric_col") or "metric")
    table_name = str(action.payload.get("table") or "dataset")
    direction_map = {
        "METRIC_TREND_SCAN": "higher",
        "CHANGEPOINT_DETECT": "higher",
        "TOP_GROUPS": "higher",
        "OUTLIER_GROUPS": "lower",
        "DATA_QUALITY_CHECK": "lower",
        "SEGMENT_COMPARE": "different",
        "CORRELATION_SCAN": "different",
        "COVERAGE_EXPLORER": "different",
        "ROBUSTNESS_CHECK": "no_change",
    }
    direction = direction_map.get(action.action_type, "different")
    if direction == "higher":
        return f"{metric_name} is higher in all rows for {table_name}; top result: {preview}"
    if direction == "lower":
        return f"{metric_name} is lower in all rows for {table_name}; top result: {preview}"
    if direction == "no_change":
        return f"{metric_name} is unchanged in all rows for {table_name}; top result: {preview}"
    return f"{metric_name} is different in all rows for {table_name}; top result: {preview}"


def _question_for_action(action: FrontierItem) -> str:
    payload = action.payload
    table = payload.get("table")
    metric = payload.get("metric_col")
    dim = payload.get("dimension_col") or payload.get("group_col")
    if metric and dim:
        return f"How does {metric} vary across {dim} in {table}?"
    if metric and table:
        return f"What pattern exists in {metric} for {table}?"
    if table:
        return f"What does {action.action_type} reveal about {table}?"
    return f"What does {action.action_type} reveal?"


async def _summarize_with_llm(
    action: FrontierItem,
    result: ExecutionResult,
    recent_insights: list[dict[str, Any]],
    model: str,
    client: OpenAI | None,
) -> dict[str, Any] | None:
    if client is None:
        return None

    row_preview = result.rows[:20]
    insights_preview = [
        {
            "title": str(i.get("title", "")),
            "claim": str(i.get("claim", "")),
            "confidence": float(i.get("confidence", 0.0) or 0.0),
        }
        for i in recent_insights[:8]
    ]
    prompt = {
        "action_type": action.action_type,
        "payload": action.payload,
        "sql": result.sql,
        "row_count": result.row_count,
        "columns": result.column_names,
        "rows": row_preview,
        "recent_insights": insights_preview,
    }
    instructions = (
        "You are an expert data analyst examining query results from an automated data exploration system. "
        "Your job is to extract meaningful, actionable insights — the kind a business analyst would care about.\n\n"
        "RULES:\n"
        "- Confidence must be between 0 and 1.\n"
        "- Keep the title short (≤10 words) and describe the FINDING, not the query.\n"
        "- Focus on business-relevant patterns: revenue drivers, cost anomalies, segment differences, "
        "supply-chain outliers, customer behavior, quality issues, etc.\n"
        "- If the result is trivial or tautological (e.g. averaging a primary/surrogate key, "
        "computing stats on sequential IDs, correlating key columns), set confidence ≤ 0.2 "
        "and prefix the title with '[trivial]'.\n"
        "- If a finding merely restates the table schema or row count with no deeper pattern, "
        "set confidence ≤ 0.3.\n"
        "- Higher confidence (0.7+) requires a non-obvious pattern with clear business meaning.\n"
        "- effect_size should quantify the magnitude of the finding (e.g. percentage difference, "
        "z-score, correlation coefficient) — set null if not applicable.\n"
    )

    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                client.responses.parse,
                model=model or "gpt-5-mini",
                instructions=instructions,
                input=str(prompt),
                reasoning={"effort": "low"},
                text_format=SummaryExtraction,
            )
            if getattr(response, "refusal", None):
                return None
            parsed = response.output_parsed
            if parsed is None:
                return None
            obj = parsed.model_dump()
            obj["confidence"] = float(min(1.0, max(0.0, obj.get("confidence", 0.5))))
            obj["tags"] = [str(tag) for tag in (obj.get("tags") or [])]
            return obj
        except (APIConnectionError, APITimeoutError):
            if attempt == 2:
                return None
            await asyncio.sleep(2**attempt)
        except RateLimitError:
            if attempt == 2:
                return None
            await asyncio.sleep(5 * (attempt + 1))
        except Exception:  # noqa: BLE001
            return None
    return None
