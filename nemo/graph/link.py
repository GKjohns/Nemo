"""Evidence graph edge creation logic."""

from __future__ import annotations

import json
import time
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, Field

from nemo.config import NemoConfig
from nemo.planner.models import EdgeClassification
from nemo.store import NemoStore


class EdgeClassificationBatch(BaseModel):
    """LLM response model for a candidate edge classification batch."""

    edges: list[EdgeClassification] = Field(default_factory=list)


def link_insight(
    store: NemoStore,
    new_insight: dict[str, Any],
    config: NemoConfig,
    client: OpenAI | None = None,
) -> list[dict[str, Any]]:
    """Link a new insight to recent insights using simple claim heuristics."""
    _ = config
    recent = store.get_recent_insights(limit=20)
    if client is not None:
        try:
            return classify_edge_batch(new_insight, recent, client)
        except Exception:  # noqa: BLE001
            pass
    return _link_insight_heuristic(new_insight, recent)


def classify_edge_batch(
    new_insight: dict[str, Any],
    candidates: list[dict[str, Any]],
    client: OpenAI,
    *,
    batch_size: int = 20,
) -> list[dict[str, Any]]:
    """Classify edges in batches using an LLM and return graph edge records."""
    new_id = str(new_insight.get("insight_id"))
    if not new_id:
        return []
    prior = [item for item in candidates if str(item.get("insight_id")) and str(item.get("insight_id")) != new_id]
    if not prior:
        return []

    batch = max(1, int(batch_size))
    results: list[dict[str, Any]] = []
    for idx in range(0, len(prior), batch):
        chunk = prior[idx : idx + batch]
        parsed = _classify_batch_llm(new_insight, chunk, client)
        for edge in parsed.edges:
            candidate_id = edge.to_insight_id.strip()
            if not candidate_id or edge.relationship == "none":
                continue
            if candidate_id not in {str(item.get("insight_id")) for item in chunk}:
                continue
            confidence = max(0.0, min(1.0, float(edge.confidence)))
            results.append(
                {
                    "from_insight_id": new_id,
                    "to_insight_id": candidate_id,
                    "type": edge.relationship,
                    "weight": 0.4 + (0.5 * confidence),
                    "rationale": edge.rationale.strip() or f"auto-link via {edge.relationship} llm",
                }
            )
    # De-dupe by (from,to,type); keep highest-weight edge.
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for edge in results:
        key = (str(edge["from_insight_id"]), str(edge["to_insight_id"]), str(edge["type"]))
        if key not in deduped or float(edge["weight"]) > float(deduped[key]["weight"]):
            deduped[key] = edge
    results = list(deduped.values())
    return results


def _link_insight_heuristic(new_insight: dict[str, Any], recent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    new_id = str(new_insight.get("insight_id"))
    new_claim = _json_dict(new_insight.get("claim_struct_json"))
    new_sources = set(_json_list(new_insight.get("source_tables_json")))
    for candidate in recent:
        candidate_id = str(candidate.get("insight_id"))
        if not candidate_id or candidate_id == new_id:
            continue
        edge_type = _infer_edge_type(new_claim, _json_dict(candidate.get("claim_struct_json")), new_sources, candidate)
        if edge_type is None:
            continue
        results.append(
            {
                "from_insight_id": new_id,
                "to_insight_id": candidate_id,
                "type": edge_type,
                "weight": 0.6 if edge_type != "contradicts" else 0.8,
                "rationale": f"auto-link via {edge_type} heuristic",
            }
        )
    return results


def _classify_batch_llm(
    new_insight: dict[str, Any],
    candidates: list[dict[str, Any]],
    client: OpenAI,
    *,
    timeout_seconds: float = 15.0,
) -> EdgeClassificationBatch:
    instructions = (
        "Classify relationships between a new analytics insight and prior insights. "
        "Allowed relationship values: supports, contradicts, refines, depends_on, none. "
        "Use none when there is no meaningful relation. "
        "Return concise rationale and confidence between 0 and 1 for each candidate."
    )
    payload = {
        "new_insight": _edge_prompt_entry(new_insight),
        "candidates": [_edge_prompt_entry(item) for item in candidates],
    }
    for attempt in range(3):
        try:
            response = client.responses.parse(
                model="gpt-5-nano",
                instructions=instructions,
                input=json.dumps(payload),
                text_format=EdgeClassificationBatch,
                timeout=timeout_seconds,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise RuntimeError("edge classification parse produced no output")
            return parsed
        except (APIConnectionError, APITimeoutError):
            if attempt == 2:
                raise
            time.sleep(2**attempt)
        except RateLimitError:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("classify_edge_batch: exhausted retries")


def _edge_prompt_entry(insight: dict[str, Any]) -> dict[str, Any]:
    return {
        "insight_id": str(insight.get("insight_id", "")),
        "title": str(insight.get("title", "")),
        "question": str(insight.get("question", "")),
        "claim": str(insight.get("claim", "")),
        "claim_struct": _json_dict(insight.get("claim_struct_json")),
        "source_tables": _json_list(insight.get("source_tables_json")),
    }


def _infer_edge_type(
    new_claim: dict[str, Any],
    prior_claim: dict[str, Any],
    new_sources: set[str],
    prior: dict[str, Any],
) -> str | None:
    if not new_claim or not prior_claim:
        return None
    same_metric = new_claim.get("metric") and new_claim.get("metric") == prior_claim.get("metric")
    same_population = new_claim.get("population") == prior_claim.get("population")
    new_direction = new_claim.get("direction")
    prior_direction = prior_claim.get("direction")
    if same_metric and same_population:
        if {new_direction, prior_direction} == {"higher", "lower"}:
            return "contradicts"
        if new_direction == prior_direction:
            return "supports"
        return "refines"

    prior_sources = set(_json_list(prior.get("source_tables_json")))
    if new_sources and prior_sources and (new_sources & prior_sources):
        return "depends_on"
    return None


def _json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(v) for v in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [part.strip() for part in raw.split(",") if part.strip()]
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    return []
