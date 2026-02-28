from __future__ import annotations

import asyncio

from nemo.config import NemoConfig
from nemo.engine import NemoEngine
from nemo.executor import compile_action, execute_query
from nemo.executor.run import ExecutionResult
from nemo.events import EventBus
from nemo.ingest.profile import profile_table
from nemo.planner.models import FrontierItem
from nemo.planner.strategist import Hypothesis, Notebook


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


def test_engine_routes_sql_hypothesis_to_execute_query(store, monkeypatch):
    engine = NemoEngine(store, NemoConfig(), EventBus())
    hypothesis = Hypothesis(
        question="Baseline row count",
        reasoning="Simple SQL validation path.",
        sql='SELECT 1 AS value',
        table="demo",
        analysis_type="sql",
    )

    called: dict[str, bool] = {"sql": False}

    def _fake_execute_query(_store, sql, _config):
        called["sql"] = True
        return ExecutionResult(
            sql=sql,
            rows=[{"value": 1}],
            row_count=1,
            column_names=["value"],
            truncated=False,
            cost_ms=1,
            error=None,
        )

    monkeypatch.setattr("nemo.engine.execute_query", _fake_execute_query)
    result_hypothesis, result, errors, should_skip = asyncio.run(
        engine._execute_strategist_step(
            run_id="run_test",
            step_num=1,
            hypothesis=hypothesis,
            notebook=Notebook(),
            schema_ctx="",
            profiles=[],
        )
    )

    assert called["sql"] is True
    assert result_hypothesis.analysis_type == "sql"
    assert result.error is None
    assert errors == 0
    assert should_skip is False


def test_engine_routes_statistical_hypothesis_to_analyst(store, monkeypatch):
    engine = NemoEngine(store, NemoConfig(), EventBus())
    hypothesis = Hypothesis(
        question="Is segment A higher than B?",
        reasoning="Requires statistical inference.",
        sql='SELECT "segment", "amount" FROM "orders" LIMIT 200',
        table="orders",
        analysis_type="statistical",
    )

    called: dict[str, bool] = {"analyst": False}

    async def _fake_execute_statistical_analysis(*, run_id, step_num, hypothesis, profiles):
        assert run_id == "run_test"
        assert step_num == 1
        assert hypothesis.analysis_type == "statistical"
        assert isinstance(profiles, list)
        called["analyst"] = True
        return (
            ExecutionResult(
                sql='SELECT "segment", "amount" FROM "orders" LIMIT 200',
                rows=[{"test": "ttest_ind", "p_value": 0.03}],
                row_count=1,
                column_names=["test", "p_value"],
                truncated=False,
                cost_ms=2,
                error=None,
            ),
            None,
        )

    def _should_not_run_sql(*_args, **_kwargs):
        raise AssertionError("SQL execution path should not run for statistical hypotheses")

    monkeypatch.setattr(engine, "_execute_statistical_analysis", _fake_execute_statistical_analysis)
    monkeypatch.setattr("nemo.engine.execute_query", _should_not_run_sql)

    result_hypothesis, result, errors, should_skip = asyncio.run(
        engine._execute_strategist_step(
            run_id="run_test",
            step_num=1,
            hypothesis=hypothesis,
            notebook=Notebook(),
            schema_ctx="",
            profiles=[],
        )
    )

    assert called["analyst"] is True
    assert result_hypothesis.analysis_type == "statistical"
    assert result.error is None
    assert errors == 0
    assert should_skip is False


def test_engine_falls_back_to_sql_when_statistical_analysis_fails(store, monkeypatch):
    engine = NemoEngine(store, NemoConfig(), EventBus())
    hypothesis = Hypothesis(
        question="Is segment A higher than B?",
        reasoning="Requires statistical inference.",
        sql="SELECT 1 AS value",
        table="orders",
        analysis_type="statistical",
    )

    async def _fake_execute_statistical_analysis(*, run_id, step_num, hypothesis, profiles):
        _ = (run_id, step_num, hypothesis, profiles)
        return None, "synthetic analyst failure"

    def _fake_execute_query(_store, sql, _config):
        return ExecutionResult(
            sql=sql,
            rows=[{"value": 1}],
            row_count=1,
            column_names=["value"],
            truncated=False,
            cost_ms=3,
            error=None,
        )

    monkeypatch.setattr(engine, "_execute_statistical_analysis", _fake_execute_statistical_analysis)
    monkeypatch.setattr("nemo.engine.execute_query", _fake_execute_query)

    result_hypothesis, result, errors, should_skip = asyncio.run(
        engine._execute_strategist_step(
            run_id="run_test",
            step_num=1,
            hypothesis=hypothesis,
            notebook=Notebook(),
            schema_ctx="",
            profiles=[],
        )
    )

    assert result_hypothesis.analysis_type == "statistical"
    assert result.error is None
    assert result.row_count == 1
    assert errors == 0
    assert should_skip is False
