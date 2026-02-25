from __future__ import annotations

import json

from nemo.config import NemoConfig
from nemo.graph import find_contradiction_clusters, link_insight


def test_linker_creates_expected_edge_types(store):
    prior_id = store.insert_insight(
        title="prior",
        question="q1",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="Revenue is higher in europe",
        claim_struct_json={"metric": "revenue", "direction": "higher", "population": "europe"},
        source_tables_json=["orders"],
    )
    new_id = store.insert_insight(
        title="new",
        question="q2",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="Revenue is lower in europe",
        claim_struct_json={"metric": "revenue", "direction": "lower", "population": "europe"},
        source_tables_json=["orders"],
    )
    new_insight = store.get_insight_by_id(new_id)
    assert new_insight is not None
    edges = link_insight(store, new_insight, NemoConfig())
    assert edges
    assert any(edge["to_insight_id"] == prior_id and edge["type"] == "contradicts" for edge in edges)


def test_contradiction_clusters_group_connected_components(store):
    i1 = store.insert_insight(
        title="a",
        question="q",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="claim a",
        claim_struct_json={"metric": "m", "direction": "higher", "population": "all"},
    )
    i2 = store.insert_insight(
        title="b",
        question="q",
        sql="SELECT 1",
        result_summary_json=json.dumps({"ok": True}),
        claim="claim b",
        claim_struct_json={"metric": "m", "direction": "lower", "population": "all"},
    )
    i3 = store.insert_insight(
        title="c",
        question="q",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="claim c",
        claim_struct_json={"metric": "m", "direction": "higher", "population": "all"},
    )
    store.insert_edge(from_insight_id=i1, to_insight_id=i2, edge_type="contradicts")
    store.insert_edge(from_insight_id=i2, to_insight_id=i3, edge_type="contradicts")

    clusters = find_contradiction_clusters(store)
    assert clusters
    largest = clusters[0]
    assert set(largest["insight_ids"]) == {i1, i2, i3}
