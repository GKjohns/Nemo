from __future__ import annotations

import json

from nemo.config import NemoConfig
from nemo.graph import find_contradiction_clusters, link_insight
from nemo.graph.link import classify_edge_batch


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


def test_classify_edge_batch_returns_llm_edges():
    class _FakeResponses:
        def __init__(self) -> None:
            self.calls = 0

        def parse(self, **kwargs):
            self.calls += 1
            return type(
                "Resp",
                (),
                {
                    "output_parsed": type(
                        "Parsed",
                        (),
                        {
                            "edges": [
                                type(
                                    "Edge",
                                    (),
                                    {
                                        "to_insight_id": "prior_1",
                                        "relationship": "supports",
                                        "confidence": 0.9,
                                        "rationale": "Both claims describe stronger revenue in the same segment.",
                                    },
                                )(),
                                type(
                                    "Edge",
                                    (),
                                    {
                                        "to_insight_id": "prior_2",
                                        "relationship": "none",
                                        "confidence": 0.2,
                                        "rationale": "",
                                    },
                                )(),
                            ]
                        },
                    )(),
                },
            )()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    new_insight = {
        "insight_id": "new_1",
        "claim": "Revenue is higher in enterprise segment.",
        "claim_struct_json": {"metric": "revenue", "direction": "higher", "population": "enterprise"},
        "source_tables_json": ["orders"],
    }
    candidates = [
        {"insight_id": "prior_1", "claim": "Enterprise revenue increased.", "source_tables_json": ["orders"]},
        {"insight_id": "prior_2", "claim": "Returns are flat.", "source_tables_json": ["returns"]},
    ]
    edges = classify_edge_batch(new_insight, candidates, _FakeClient())  # type: ignore[arg-type]
    assert len(edges) == 1
    assert edges[0]["to_insight_id"] == "prior_1"
    assert edges[0]["type"] == "supports"
    assert edges[0]["weight"] > 0.8


def test_linker_falls_back_to_heuristic_when_llm_fails(store):
    class _FailingResponses:
        def parse(self, **kwargs):
            raise RuntimeError("boom")

    class _FailingClient:
        def __init__(self) -> None:
            self.responses = _FailingResponses()

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
    edges = link_insight(store, new_insight, NemoConfig(), _FailingClient())  # type: ignore[arg-type]
    assert any(edge["to_insight_id"] == prior_id and edge["type"] == "contradicts" for edge in edges)


def test_classify_edge_batch_batches_candidates():
    class _FakeResponses:
        def __init__(self) -> None:
            self.calls = 0

        def parse(self, **kwargs):
            self.calls += 1
            payload = json.loads(kwargs.get("input", "{}"))
            edges = [
                type(
                    "Edge",
                    (),
                    {
                        "to_insight_id": str(candidate.get("insight_id")),
                        "relationship": "supports",
                        "confidence": 0.7,
                        "rationale": "Related metric direction and population.",
                    },
                )()
                for candidate in payload.get("candidates", [])
            ]
            return type(
                "Resp",
                (),
                {
                    "output_parsed": type("Parsed", (), {"edges": edges})(),
                },
            )()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    client = _FakeClient()
    new_insight = {"insight_id": "new_1", "claim": "x", "source_tables_json": ["orders"]}
    candidates = [{"insight_id": f"prior_{idx}", "claim": "x"} for idx in range(45)]
    edges = classify_edge_batch(new_insight, candidates, client, batch_size=20)  # type: ignore[arg-type]
    assert len(edges) == 45
    assert client.responses.calls == 3


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
