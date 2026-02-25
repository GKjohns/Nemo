from __future__ import annotations

from nemo.config import NemoConfig
from nemo.graph import record_learnings, recall_learnings, update_thread_cards


def test_record_and_recall_learnings(store):
    run_id = store.insert_run(config_json={"max_steps": 5}, status="completed")
    store.insert_frontier_item(
        action_type="slice_dimension",
        payload_json={"table": "orders", "metric_col": "revenue"},
        dedupe_key="slice:orders:revenue",
        run_id=run_id,
        status="done",
    )
    store.insert_frontier_item(
        action_type="slice_dimension",
        payload_json={"table": "orders", "metric_col": "revenue"},
        dedupe_key="slice:orders:revenue:error",
        run_id=run_id,
        status="error",
        last_error="timeout",
    )
    store.insert_insight(
        title="Revenue pattern",
        question="Q",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="Revenue is higher in europe",
        confidence=0.85,
        run_id=run_id,
        claim_struct_json={"metric": "revenue", "direction": "higher", "population": "europe"},
        source_tables_json=["orders"],
    )

    learning_ids = record_learnings(store, run_id)
    assert learning_ids

    recalled = recall_learnings(store, {"tables": ["orders"], "columns": ["revenue"], "action_types": ["slice_dimension"]})
    assert recalled
    assert any(item["category"] in {"generator_hit_rate", "useful_metric", "error_pattern"} for item in recalled)


def test_update_thread_cards_creates_contradiction_threads(store):
    run_id = store.insert_run(config_json={"max_steps": 5}, status="completed")
    i1 = store.insert_insight(
        title="a",
        question="q",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="Revenue is higher in europe",
        run_id=run_id,
        confidence=0.7,
        claim_struct_json={"metric": "revenue", "direction": "higher", "population": "europe"},
    )
    i2 = store.insert_insight(
        title="b",
        question="q",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="Revenue is lower in europe",
        run_id=run_id,
        confidence=0.7,
        claim_struct_json={"metric": "revenue", "direction": "lower", "population": "europe"},
    )
    store.insert_edge(from_insight_id=i1, to_insight_id=i2, edge_type="contradicts")

    config = NemoConfig(key_metrics={"revenue": "revenue"})
    updated = update_thread_cards(store, config)
    assert updated
    count = store.execute("SELECT COUNT(*) FROM thread_cards").fetchone()[0]
    assert int(count) >= 1
