"""TUI data helpers for querying and mutating Nemo state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nemo.config import write_default_config
from nemo.graph import find_contradiction_clusters
from nemo.ingest.profile import profile_table
from nemo.report import generate_brief_markdown, write_brief_report
from nemo.store import NemoStore


def has_project(project_dir: Path) -> bool:
    """Return True when a Nemo project exists in the directory."""
    return (project_dir / "nemo.duckdb").exists()


def initialize_project(project_dir: Path) -> Path:
    """Initialize a Nemo project and return the database path."""
    root = project_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "nemo.duckdb"
    config_path = root / "nemo.toml"
    nemo_dir = root / ".nemo"
    (nemo_dir / "generators").mkdir(parents=True, exist_ok=True)
    (nemo_dir / "hooks").mkdir(parents=True, exist_ok=True)

    store = NemoStore(db_path)
    try:
        store.initialize()
    finally:
        store.close()

    if not config_path.exists():
        write_default_config(config_path)
    return db_path


def open_store(project_dir: Path) -> NemoStore:
    """Open the project store in project_dir."""
    db_path = project_dir / "nemo.duckdb"
    if not db_path.exists():
        raise RuntimeError(f"missing {db_path}; run `nemo init` first")
    return NemoStore(db_path)


def dashboard_status(store: NemoStore, project_dir: Path) -> dict[str, Any]:
    """Collect dashboard-level summary metrics."""
    datasets = list_datasets(store)
    latest = store.list_runs(limit=1)
    latest_run = latest[0] if latest else None
    db_path = project_dir / "nemo.duckdb"
    return {
        "project_path": str(project_dir),
        "db_path": str(db_path),
        "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "dataset_count": len(datasets),
        "dataset_rows_total": sum(int(row["rows"]) for row in datasets if row["rows"] >= 0),
        "frontier_queued": store.count_frontier(status="queued"),
        "contradictions": store.count_contradictions(),
        "learnings_count": int(store.execute("SELECT COUNT(*) FROM learnings").fetchone()[0]),
        "insights_count": int(store.execute("SELECT COUNT(*) FROM insights").fetchone()[0]),
        "latest_run": latest_run,
    }


def list_datasets(store: NemoStore) -> list[dict[str, Any]]:
    """Return dataset rows enriched with row/column counts."""
    rows: list[dict[str, Any]] = []
    for dataset in store.get_datasets():
        table_name = str(dataset["name"])
        row_count = -1
        col_count = -1
        if store.table_exists(table_name):
            safe_name = _quote_ident(table_name)
            row_count = int(store.execute(f"SELECT COUNT(*) FROM {safe_name}").fetchone()[0])
            col_count = int(store.execute(f"SELECT COUNT(*) FROM pragma_table_info({safe_name})").fetchone()[0])
        rows.append(
            {
                "dataset_id": str(dataset["dataset_id"]),
                "name": table_name,
                "rows": row_count,
                "cols": col_count,
                "format": str(dataset["format"]),
                "source_uri": str(dataset["source_uri"]),
                "created_at": str(dataset["created_at"]),
            }
        )
    return rows


def profile_dataset(store: NemoStore, table_name: str) -> dict[str, Any]:
    """Return serialized profile data for a table."""
    prof = profile_table(store, table_name)
    return {
        "table": prof.name,
        "row_count": prof.row_count,
        "columns": [
            {
                "name": col.name,
                "dtype": col.dtype,
                "null_pct": col.null_pct,
                "distinct_count": col.distinct_count,
                "min_val": col.min_val,
                "max_val": col.max_val,
                "p25": col.p25,
                "p50": col.p50,
                "p75": col.p75,
                "sample_values": col.sample_values,
            }
            for col in prof.columns
        ],
    }


def list_insights(
    store: NemoStore, *, search: str = "", sort: str = "confidence", limit: int = 200
) -> list[dict[str, Any]]:
    """Return insight rows for browse/search/sort experiences."""
    order_by = "confidence DESC, created_at DESC" if sort == "confidence" else "created_at DESC"
    params: list[Any] = []
    where = ""
    if search.strip():
        where = "WHERE lower(title) LIKE ? OR lower(claim) LIKE ? OR lower(question) LIKE ?"
        like = f"%{search.strip().lower()}%"
        params.extend([like, like, like])
    params.append(max(1, int(limit)))
    query = f"""
        SELECT insight_id, title, claim, confidence, source_tables_json, created_at, status
        FROM insights
        {where}
        ORDER BY {order_by}
        LIMIT ?
    """
    output: list[dict[str, Any]] = []
    for row in store.execute(query, params).fetchall():
        output.append(
            {
                "insight_id": str(row[0]),
                "title": str(row[1] or "Untitled"),
                "claim": str(row[2] or ""),
                "confidence": float(row[3] or 0.0),
                "tables": _json_list(row[4]),
                "created_at": str(row[5]),
                "status": str(row[6] or "ok"),
            }
        )
    return output


def get_insight_detail(store: NemoStore, insight_id: str) -> dict[str, Any] | None:
    """Fetch one insight with attached edges."""
    insight = store.get_insight_by_id(insight_id)
    if insight is None:
        return None
    return {
        **insight,
        "source_tables": _json_list(insight.get("source_tables_json")),
        "result_sample": _json_or_any(insight.get("result_sample_json")),
        "edges": store.get_edges_for_insight(insight_id),
    }


def rerun_insight_sql(store: NemoStore, insight_id: str) -> dict[str, Any]:
    """Re-execute an insight's SQL for reproducibility checks."""
    insight = store.get_insight_by_id(insight_id)
    if insight is None:
        raise RuntimeError(f"insight not found: {insight_id}")
    sql = str(insight.get("sql") or "").strip()
    if not sql:
        return {"ok": False, "error": "missing SQL", "row_count": 0, "preview": []}
    try:
        rows = store.execute(sql).fetchall()
        return {"ok": True, "error": "", "row_count": len(rows), "preview": rows[:5]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "row_count": 0, "preview": []}


