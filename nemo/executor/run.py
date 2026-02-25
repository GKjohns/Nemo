"""Safe query execution utilities."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from nemo.config import NemoConfig
from nemo.store import NemoStore


@dataclass
class ExecutionResult:
    sql: str
    rows: list[dict[str, Any]]
    row_count: int
    column_names: list[str]
    truncated: bool
    cost_ms: int
    error: str | None = None


def execute_query(store: NemoStore, sql: str, config: NemoConfig) -> ExecutionResult:
    """
    Execute one read-only query with timing and shape metadata.
    """
    try:
        _validate_select_only(sql)
    except ValueError as exc:
        return ExecutionResult(
            sql=sql,
            rows=[],
            row_count=0,
            column_names=[],
            truncated=False,
            cost_ms=0,
            error=str(exc),
        )

    _try_set_timeout(store, int(config.max_query_runtime_ms))
    limit = 200 if config.max_scan_rows is None else max(1, int(config.max_scan_rows))

    started = time.perf_counter()
    try:
        cursor = store.execute(sql)
        raw_rows = cursor.fetchmany(limit + 1)
        columns = [col[0] for col in cursor.description] if cursor.description else []
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.perf_counter() - started) * 1000)
        return ExecutionResult(
            sql=sql,
            rows=[],
            row_count=0,
            column_names=[],
            truncated=False,
            cost_ms=elapsed,
            error=str(exc),
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    truncated = len(raw_rows) > limit
    rows = raw_rows[:limit]
    as_dicts = [dict(zip(columns, row, strict=False)) for row in rows]
    return ExecutionResult(
        sql=sql,
        rows=as_dicts,
        row_count=len(as_dicts),
        column_names=columns,
        truncated=truncated,
        cost_ms=elapsed,
        error=None,
    )


def _validate_select_only(sql: str) -> None:
    cleaned = sql.strip()
    if not cleaned:
        raise ValueError("empty SQL")
    lowered = cleaned.lower()
    forbidden = ("insert ", "update ", "delete ", "drop ", "create ", "alter ", "truncate ", "attach ", "copy ")
    if any(token in lowered for token in forbidden):
        raise ValueError("only SELECT queries are allowed in safe mode")

    statements = [part.strip() for part in cleaned.split(";") if part.strip()]
    if len(statements) != 1:
        raise ValueError("query must contain exactly one statement")
    single = _strip_leading_comments(statements[0]).lstrip()
    if not (single.lower().startswith("select") or single.lower().startswith("with")):
        raise ValueError("query must start with SELECT or WITH")


def _strip_leading_comments(statement: str) -> str:
    lines = statement.splitlines()
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("--"):
            idx += 1
            continue
        break
    return "\n".join(lines[idx:])


def _try_set_timeout(store: NemoStore, timeout_ms: int) -> None:
    if timeout_ms <= 0:
        return
    try:
        store.execute(f"PRAGMA statement_timeout={int(timeout_ms)}")
    except Exception:  # noqa: BLE001
        # DuckDB versions vary in timeout pragmas; validation still happens in caller.
        return
