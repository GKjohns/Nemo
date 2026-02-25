from nemo.store.db import SYSTEM_TABLES


def test_store_initializes_all_system_tables(store):
    for table_name in SYSTEM_TABLES:
        assert store.table_exists(table_name), f"expected table {table_name} to exist"


def test_store_insert_and_query_all_system_tables(store):
    run_id = store.insert_run({"mode": "test"})
    dataset_id = store.insert_dataset(
        name="orders",
        source_uri="dummy.csv",
        fmt="csv",
        schema_json={"columns": ["id", "amount"]},
    )
    insight_id_a = store.insert_insight(
        title="Revenue grows",
        question="Did revenue increase?",
        sql="select 1",
        result_summary_json={"value": 1},
        claim="Revenue increased.",
        run_id=run_id,
    )
    insight_id_b = store.insert_insight(
        title="Revenue drops",
        question="Did revenue decrease?",
        sql="select 2",
        result_summary_json={"value": 2},
        claim="Revenue decreased.",
        run_id=run_id,
    )
    edge_id = store.insert_edge(
        from_insight_id=insight_id_a,
        to_insight_id=insight_id_b,
        edge_type="contradicts",
    )
    action_id = store.insert_frontier_item(
        action_type="METRIC_TREND_SCAN",
        payload_json={"metric": "revenue"},
        dedupe_key="metric:revenue",
        run_id=run_id,
        score=0.42,
    )
    thread_id = store.insert_thread_card(
        thread_id="thread_001",
        title="Revenue thread",
        key_insight_ids_json=[insight_id_a, insight_id_b],
    )
    learning_id = store.insert_learning(
        run_id=run_id,
        category="useful_metric",
        subject="orders.revenue",
        detail="Revenue trend scan produced consistent signal.",
        confidence=0.8,
    )

    assert dataset_id.startswith("dataset_")
    assert edge_id.startswith("edge_")
    assert action_id.startswith("action_")
    assert run_id.startswith("run_")
    assert thread_id == "thread_001"
    assert learning_id.startswith("learning_")

    datasets = store.get_datasets()
    frontier = store.get_frontier_queue()
    insights = store.get_recent_insights()
    edges = store.get_edges_for_insight(insight_id_a)

    assert len(datasets) == 1
    assert len(frontier) == 1
    assert len(insights) == 2
    assert len(edges) == 1

    learning_count = store.execute("SELECT COUNT(*) FROM learnings").fetchone()[0]
    thread_count = store.execute("SELECT COUNT(*) FROM thread_cards").fetchone()[0]
    assert learning_count == 1
    assert thread_count == 1