def graph_stats(store: NemoStore) -> dict[str, Any]:
    """Return graph statistics for the graph tab."""
    insights = int(store.execute("SELECT COUNT(*) FROM insights").fetchone()[0])
    edges = int(store.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
    contradictions = int(store.execute("SELECT COUNT(*) FROM edges WHERE type = 'contradicts'").fetchone()[0])
    supports = int(store.execute("SELECT COUNT(*) FROM edges WHERE type = 'supports'").fetchone()[0])
    refines = int(store.execute("SELECT COUNT(*) FROM edges WHERE type = 'refines'").fetchone()[0])
    depends_on = int(store.execute("SELECT COUNT(*) FROM edges WHERE type = 'depends_on'").fetchone()[0])
    avg_conf = float(store.execute("SELECT AVG(confidence) FROM insights WHERE status = 'ok'").fetchone()[0] or 0.0)

    datasets = [str(row[0]) for row in store.execute("SELECT name FROM datasets").fetchall()]
    touched: set[str] = set()
    for row in store.execute("SELECT source_tables_json FROM insights WHERE source_tables_json IS NOT NULL").fetchall():
        touched.update(_json_list(row[0]))
    coverage_ratio = (len(touched) / len(datasets)) if datasets else 0.0
    hypothesis_rows = store.execute(
        """
        SELECT status, COUNT(*) AS cnt
        FROM hypotheses
        GROUP BY status
        ORDER BY status
        """
    ).fetchall()
    hypothesis_counts = {str(row[0]): int(row[1]) for row in hypothesis_rows}

    return {
        "insights": insights,
        "edges": edges,
        "supports": supports,
        "contradictions": contradictions,
        "refines": refines,
        "depends_on": depends_on,
        "avg_confidence": avg_conf,
        "coverage_touched": len(touched),
        "coverage_total": len(datasets),
        "coverage_ratio": coverage_ratio,
        "hypotheses_total": sum(hypothesis_counts.values()),
        "hypothesis_counts": hypothesis_counts,
    }


def list_edges(store: NemoStore, edge_type: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """Return edges, optionally filtered by type."""
    params: list[Any] = [max(1, int(limit))]
    query = """
        SELECT edge_id, from_insight_id, to_insight_id, type, weight, rationale, created_at
        FROM edges
        ORDER BY created_at DESC
        LIMIT ?
    """
    if edge_type:
        query = """
            SELECT edge_id, from_insight_id, to_insight_id, type, weight, rationale, created_at
            FROM edges
            WHERE type = ?
            ORDER BY created_at DESC
            LIMIT ?
        """
        params = [edge_type, max(1, int(limit))]
    output: list[dict[str, Any]] = []
    for row in store.execute(query, params).fetchall():
        output.append(
            {
                "edge_id": str(row[0]),
                "from": str(row[1]),
                "to": str(row[2]),
                "type": str(row[3]),
                "weight": float(row[4] or 0.0),
                "rationale": str(row[5] or ""),
                "created_at": str(row[6]),
            }
        )
    return output


def contradiction_clusters(store: NemoStore) -> list[dict[str, Any]]:
    """Return contradiction clusters."""
    return find_contradiction_clusters(store)


def brief_markdown(store: NemoStore, top_n: int = 10) -> str:
    """Generate markdown brief text."""
    return generate_brief_markdown(store, top_n=top_n)


def save_brief_markdown(store: NemoStore, output_path: Path, top_n: int = 10) -> Path:
    """Write brief markdown to the given file."""
    return write_brief_report(store, output_path, top_n=top_n)


def list_runs(store: NemoStore, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent runs."""
    return store.list_runs(limit=limit)


def _quote_ident(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


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


def _json_or_any(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    if not raw.strip():
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
