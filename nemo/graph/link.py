"""Evidence graph edge creation logic."""

from __future__ import annotations

import json
from typing import Any

from nemo.config import NemoConfig
from nemo.store import NemoStore


def link_insight(store: NemoStore, new_insight: dict[str, Any], config: NemoConfig) -> list[dict[str, Any]]:
    """Link a new insight to recent insights using simple claim heuristics."""
    _ = config
    recent = store.get_recent_insights(limit=50)
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
