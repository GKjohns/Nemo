"""DuckDB-backed storage for Nemo system tables."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import duckdb

SYSTEM_TABLES = (
    "datasets",
    "insights",
    "edges",
    "frontier",
    "runs",
    "thread_cards",
    "learnings",
)


class NemoStore:
    """Thin wrapper around a DuckDB database file."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))

    def initialize(self) -> None:
        """Apply the bundled schema.sql file."""
        schema_path = Path(__file__).with_name("schema.sql")
        schema_sql = schema_path.read_text(encoding="utf-8")
        self.conn.execute(schema_sql)

    def execute(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> duckdb.DuckDBPyConnection:
        """Execute SQL directly against the DuckDB connection."""
        if params is None:
            return self.conn.execute(sql)
        return self.conn.execute(sql, params)

    def insert_dataset(
        self,
        name: str,
        source_uri: str,
        fmt: str = "csv",
        notes: str | None = None,
        schema_json: str | dict[str, Any] | None = None,
    ) -> str:
        dataset_id = _new_id("dataset")
        schema_payload = _json_or_none(schema_json)
        self.execute(
            """
            INSERT INTO datasets (dataset_id, name, source_uri, format, notes, schema_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [dataset_id, name, source_uri, fmt, notes, schema_payload],
        )
        return dataset_id

    def insert_insight(
        self,
        title: str,
        question: str,
        sql: str,
        result_summary_json: str | dict[str, Any],
        claim: str,
        run_id: str | None = None,
        thread_id: str | None = None,
        confidence: float = 0.5,
        status: str = "ok",
    ) -> str:
        insight_id = _new_id("insight")
        self.execute(
            """
            INSERT INTO insights (
                insight_id, run_id, thread_id, title, question, sql,
                result_summary_json, claim, confidence, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                insight_id,
                run_id,
                thread_id,
                title,
                question,
                sql,
                _json_or_none(result_summary_json),
                claim,
                confidence,
                status,
            ],
        )
        return insight_id

    def insert_edge(
        self,
        from_insight_id: str,
        to_insight_id: str,
        edge_type: str,
        weight: float = 0.5,
        rationale: str | None = None,
    ) -> str:
        edge_id = _new_id("edge")
        self.execute(
            """
            INSERT INTO edges (edge_id, from_insight_id, to_insight_id, type, weight, rationale)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [edge_id, from_insight_id, to_insight_id, edge_type, weight, rationale],
        )
        return edge_id

    def insert_frontier_item(
        self,
        action_type: str,
        payload_json: str | dict[str, Any],
        dedupe_key: str,
        run_id: str | None = None,
        thread_id: str | None = None,
        score: float = 0.0,
        status: str = "queued",
    ) -> str:
        action_id = _new_id("action")
        self.execute(
            """
            INSERT INTO frontier (action_id, run_id, thread_id, action_type, payload_json, score, status, dedupe_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                action_id,
                run_id,
                thread_id,
                action_type,
                _json_or_none(payload_json),
                score,
                status,
                dedupe_key,
            ],
        )
        return action_id

    def insert_run(self, config_json: str | dict[str, Any], status: str = "running") -> str:
        run_id = _new_id("run")
        self.execute(
            "INSERT INTO runs (run_id, status, config_json) VALUES (?, ?, ?)",
            [run_id, status, _json_or_none(config_json)],
        )
        return run_id

    def insert_thread_card(
        self,
        thread_id: str,
        title: str,
        summary_text: str | None = None,
        key_insight_ids_json: str | list[str] | None = None,
        open_questions_json: str | list[str] | None = None,
        contradictions_json: str | list[str] | None = None,
    ) -> str:
        self.execute(
            """
            INSERT INTO thread_cards (
                thread_id, title, summary_text, key_insight_ids_json, open_questions_json, contradictions_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                thread_id,
                title,
                summary_text,
                _json_or_none(key_insight_ids_json),
                _json_or_none(open_questions_json),
                _json_or_none(contradictions_json),
            ],
        )
        return thread_id

    def insert_learning(
        self,
        category: str,
        subject: str,
        detail: str,
        run_id: str | None = None,
        confidence: float = 0.5,
        times_confirmed: int = 1,
    ) -> str:
        learning_id = _new_id("learning")
        self.execute(
            """
            INSERT INTO learnings (learning_id, run_id, category, subject, detail, confidence, times_confirmed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [learning_id, run_id, category, subject, detail, confidence, times_confirmed],
        )
        return learning_id

    def get_frontier_queue(self, status: str = "queued", limit: int = 50) -> list[dict[str, Any]]:
        return self._query_dicts(
            """
            SELECT * FROM frontier
            WHERE status = ?
            ORDER BY score DESC, created_at ASC
            LIMIT ?
            """,
            [status, limit],
        )

    def get_recent_insights(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._query_dicts(
            """
            SELECT * FROM insights
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [limit],
        )

    def get_edges_for_insight(self, insight_id: str) -> list[dict[str, Any]]:
        return self._query_dicts(
            """
            SELECT * FROM edges
            WHERE from_insight_id = ? OR to_insight_id = ?
            ORDER BY created_at DESC
            """,
            [insight_id, insight_id],
        )

    def get_datasets(self) -> list[dict[str, Any]]:
        return self._query_dicts("SELECT * FROM datasets ORDER BY created_at DESC")

    def table_exists(self, table_name: str) -> bool:
        row = self.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table_name],
        ).fetchone()
        return bool(row and row[0] > 0)

    def close(self) -> None:
        self.conn.close()

    def _query_dicts(
        self,
        sql: str,
        params: list[Any] | tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        cursor = self.execute(sql, params)
        rows = cursor.fetchall()
        if not rows:
            return []
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row, strict=False)) for row in rows]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)
