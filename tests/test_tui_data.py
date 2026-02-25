from __future__ import annotations

from pathlib import Path

from nemo.tui import data


def test_initialize_and_dashboard_status(project_dir: Path):
    data.initialize_project(project_dir)
    assert data.has_project(project_dir)

    store = data.open_store(project_dir)
    try:
        store.insert_dataset(name="orders", source_uri="memory://orders", fmt="csv")
        store.execute("CREATE TABLE orders AS SELECT 1 AS id UNION ALL SELECT 2 AS id")
        store.insert_run(config_json={"max_steps": 3}, status="completed")
        status = data.dashboard_status(store, project_dir)
    finally:
        store.close()

    assert status["dataset_count"] == 1
    assert status["dataset_rows_total"] == 2
    assert status["insights_count"] == 0


def test_insights_search_sort_and_rerun_sql(store):
    low_id = store.insert_insight(
        title="Low confidence",
        question="Q1",
        sql="SELECT 1 AS x",
        result_summary_json={"ok": True},
        claim="baseline",
        confidence=0.2,
        source_tables_json=["orders"],
    )
    high_id = store.insert_insight(
        title="High confidence",
        question="Q2",
        sql="SELECT 2 AS x",
        result_summary_json={"ok": True},
        claim="important signal",
        confidence=0.9,
        source_tables_json=["orders"],
    )

    ranked = data.list_insights(store, sort="confidence")
    assert ranked[0]["insight_id"] == high_id
    filtered = data.list_insights(store, search="important")
    assert len(filtered) == 1
    assert filtered[0]["insight_id"] == high_id

    detail = data.get_insight_detail(store, high_id)
    assert detail is not None
    assert detail["insight_id"] == high_id

    repro = data.rerun_insight_sql(store, low_id)
    assert repro["ok"] is True
    assert repro["row_count"] == 1


def test_graph_helpers_and_brief_write(store, tmp_path: Path):
    left = store.insert_insight(
        title="A",
        question="Q1",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="A",
        confidence=0.8,
        source_tables_json=["orders"],
    )
    right = store.insert_insight(
        title="B",
        question="Q2",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="B",
        confidence=0.7,
        source_tables_json=["orders"],
    )
    store.insert_edge(from_insight_id=left, to_insight_id=right, edge_type="contradicts", weight=0.9)

    stats = data.graph_stats(store)
    assert stats["insights"] == 2
    assert stats["contradictions"] == 1

    edges = data.list_edges(store, edge_type="contradicts")
    assert len(edges) == 1
    assert edges[0]["type"] == "contradicts"

    clusters = data.contradiction_clusters(store)
    assert len(clusters) == 1

    output = tmp_path / "brief.md"
    written = data.save_brief_markdown(store, output, top_n=5)
    assert written == output
    assert output.exists()
