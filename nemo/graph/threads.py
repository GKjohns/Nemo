"""Thread card scaffolding."""

from __future__ import annotations

import hashlib
import json

from nemo.config import NemoConfig
from nemo.graph.contradictions import find_contradiction_clusters
from nemo.store import NemoStore


def update_thread_cards(store: NemoStore, config: NemoConfig) -> list[str]:
    """
    Create or update thread cards for contradiction clusters and metric groups.
    """
    thread_ids: list[str] = []
    clusters = find_contradiction_clusters(store)
    for cluster in clusters:
        insight_ids = [str(v) for v in cluster.get("insight_ids", [])]
        if len(insight_ids) < 2:
            continue
        digest = hashlib.sha1("|".join(sorted(insight_ids)).encode("utf-8")).hexdigest()[:12]
        thread_id = f"thread_contradiction_{digest}"
        title = f"Contradiction cluster ({len(insight_ids)} insights)"
        summary_text = "Conflicting claims need disambiguation."
        _upsert_thread_card(
            store=store,
            thread_id=thread_id,
            title=title,
            summary_text=summary_text,
            key_insight_ids_json=insight_ids,
            open_questions_json=cluster.get("open_questions", []),
            contradictions_json=cluster.get("claims", []),
        )
        thread_ids.append(thread_id)

    configured_metrics = [str(metric) for metric in config.key_metrics.values()]
    if configured_metrics:
        rows = store.execute(
            """
            SELECT insight_id,
                   COALESCE(json_extract_string(claim_struct_json, '$.metric'), '') AS metric,
                   claim
            FROM insights
            ORDER BY created_at DESC
            LIMIT 400
            """
        ).fetchall()
        metric_map: dict[str, list[tuple[str, str]]] = {metric: [] for metric in configured_metrics}
        for insight_id, metric, claim in rows:
            metric_name = str(metric or "")
            if metric_name in metric_map:
                metric_map[metric_name].append((str(insight_id), str(claim or "")))
        for metric_name, members in metric_map.items():
            if len(members) < 2:
                continue
            digest = hashlib.sha1(metric_name.encode("utf-8")).hexdigest()[:12]
            thread_id = f"thread_metric_{digest}"
            _upsert_thread_card(
                store=store,
                thread_id=thread_id,
                title=f"Metric thread: {metric_name}",
                summary_text=f"Related insights for configured metric `{metric_name}`.",
                key_insight_ids_json=[member[0] for member in members[:12]],
                open_questions_json=[f"What drives variation in {metric_name}?"],
                contradictions_json=[member[1] for member in members[:6]],
            )
            thread_ids.append(thread_id)
    return thread_ids


def _upsert_thread_card(
    store: NemoStore,
    thread_id: str,
    title: str,
    summary_text: str,
    key_insight_ids_json: list[str],
    open_questions_json: list[str],
    contradictions_json: list[str],
) -> None:
    exists = store.execute("SELECT 1 FROM thread_cards WHERE thread_id = ? LIMIT 1", [thread_id]).fetchone()
    if exists:
        store.execute(
            """
            UPDATE thread_cards
            SET updated_at = now(),
                title = ?,
                summary_text = ?,
                key_insight_ids_json = ?,
                open_questions_json = ?,
                contradictions_json = ?
            WHERE thread_id = ?
            """,
            [
                title,
                summary_text,
                json.dumps(key_insight_ids_json),
                json.dumps(open_questions_json),
                json.dumps(contradictions_json),
                thread_id,
            ],
        )
        return
    store.insert_thread_card(
        thread_id=thread_id,
        title=title,
        summary_text=summary_text,
        key_insight_ids_json=key_insight_ids_json,
        open_questions_json=open_questions_json,
        contradictions_json=contradictions_json,
    )
