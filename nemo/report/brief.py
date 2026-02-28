"""Markdown brief generation helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from nemo.graph import find_contradiction_clusters
from nemo.planner.arbiter import _format_hypotheses
from nemo.planner.models import HypothesisRecord
from nemo.planner.strategist import Notebook, format_notebook
from nemo.store import NemoStore


DEBRIEF_SYSTEM = """\
You are a senior analyst writing the executive debrief for an automated data \
investigation run. Your audience is a human who wants to understand what was \
explored, what was found, and what to do next — without reading raw logs.

Write in direct, professional prose. Use specific numbers and table/column names. \
Do not hedge excessively. If data quality issues were encountered, note them \
matter-of-factly. If a goal was provided, assess progress toward it."""


async def generate_run_debrief(
    notebook: Notebook,
    hypotheses: list[HypothesisRecord],
    stats: dict[str, Any],
    goal: str,
    client: OpenAI | None,
    model: str = "gpt-5.2",
) -> str:
    """Produce a narrative debrief from the run's notebook and hypothesis outcomes.

    Falls back to a formatted notebook dump when no LLM client is available.
    """
    context = _build_debrief_context(notebook, hypotheses, stats, goal)
    if client is None:
        return _fallback_debrief(notebook, hypotheses, stats, goal)

    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                client.responses.create,
                model=model,
                instructions=DEBRIEF_SYSTEM,
                input=[{"role": "user", "content": context}],
                reasoning={"effort": "medium"},
            )
            text = response.output_text
            if text and text.strip():
                return text.strip()
            return _fallback_debrief(notebook, hypotheses, stats, goal)
        except (APIConnectionError, APITimeoutError):
            if attempt == 2:
                return _fallback_debrief(notebook, hypotheses, stats, goal)
            await asyncio.sleep(2 ** attempt)
        except RateLimitError:
            if attempt == 2:
                return _fallback_debrief(notebook, hypotheses, stats, goal)
            await asyncio.sleep(5 * (attempt + 1))
        except Exception:  # noqa: BLE001
            return _fallback_debrief(notebook, hypotheses, stats, goal)
    return _fallback_debrief(notebook, hypotheses, stats, goal)


def _build_debrief_context(
    notebook: Notebook,
    hypotheses: list[HypothesisRecord],
    stats: dict[str, Any],
    goal: str,
) -> str:
    parts: list[str] = []

    parts.append("## Investigation Notebook")
    parts.append(format_notebook(notebook, detail="full"))

    parts.append("\n## Hypothesis Outcomes")
    parts.append(_format_hypotheses(hypotheses, include_resolved=True))

    parts.append("\n## Run Statistics")
    parts.append(f"Steps: {stats.get('steps', 0)}")
    parts.append(f"Insights created: {stats.get('insights_created', 0)}")
    parts.append(f"Errors: {stats.get('errors', 0)}")
    duration_ms = stats.get("duration_ms", 0)
    parts.append(f"Duration: {duration_ms / 1000:.0f}s ({duration_ms / 60000:.1f}m)")

    if goal.strip():
        parts.append(f"\n## Investigation Goal\n{goal}")

    parts.append(
        "\n## Task\n"
        "Write a concise investigation debrief (3-5 paragraphs). Cover:\n"
        "1. What was explored and the main findings (with specific numbers)\n"
        "2. Key hypotheses tested and their outcomes\n"
        "3. Data quality issues or methodology notes\n"
        "4. What remains open / recommended next steps\n"
        "5. Progress toward the investigation goal (if one was set)\n\n"
        "Write for a human reader. Be direct and specific."
    )

    return "\n".join(parts)


def _fallback_debrief(
    notebook: Notebook,
    hypotheses: list[HypothesisRecord],
    stats: dict[str, Any],
    goal: str,
) -> str:
    """Deterministic fallback when LLM is unavailable."""
    lines: list[str] = []
    lines.append(
        f"Completed {stats.get('steps', 0)} steps, "
        f"producing {stats.get('insights_created', 0)} insights "
        f"with {stats.get('errors', 0)} errors."
    )
    if goal.strip():
        lines.append(f"\nGoal: {goal}")

    if notebook.entries:
        lines.append("\nThemes explored:")
        for entry in notebook.entries:
            lines.append(f"  [{entry.theme}] {entry.summary}")
            for finding in entry.key_findings[-3:]:
                lines.append(f"    - {finding}")
            if entry.open_questions:
                lines.append(f"    Open: {entry.open_questions[0]}")

    if hypotheses:
        lines.append("\nHypothesis outcomes:")
        for h in hypotheses:
            verdict_part = f" — {h.verdict}" if h.verdict else ""
            lines.append(f"  [{h.status}] {h.claim}{verdict_part}")

    return "\n".join(lines)


def generate_brief_markdown(store: NemoStore, top_n: int = 10) -> str:
    """Build a markdown summary for recent run outcomes."""
    insights = store.execute(
        """
        SELECT insight_id, title, claim, confidence, source_tables_json, created_at, result_sample_json
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
    hypothesis_summary = _hypothesis_summary(store, latest_run[0] if latest_run is not None else None)

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
            stat_suffix = _format_statistical_suffix(row[6])
            lines.append(
                f"- `{insight_id}` **{title}** (confidence {confidence:.2f}, tables: {source_tables}) - "
                f"{claim}{stat_suffix}"
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

    lines.append("## Hypothesis Verdicts")
    if not hypothesis_summary["rows"]:
        lines.append("- No hypotheses recorded for the latest run.")
    else:
        lines.append(
            "- Status counts: "
            + ", ".join(
                f"{status}={count}" for status, count in sorted(hypothesis_summary["status_counts"].items())
            )
        )
        for row in hypothesis_summary["rows"]:
            lines.append(
                f"- `{row['hypothesis_id']}` **{row['status']}** "
                f"(confidence {row['confidence']:.2f}) — {row['claim']}"
            )
            if row["evidence_count"] > 0:
                lines.append(f"  - Evidence items: {row['evidence_count']}")
            if row["verdict"]:
                lines.append(f"  - Verdict: {row['verdict']}")
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


def _hypothesis_summary(store: NemoStore, run_id: str | None) -> dict[str, Any]:
    if not run_id:
        return {"rows": [], "status_counts": {}}
    rows = store.load_hypotheses(run_id)
    status_counts: dict[str, int] = {}
    parsed_rows: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        evidence = _json_list(row.get("evidence_chain"))
        parsed_rows.append(
            {
                "hypothesis_id": str(row.get("hypothesis_id") or ""),
                "status": status,
                "claim": str(row.get("claim") or ""),
                "confidence": float(
                    row.get("verdict_confidence")
                    if row.get("verdict_confidence") is not None
                    else row.get("initial_confidence")
                    or 0.0
                ),
                "verdict": str(row.get("verdict") or ""),
                "evidence_count": len(evidence),
            }
        )
    parsed_rows.sort(key=lambda item: (item["status"], -item["confidence"], item["hypothesis_id"]))
    return {"rows": parsed_rows, "status_counts": status_counts}


def _format_statistical_suffix(raw_result_sample: Any) -> str:
    rows = _parse_json_rows(raw_result_sample)
    if not rows:
        return ""
    first = rows[0]
    test = str(first.get("test") or "").strip()
    if not test:
        return ""
    p_value = first.get("p_value")
    effect = first.get("effect_size")
    p_display = f"{float(p_value):.4g}" if isinstance(p_value, (float, int)) else "n/a"
    effect_display = f"{float(effect):.3f}" if isinstance(effect, (float, int)) else "n/a"
    return f" _(stats: {test}, p={p_display}, effect={effect_display})_"


def _parse_json_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return []


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
