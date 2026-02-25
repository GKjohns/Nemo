"""Markdown brief generation helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nemo.graph import find_contradiction_clusters
from nemo.store import NemoStore


def generate_brief_markdown(store: NemoStore, top_n: int = 10) -> str:
    """Build a markdown summary for recent run outcomes."""
    insights = store.execute(
        """
        SELECT insight_id, title, claim, confidence, source_tables_json, created_at
        FROM insights
        WHERE status = 'ok'
        ORDER BY confidence DESC, created_at DESC
        LIMIT ?
        """,
        [max(1, int(top_n))],
    ).fetchall()
    clusters = find_contradiction_clusters(store)
    latest_run = store.execute(
        """
        SELECT run_id, status, steps_completed, insights_created, errors, started_at, ended_at
        FROM runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    ).fetchone()
    graph_counts = store.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM insights) AS insights_count,
            (SELECT COUNT(*) FROM edges) AS edges_count,
            (SELECT AVG(confidence) FROM insights WHERE status = 'ok') AS avg_confidence
        """
    ).fetchone()
    coverage = _coverage_summary(store)
    recommendations = _recommendations(insights, clusters, coverage)

    lines: list[str] = []
    lines.append("# Nemo Brief")
    lines.append("")
    lines.append("## Run Summary")
    if latest_run is None:
        lines.append("- No runs found yet.")
    else:
        lines.append(f"- Run: `{latest_run[0]}` (`{latest_run[1]}`)")
        lines.append(f"- Steps completed: {int(latest_run[2] or 0)}")
        lines.append(f"- Insights created: {int(latest_run[3] or 0)}")
        lines.append(f"- Errors: {int(latest_run[4] or 0)}")
        lines.append(f"- Started: {latest_run[5]}")
        lines.append(f"- Ended: {latest_run[6]}")
    lines.append("")

    lines.append("## Top Insights")
    if not insights:
        lines.append("- No insights available yet.")
    else:
        for row in insights:
            insight_id = str(row[0])
            title = str(row[1] or "Untitled")
            claim = str(row[2] or "")
            confidence = float(row[3] or 0.0)
            source_tables = ", ".join(_json_list(row[4])) or "unknown"
            lines.append(
                f"- `{insight_id}` **{title}** (confidence {confidence:.2f}, tables: {source_tables}) - {claim}"
            )
    lines.append("")

    lines.append("## Contradictions")
    if not clusters:
        lines.append("- No contradiction clusters detected.")
    else:
        for idx, cluster in enumerate(clusters[:5], start=1):
            insight_ids = cluster.get("insight_ids", [])
            claims = cluster.get("claims", [])
            lines.append(f"- Cluster {idx}: {len(insight_ids)} insights in tension")
            for claim in claims[:2]:
                lines.append(f"  - {claim}")
    lines.append("")

    lines.append("## Coverage")
    lines.append(f"- Tables loaded: {coverage['tables_total']}")
    lines.append(f"- Tables touched by insights: {coverage['tables_touched']}")
    lines.append(f"- Coverage ratio: {coverage['ratio']:.1%}")
    if graph_counts is not None:
        lines.append(f"- Total insights: {int(graph_counts[0] or 0)}")
        lines.append(f"- Total edges: {int(graph_counts[1] or 0)}")
        lines.append(f"- Average confidence: {float(graph_counts[2] or 0.0):.2f}")
    lines.append("")

    lines.append("## Recommendations")
    for recommendation in recommendations:
        lines.append(f"- {recommendation}")
    lines.append("")
    return "\n".join(lines)


def write_brief_report(store: NemoStore, output_path: Path, top_n: int = 10) -> Path:
    """Generate and write the markdown brief to disk."""
    markdown = generate_brief_markdown(store, top_n=top_n)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


def _coverage_summary(store: NemoStore) -> dict[str, Any]:
    dataset_rows = store.execute("SELECT name FROM datasets").fetchall()
    tables_total = len(dataset_rows)
    touched: set[str] = set()
    for row in store.execute("SELECT source_tables_json FROM insights WHERE source_tables_json IS NOT NULL").fetchall():
        touched.update(_json_list(row[0]))
    tables_touched = len(touched)
    ratio = (tables_touched / tables_total) if tables_total else 0.0
    return {"tables_total": tables_total, "tables_touched": tables_touched, "ratio": ratio}


def _recommendations(insights: list[tuple[Any, ...]], clusters: list[dict[str, Any]], coverage: dict[str, Any]) -> list[str]:
    suggestions: list[str] = []
    if not insights:
        suggestions.append("Run `nemo run --steps 15` to generate an initial evidence set.")
        return suggestions
    if coverage["ratio"] < 0.6:
        suggestions.append("Coverage is low; prioritize unexplored tables with high-cardinality metrics.")
    if clusters:
        suggestions.append("Investigate contradiction clusters with targeted slices or additional joins.")
    low_conf = sum(1 for row in insights if float(row[3] or 0.0) < 0.4)
    if low_conf > max(1, len(insights) // 3):
        suggestions.append("Many top insights are low confidence; add cleaner dimensions or tighter filters.")
    if not suggestions:
        suggestions.append("Current signal quality looks healthy; continue exploring adjacent dimensions.")
    return suggestions


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
