"""DuckDB-backed storage for Nemo system tables."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time
from decimal import Decimal
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
    "notebooks",
    "learnings",
    "hypotheses",
)


class NemoStore:
    """Thin wrapper around a DuckDB database file."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self._migrate()

    def initialize(self) -> None:
        """Apply the bundled schema.sql file and run any needed migrations."""
        schema_path = Path(__file__).with_name("schema.sql")
        schema_sql = schema_path.read_text(encoding="utf-8")
        self.conn.execute(schema_sql)
        self._migrate()

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
        hypothesis_struct_json: str | dict[str, Any] | None = None,
        result_sample_json: str | list[dict[str, Any]] | None = None,
        claim_struct_json: str | dict[str, Any] | None = None,
        effect_size: float | None = None,
        coverage: float | None = None,
        cost_ms: int | None = None,
        source_tables_json: str | list[str] | None = None,
        tags_json: str | list[str] | None = None,
        error_text: str | None = None,
        reasoning: str | None = None,
    ) -> str:
        insight_id = _new_id("insight")
        self.execute(
            """
            INSERT INTO insights (
                insight_id, run_id, thread_id, title, question, hypothesis_struct_json,
                sql, result_summary_json, result_sample_json, claim, claim_struct_json,
                confidence, effect_size, coverage, cost_ms, source_tables_json, tags_json,
                status, error_text, reasoning
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                insight_id,
                run_id,
                thread_id,
                title,
                question,
                _json_or_none(hypothesis_struct_json),
                sql,
                _json_or_none(result_summary_json),
                _json_or_none(result_sample_json),
                claim,
                _json_or_none(claim_struct_json),
                confidence,
                effect_size,
                coverage,
                cost_ms,
                _json_or_none(source_tables_json),
                _json_or_none(tags_json),
                status,
                error_text,
                reasoning,
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
        last_error: str | None = None,
        depends_on_action_id: str | None = None,
    ) -> str:
        action_id = _new_id("action")
        self.execute(
            """
            INSERT INTO frontier (
                action_id, run_id, thread_id, action_type, payload_json,
                score, status, last_error, depends_on_action_id, dedupe_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                action_id,
                run_id,
                thread_id,
                action_type,
                _json_or_none(payload_json),
                score,
                status,
                last_error,
                depends_on_action_id,
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

    def save_notebook(self, run_id: str, notebook_json: str | dict) -> None:
        payload = _json_or_none(notebook_json) or "{}"
        existing = self.execute(
            "SELECT run_id FROM notebooks WHERE run_id = ?", [run_id]
        ).fetchone()
        if existing:
            self.execute(
                "UPDATE notebooks SET notebook_json = ?, updated_at = now() WHERE run_id = ?",
                [payload, run_id],
            )
        else:
            self.execute(
                "INSERT INTO notebooks (run_id, notebook_json) VALUES (?, ?)",
                [run_id, payload],
            )

    def load_notebook(self, run_id: str) -> dict | None:
        rows = self._query_dicts(
            "SELECT notebook_json FROM notebooks WHERE run_id = ? LIMIT 1", [run_id]
        )
        if not rows:
            return None
        raw = rows[0].get("notebook_json", "{}")
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return raw if isinstance(raw, dict) else None

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

    def save_hypothesis(self, run_id: str, hypothesis: str | dict[str, Any]) -> None:
        payload: dict[str, Any]
        if isinstance(hypothesis, str):
            payload = json.loads(hypothesis)
        else:
            payload = dict(hypothesis)

        hypothesis_id = str(payload.get("hypothesis_id", ""))
        if not hypothesis_id:
            raise ValueError("hypothesis_id is required")

        existing = self.execute(
            "SELECT hypothesis_id FROM hypotheses WHERE hypothesis_id = ? LIMIT 1",
            [hypothesis_id],
        ).fetchone()
        if existing:
            self.execute(
                """
                UPDATE hypotheses
                SET run_id = ?, claim = ?, source_insight_id = ?, initial_confidence = ?,
                    status = ?, priority = ?, evidence_chain = ?, verdict = ?,
                    verdict_confidence = ?, validation_step = ?, tables_involved = ?,
                    updated_at = now()
                WHERE hypothesis_id = ?
                """,
                [
                    run_id,
                    str(payload.get("claim", "")),
                    str(payload.get("source_insight_id", "")),
                    float(payload.get("initial_confidence", 0.0) or 0.0),
                    str(payload.get("status", "proposed")),
                    float(payload.get("priority", 0.0) or 0.0),
                    _json_or_none(payload.get("evidence_chain", [])),
                    payload.get("verdict"),
                    payload.get("verdict_confidence"),
                    int(payload.get("validation_step", 0) or 0),
                    _json_or_none(payload.get("tables_involved", [])),
                    hypothesis_id,
                ],
            )
            return

        self.execute(
            """
            INSERT INTO hypotheses (
                hypothesis_id, run_id, claim, source_insight_id, initial_confidence,
                status, priority, evidence_chain, verdict, verdict_confidence,
                validation_step, tables_involved
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                hypothesis_id,
                run_id,
                str(payload.get("claim", "")),
                str(payload.get("source_insight_id", "")),
                float(payload.get("initial_confidence", 0.0) or 0.0),
                str(payload.get("status", "proposed")),
                float(payload.get("priority", 0.0) or 0.0),
                _json_or_none(payload.get("evidence_chain", [])),
                payload.get("verdict"),
                payload.get("verdict_confidence"),
                int(payload.get("validation_step", 0) or 0),
                _json_or_none(payload.get("tables_involved", [])),
            ],
        )

    def load_hypotheses(self, run_id: str) -> list[dict[str, Any]]:
        return self._query_dicts(
            """
            SELECT *
            FROM hypotheses
            WHERE run_id = ?
            ORDER BY created_at ASC
            """,
            [run_id],
        )

    def get_hypothesis(self, hypothesis_id: str) -> dict[str, Any] | None:
        rows = self._query_dicts(
            "SELECT * FROM hypotheses WHERE hypothesis_id = ? LIMIT 1",
            [hypothesis_id],
        )
        return rows[0] if rows else None

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

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._query_dicts("SELECT * FROM runs WHERE run_id = ? LIMIT 1", [run_id])
        return rows[0] if rows else None

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._query_dicts(
            """
            SELECT *
            FROM runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            [limit],
        )

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        steps_completed: int | None = None,
        insights_created: int | None = None,
        errors: int | None = None,
        frontier_size: int | None = None,
        notes: str | None = None,
        ended: bool = False,
    ) -> None:
        fields: list[str] = []
        params: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if steps_completed is not None:
            fields.append("steps_completed = ?")
            params.append(int(steps_completed))
        if insights_created is not None:
            fields.append("insights_created = ?")
            params.append(int(insights_created))
        if errors is not None:
            fields.append("errors = ?")
            params.append(int(errors))
        if frontier_size is not None:
            fields.append("frontier_size = ?")
            params.append(int(frontier_size))
        if notes is not None:
            fields.append("notes = ?")
            params.append(notes)
        if ended:
            fields.append("ended_at = now()")
        if not fields:
            return
        params.append(run_id)
        self.execute(f"UPDATE runs SET {', '.join(fields)} WHERE run_id = ?", params)

    def get_frontier_existing_keys(self) -> set[str]:
        rows = self.execute("SELECT dedupe_key FROM frontier").fetchall()
        return {str(row[0]) for row in rows if row and row[0]}

    def update_frontier_status(self, action_id: str, status: str, last_error: str | None = None) -> None:
        self.execute(
            """
            UPDATE frontier
            SET status = ?, last_error = ?
            WHERE action_id = ?
            """,
            [status, last_error, action_id],
        )

    def get_insight_by_id(self, insight_id: str) -> dict[str, Any] | None:
        rows = self._query_dicts("SELECT * FROM insights WHERE insight_id = ? LIMIT 1", [insight_id])
        return rows[0] if rows else None

    def get_edge_by_id(self, edge_id: str) -> dict[str, Any] | None:
        rows = self._query_dicts("SELECT * FROM edges WHERE edge_id = ? LIMIT 1", [edge_id])
        return rows[0] if rows else None

    def count_frontier(self, status: str | None = None) -> int:
        if status is None:
            return int(self.execute("SELECT COUNT(*) FROM frontier").fetchone()[0])
        return int(self.execute("SELECT COUNT(*) FROM frontier WHERE status = ?", [status]).fetchone()[0])

    def count_contradictions(self) -> int:
        return int(self.execute("SELECT COUNT(*) FROM edges WHERE type = 'contradicts'").fetchone()[0])

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

    def save_debrief(self, run_id: str, debrief_text: str) -> None:
        self.execute(
            "UPDATE runs SET debrief_text = ? WHERE run_id = ?",
            [debrief_text, run_id],
        )

    def load_debrief(self, run_id: str) -> str | None:
        row = self.execute(
            "SELECT debrief_text FROM runs WHERE run_id = ? LIMIT 1",
            [run_id],
        ).fetchone()
        if row and row[0]:
            return str(row[0])
        return None

    def _migrate(self) -> None:
        """Additive migrations for existing databases."""
        migrations = [
            "ALTER TABLE insights ADD COLUMN reasoning VARCHAR",
            """
            CREATE TABLE IF NOT EXISTS hypotheses (
                hypothesis_id      VARCHAR PRIMARY KEY,
                run_id             VARCHAR NOT NULL,
                claim              VARCHAR NOT NULL,
                source_insight_id  VARCHAR,
                initial_confidence DOUBLE,
                status             VARCHAR NOT NULL DEFAULT 'proposed',
                priority           DOUBLE NOT NULL DEFAULT 0.0,
                evidence_chain     VARCHAR,
                verdict            VARCHAR,
                verdict_confidence DOUBLE,
                validation_step    INTEGER NOT NULL DEFAULT 0,
                tables_involved    VARCHAR,
                created_at         TIMESTAMP NOT NULL DEFAULT now(),
                updated_at         TIMESTAMP NOT NULL DEFAULT now()
            )
            """,
            "ALTER TABLE runs ADD COLUMN debrief_text VARCHAR",
        ]
        for sql in migrations:
            try:
                self.execute(sql)
            except Exception:  # noqa: BLE001
                pass

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
    return json.dumps(value, default=_json_default)


def _json_default(value: Any) -> Any:
    """Serialize DuckDB/Python scalar types that json can't handle by default."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
