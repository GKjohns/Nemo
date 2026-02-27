from __future__ import annotations

from pathlib import Path

from nemo.report import generate_brief_markdown, write_brief_report


def test_generate_brief_contains_required_sections(store):
    run_id = store.insert_run(config_json={"max_steps": 5}, status="completed")
    store.insert_dataset(name="orders", source_uri="memory://orders", fmt="tpch")
    left = store.insert_insight(
        title="Revenue up",
        question="Q1",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="Revenue is higher in europe",
        confidence=0.8,
        run_id=run_id,
        claim_struct_json={"metric": "revenue", "direction": "higher", "population": "europe"},
        source_tables_json=["orders"],
    )
    right = store.insert_insight(
        title="Revenue down",
        question="Q2",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="Revenue is lower in europe",
        confidence=0.7,
        run_id=run_id,
        claim_struct_json={"metric": "revenue", "direction": "lower", "population": "europe"},
        source_tables_json=["orders"],
    )
    store.insert_edge(from_insight_id=left, to_insight_id=right, edge_type="contradicts")
    store.save_hypothesis(
        run_id,
        {
            "hypothesis_id": "hyp_1",
            "claim": "European revenue trend has reversed in recent months.",
            "source_insight_id": left,
            "initial_confidence": 0.72,
            "status": "validated",
            "priority": 0.8,
            "evidence_chain": [{"insight_id": left, "relationship": "supports", "note": "strong trend"}],
            "verdict": "Validated after segmentation and confound checks.",
            "verdict_confidence": 0.84,
            "validation_step": 3,
            "tables_involved": ["orders"],
        },
    )

    markdown = generate_brief_markdown(store, top_n=5)
    assert "## Top Insights" in markdown
    assert "## Hypothesis Verdicts" in markdown
    assert "## Contradictions" in markdown
    assert "## Coverage" in markdown
    assert "## Recommendations" in markdown


def test_write_brief_report_writes_markdown_file(store, tmp_path: Path):
    store.insert_run(config_json={"max_steps": 5}, status="completed")
    store.insert_insight(
        title="Simple insight",
        question="Q",
        sql="SELECT 1",
        result_summary_json={"ok": True},
        claim="Something happened",
        confidence=0.6,
        source_tables_json=["orders"],
    )
    output = tmp_path / "reports" / "brief.md"
    written = write_brief_report(store, output, top_n=3)
    assert written == output
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    assert content.startswith("# Nemo Brief")
