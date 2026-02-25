from __future__ import annotations

from nemo.config import NemoConfig
from nemo.executor import compile_action, execute_query
from nemo.ingest.profile import profile_table
from nemo.planner.models import FrontierItem


def test_compile_action_for_all_sprint4_types(store):
    store.execute("CREATE TABLE demo (dt DATE, segment VARCHAR, x DOUBLE, y DOUBLE)")
    store.execute(
        "INSERT INTO demo VALUES "
        "('2024-01-01','a',1.0,2.0),"
        "('2024-01-02','b',2.0,3.5),"
        "('2024-01-03','a',4.0,5.0)"
    )
    profiles = [profile_table(store, "demo")]
    base_payload = {
        "table": "demo",
        "time_col": "dt",
        "metric_col": "x",
        "dimension_col": "segment",
        "group_col": "segment",
        "columns": ["x", "y"],
        "thread_id": "thread_1",
        "prior_sql": "SELECT * FROM demo",
    }
    action_types = [
        "SCHEMA_PROFILE",
        "METRIC_TREND_SCAN",
        "CHANGEPOINT_DETECT",
        "SEGMENT_COMPARE",
        "TOP_GROUPS",
        "OUTLIER_GROUPS",
        "CORRELATION_SCAN",
        "DATA_QUALITY_CHECK",
        "COVERAGE_EXPLORER",
        "ROBUSTNESS_CHECK",
        "CONTRADICTION_RESOLVE",
    ]
    for action_type in action_types:
        item = FrontierItem(action_type=action_type, payload=base_payload, dedupe_key=f"{action_type}:demo")
        sql = compile_action(item, profiles=profiles, join_candidates=[])
        assert sql.lower().startswith("-- action_id:")
        assert "select" in sql.lower()
        assert "limit " in sql.lower()


def test_execute_query_enforces_select_only(store):
    config = NemoConfig(max_scan_rows=5, max_query_runtime_ms=1000)
    bad = execute_query(store, "DELETE FROM datasets", config)
    assert bad.error is not None
    assert "select" in bad.error.lower()


def test_execute_query_returns_rows_and_metadata(store):
    store.execute("CREATE TABLE demo_exec (id INTEGER, value DOUBLE)")
    store.execute("INSERT INTO demo_exec VALUES (1, 1.5), (2, 3.0)")
    config = NemoConfig(max_scan_rows=10, max_query_runtime_ms=1000)
    result = execute_query(store, "SELECT id, value FROM demo_exec ORDER BY id", config)
    assert result.error is None
    assert result.row_count == 2
    assert result.column_names == ["id", "value"]
    assert result.rows[0]["id"] == 1
