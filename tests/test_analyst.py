from __future__ import annotations

import json

from nemo.executor.analyst import (
    AnalystResult,
    _dispatch_tool,
    _extract_dataframe,
    _parse_analyst_result,
)
from nemo.executor.sandbox import PythonSession


def test_extract_dataframe_tool_returns_shape_and_dtypes(store) -> None:
    store.execute("CREATE TABLE demo (x DOUBLE, y DOUBLE, label VARCHAR)")
    store.execute("INSERT INTO demo VALUES (1.0, 2.0, 'A'), (3.0, 4.0, 'B'), (5.0, 6.0, 'A')")

    df, meta = _extract_dataframe("SELECT x, y, label FROM demo ORDER BY x", store, max_rows=10)

    assert list(df.columns) == ["x", "y", "label"]
    assert df.shape == (3, 3)
    assert meta["shape"] == [3, 3]
    assert "x" in meta["dtypes"]
    assert isinstance(meta["head"], list)
    assert len(meta["head"]) == 3


def test_extract_dataframe_row_cap_enforced(store) -> None:
    store.execute("CREATE TABLE big_demo AS SELECT i::INTEGER AS i FROM range(100000) tbl(i)")

    df, meta = _extract_dataframe("SELECT i FROM big_demo ORDER BY i", store, max_rows=50_000)

    assert df.shape[0] == 50_000
    assert meta["shape"][0] == 50_000
    assert meta["hit_row_cap"] is True


def test_extract_dataframe_without_limit_auto_appends_limit(store) -> None:
    store.execute("CREATE TABLE no_limit_demo AS SELECT i::INTEGER AS i FROM range(1000) tbl(i)")

    df, meta = _extract_dataframe("SELECT i FROM no_limit_demo ORDER BY i", store, max_rows=17)

    assert df.shape[0] == 17
    assert "limit 17" in meta["executed_sql"].lower()


def test_run_python_tool_executes_code_and_returns_structured_output(store) -> None:
    session = PythonSession()
    payload = _dispatch_tool(
        "run_python",
        {"code": "_result = {'mean': 2.5}"},
        store=store,
        profiles=[],
        session=session,
        all_sql=[],
        max_rows=100,
    )
    parsed = json.loads(payload)
    assert parsed["error"] is None
    assert parsed["result"]["mean"] == 2.5


def test_run_python_session_continuity_after_extraction(store) -> None:
    store.execute("CREATE TABLE continuity_demo (x DOUBLE, grp VARCHAR)")
    store.execute("INSERT INTO continuity_demo VALUES (1.0, 'A'), (2.0, 'A'), (5.0, 'B')")
    session = PythonSession()
    all_sql: list[str] = []

    extract_payload = _dispatch_tool(
        "extract_dataframe",
        {"sql": "SELECT x, grp FROM continuity_demo", "variable_name": "df"},
        store=store,
        profiles=[],
        session=session,
        all_sql=all_sql,
        max_rows=10,
    )
    extract_json = json.loads(extract_payload)
    assert "error" not in extract_json

    python_payload = _dispatch_tool(
        "run_python",
        {
            "code": (
                "grouped = df.groupby('grp')['x'].mean().to_dict()\n"
                "_result = {'a_mean': float(grouped['A']), 'b_mean': float(grouped['B'])}"
            )
        },
        store=store,
        profiles=[],
        session=session,
        all_sql=all_sql,
        max_rows=10,
    )
    python_json = json.loads(python_payload)
    assert python_json["error"] is None
    assert python_json["result"]["a_mean"] == 1.5
    assert python_json["result"]["b_mean"] == 5.0


def test_analyst_result_to_execution_result_adapter() -> None:
    analyst = AnalystResult(
        title="Difference is significant",
        claim="A differs from B",
        confidence=0.84,
        effect_size=0.62,
        statistical_tests=[
            {
                "test": "t-test",
                "statistic": 3.1,
                "p_value": 0.004,
                "effect_size": 0.62,
                "interpretation": "Strong evidence for a difference",
            }
        ],
        methodology="Two-sample t-test",
        tags=["comparison"],
        extraction_sql="SELECT x, grp FROM demo",
        python_code="_result = {...}",
        sample_size=120,
        data_quality_notes="No major issues.",
        all_sql=["SELECT x, grp FROM demo"],
    )

    execution = analyst.to_execution_result()
    assert execution.error is None
    assert execution.sql == "SELECT x, grp FROM demo"
    assert execution.row_count == 1
    assert execution.column_names == ["test", "statistic", "p_value", "effect_size", "interpretation"]
    assert execution.rows[0]["test"] == "t-test"


def test_parse_analyst_result_maps_fields() -> None:
    text = json.dumps({
        "title": "Treatment effect",
        "claim": "Coefficient is positive and significant.",
        "confidence": 0.91,
        "effect_size": 0.22,
        "statistical_tests": [
            {"test": "OLS", "statistic": 2.8, "p_value": 0.005, "effect_size": 0.22, "interpretation": "Significant"}
        ],
        "methodology": "OLS with controls",
        "tags": ["regression", "causal"],
        "extraction_sql": "SELECT y, treated, x1 FROM trial_data",
        "python_code": "import statsmodels.api as sm",
        "sample_size": 2034,
        "data_quality_notes": "Low missingness.",
    })

    parsed = _parse_analyst_result(text, ["SELECT y, treated, x1 FROM trial_data"], "SELECT 1")
    assert parsed is not None
    assert parsed.title == "Treatment effect"
    assert parsed.confidence == 0.91
    assert parsed.statistical_tests[0]["test"] == "OLS"
    assert parsed.sample_size == 2034
    assert parsed.extraction_sql == "SELECT y, treated, x1 FROM trial_data"


def test_dispatch_unknown_tool_returns_clean_error(store) -> None:
    session = PythonSession()
    payload = _dispatch_tool(
        "does_not_exist",
        {},
        store=store,
        profiles=[],
        session=session,
        all_sql=[],
        max_rows=10,
    )
    parsed = json.loads(payload)
    assert "error" in parsed
    assert "unknown tool" in parsed["error"]
