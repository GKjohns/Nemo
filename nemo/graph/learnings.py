"""Cross-run learning extraction and recall."""

from __future__ import annotations

from typing import Any

from nemo.store import NemoStore


def record_learnings(store: NemoStore, run_id: str) -> list[str]:
    """Extract reusable patterns from one completed run."""
    learning_ids: list[str] = []
    learning_ids.extend(_record_generator_hit_rates(store, run_id))
    learning_ids.extend(_record_error_patterns(store, run_id))
    learning_ids.extend(_record_metric_signal(store, run_id))
    return learning_ids


def recall_learnings(store: NemoStore, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Return relevant learnings based on current tables/columns context."""
    rows = store.execute(
        """
        SELECT learning_id, category, subject, detail, confidence, times_confirmed
        FROM learnings
        ORDER BY confidence DESC, created_at DESC
        LIMIT 200
        """
    ).fetchall()
    if not rows:
        return []

    tables = [str(t).lower() for t in context.get("tables", [])]
    columns = [str(c).lower() for c in context.get("columns", [])]
    action_types = [str(a).lower() for a in context.get("action_types", [])]
    tokens = [*tables, *columns, *action_types]

    recalled: list[dict[str, Any]] = []
    for row in rows:
        learning = {
            "learning_id": str(row[0]),
            "category": str(row[1]),
            "subject": str(row[2]),
            "detail": str(row[3]),
            "confidence": float(row[4] or 0.5),
            "times_confirmed": int(row[5] or 1),
        }
        if not tokens:
            recalled.append(learning)
            continue
        haystack = f"{learning['subject']} {learning['detail']}".lower()
        if any(token and token in haystack for token in tokens):
            recalled.append(learning)
    return recalled[:50]


def _record_generator_hit_rates(store: NemoStore, run_id: str) -> list[str]:
    rows = store.execute(
        """
        SELECT action_type,
               SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS completed,
               COUNT(*) AS total
        FROM frontier
        WHERE run_id = ?
        GROUP BY action_type
        """,
        [run_id],
    ).fetchall()
    learning_ids: list[str] = []
    for action_type, completed, total in rows:
        total_count = int(total or 0)
        if total_count <= 0:
            continue
        completed_count = int(completed or 0)
        hit_rate = completed_count / total_count
        detail = f"completed {completed_count}/{total_count} actions ({hit_rate:.2f} hit rate)"
        learning_ids.append(
            _upsert_learning(
                store=store,
                run_id=run_id,
                category="generator_hit_rate",
                subject=str(action_type),
                detail=detail,
                confidence=max(0.1, min(1.0, hit_rate)),
            )
        )
    return learning_ids


def _record_error_patterns(store: NemoStore, run_id: str) -> list[str]:
    rows = store.execute(
        """
        SELECT
            action_type,
            COALESCE(json_extract_string(payload_json, '$.table'), '') AS table_name,
            COUNT(*) AS failures
        FROM frontier
        WHERE run_id = ?
          AND status = 'error'
        GROUP BY action_type, table_name
        """,
        [run_id],
    ).fetchall()
    learning_ids: list[str] = []
    for action_type, table_name, failures in rows:
        fail_count = int(failures or 0)
        if fail_count <= 0:
            continue
        subject = f"{action_type}:{table_name or 'unknown'}"
        detail = f"{fail_count} errors for this action/table combo"
        confidence = min(1.0, 0.4 + (0.1 * fail_count))
        learning_ids.append(
            _upsert_learning(
                store=store,
                run_id=run_id,
                category="error_pattern",
                subject=str(subject),
                detail=detail,
                confidence=confidence,
            )
        )
    return learning_ids


def _record_metric_signal(store: NemoStore, run_id: str) -> list[str]:
    rows = store.execute(
        """
        SELECT
            COALESCE(json_extract_string(claim_struct_json, '$.metric'), '') AS metric,
            AVG(confidence) AS avg_conf,
            COUNT(*) AS n
        FROM insights
        WHERE run_id = ?
        GROUP BY metric
        """,
        [run_id],
    ).fetchall()
    learning_ids: list[str] = []
    for metric, avg_conf, count in rows:
        metric_name = str(metric or "")
        n = int(count or 0)
        if not metric_name or n <= 0:
            continue
        avg = float(avg_conf or 0.0)
        if avg >= 0.7:
            category = "useful_metric"
            detail = f"{n} insights with avg confidence {avg:.2f}"
            confidence = avg
        elif avg <= 0.35:
            category = "noisy_column"
            detail = f"{n} low-signal insights with avg confidence {avg:.2f}"
            confidence = max(0.1, 1.0 - avg)
        else:
            continue
        learning_ids.append(
            _upsert_learning(
                store=store,
                run_id=run_id,
                category=category,
                subject=metric_name,
                detail=detail,
                confidence=confidence,
            )
        )
    return learning_ids


def _upsert_learning(
    store: NemoStore,
    run_id: str,
    category: str,
    subject: str,
    detail: str,
    confidence: float,
) -> str:
    existing = store.execute(
        """
        SELECT learning_id, confidence, times_confirmed
        FROM learnings
        WHERE category = ? AND subject = ? AND detail = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [category, subject, detail],
    ).fetchone()
    if existing is None:
        return store.insert_learning(
            run_id=run_id,
            category=category,
            subject=subject,
            detail=detail,
            confidence=max(0.0, min(1.0, float(confidence))),
            times_confirmed=1,
        )

    learning_id = str(existing[0])
    prior_conf = float(existing[1] or 0.5)
    prior_times = int(existing[2] or 1)
    new_times = prior_times + 1
    new_conf = ((prior_conf * prior_times) + float(confidence)) / new_times
    store.execute(
        """
        UPDATE learnings
        SET run_id = ?,
            confidence = ?,
            times_confirmed = ?
        WHERE learning_id = ?
        """,
        [run_id, max(0.0, min(1.0, new_conf)), new_times, learning_id],
    )
    return learning_id
