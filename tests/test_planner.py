from __future__ import annotations

from pathlib import Path

from nemo.config import NemoConfig
from nemo.engine import _build_coverage_context, _is_duplicate_claim, _is_duplicate_question
from nemo.ingest.profile import ColumnProfile, TableProfile
from nemo.planner import (
    GeneratorContext,
    dedupe_frontier,
    derive_recent_insight_keys,
    get_all_generators,
    is_saturated,
    run_generators,
    score_frontier,
    select_next,
)
from nemo.planner.strategist import (
    InterpretationResult,
    Notebook,
    NotebookEntry,
    apply_notebook_update,
    build_schema_context,
    format_notebook,
)


def test_generators_and_loader_cover_sprint3_scope(store):
    """Legacy generators still work as fallback."""
    ctx = _make_context(store)
    items = run_generators(ctx)

    assert items
    generated_types = {item.action_type for item in items}
    assert generated_types >= {
        "SCHEMA_PROFILE",
        "METRIC_TREND_SCAN",
        "CHANGEPOINT_DETECT",
        "SEGMENT_COMPARE",
        "TOP_GROUPS",
        "OUTLIER_GROUPS",
        "DATA_QUALITY_CHECK",
        "COVERAGE_EXPLORER",
        "ROBUSTNESS_CHECK",
        "CONTRADICTION_RESOLVE",
    }
    if any(item.action_type == "CORRELATION_SCAN" for item in items):
        assert "CORRELATION_SCAN" in generated_types
    assert all(item.dedupe_key for item in items)
    assert len(get_all_generators(Path("/tmp/path-that-does-not-exist"))) >= 11


def test_dedupe_and_scoring_are_deterministic(store):
    ctx = _make_context(store)
    generated = run_generators(ctx)

    recent_keys = derive_recent_insight_keys(ctx.recent_insights)
    existing_keys = {generated[0].dedupe_key}
    deduped = dedupe_frontier(generated, existing_keys=existing_keys, recent_insight_keys=recent_keys)

    assert len(deduped) < len(generated)
    assert len({item.dedupe_key for item in deduped}) == len(deduped)

    first = score_frontier(deduped, ctx)
    second = score_frontier(deduped, ctx)
    assert [item.score for item in first] == [item.score for item in second]
    assert all(first[idx].score >= first[idx + 1].score for idx in range(len(first) - 1))


def test_scheduler_respects_budget_and_saturation(store):
    config = NemoConfig(max_actions_per_thread=1, max_query_runtime_ms=1_000, saturation_threshold=0.5)

    store.insert_frontier_item(
        action_type="SEGMENT_COMPARE",
        payload_json={"table": "orders", "estimated_runtime_ms": 100},
        dedupe_key="done-thread-a",
        status="done",
        thread_id="thread_a",
        score=0.9,
    )
    store.insert_frontier_item(
        action_type="METRIC_TREND_SCAN",
        payload_json={"table": "orders", "estimated_runtime_ms": 100},
        dedupe_key="queued-thread-a",
        status="queued",
        thread_id="thread_a",
        score=0.99,
    )
    store.insert_frontier_item(
        action_type="DATA_QUALITY_CHECK",
        payload_json={"table": "orders", "estimated_runtime_ms": 2_000},
        dedupe_key="too-slow",
        status="queued",
        score=0.98,
    )
    store.insert_frontier_item(
        action_type="SCHEMA_PROFILE",
        payload_json={"table": "orders", "estimated_runtime_ms": 300},
        dedupe_key="eligible",
        status="queued",
        score=0.75,
    )

    selected = select_next(store, config)
    assert selected is not None
    assert selected.dedupe_key == "eligible"
    assert is_saturated(store, config) is False

    store.execute("UPDATE frontier SET score = 0.1 WHERE status = 'queued'")
    assert is_saturated(store, config) is True


def test_full_pipeline_selects_action(store):
    ctx = _make_context(store)
    generated = run_generators(ctx)
    deduped = dedupe_frontier(
        generated,
        existing_keys=set(),
        recent_insight_keys=derive_recent_insight_keys(ctx.recent_insights),
    )
    scored = score_frontier(deduped, ctx)

    for item in scored[:20]:
        store.insert_frontier_item(
            action_type=item.action_type,
            payload_json=item.payload,
            dedupe_key=item.dedupe_key,
            score=item.score,
            status=item.status,
            thread_id=item.thread_id,
            depends_on_action_id=item.depends_on_action_id,
            last_error=item.last_error,
        )

    selected = select_next(store, ctx.config)
    assert selected is not None
    assert selected.score >= ctx.config.saturation_threshold


def _make_context(store):
    config = NemoConfig(
        time_columns=["orders.order_date"],
        key_metrics={"amount": "sum(amount)"},
        saturation_threshold=0.2,
        max_query_runtime_ms=2_000,
        max_actions_per_thread=2,
    )
    profiles = [_orders_profile(), _customers_profile()]

    store.insert_insight(
        title="METRIC_TREND_SCAN: orders",
        question="orders.amount over time",
        sql="SELECT 1",
        result_summary_json={"dedupe_key": "metric_trend:orders.order_date:amount"},
        claim="Orders amount trends upward.",
        thread_id="thread_rev",
    )
    store.insert_insight(
        title="SEGMENT_COMPARE: orders",
        question="orders.amount by segment",
        sql="SELECT 1",
        result_summary_json={"rows": 3},
        claim="Enterprise leads average amount.",
        thread_id="thread_rev",
    )
    recent_insights = store.get_recent_insights(limit=20)
    return GeneratorContext(
        store=store,
        profiles=profiles,
        recent_insights=recent_insights,
        join_candidates=[],
        config=config,
    )


def _orders_profile() -> TableProfile:
    return TableProfile(
        name="orders",
        row_count=1000,
        columns=[
            _col("order_id", "INTEGER", distinct_count=1000, cardinality_ratio=1.0),
            _col(
                "order_date",
                "DATE",
                distinct_count=180,
                cardinality_ratio=0.18,
                min_val="2024-01-01",
                max_val="2024-06-30",
            ),
            _col("amount", "DOUBLE", distinct_count=700, cardinality_ratio=0.7, stddev=35.0),
            _col("segment", "VARCHAR", distinct_count=4, cardinality_ratio=0.004),
            _col("customer_id", "INTEGER", distinct_count=350, cardinality_ratio=0.35),
        ],
    )


def _customers_profile() -> TableProfile:
    return TableProfile(
        name="customers",
        row_count=350,
        columns=[
            _col("customer_id", "INTEGER", distinct_count=350, cardinality_ratio=1.0),
            _col("region", "VARCHAR", distinct_count=6, cardinality_ratio=0.017),
            _col("signup_date", "DATE", distinct_count=200, cardinality_ratio=0.57),
            _col("lifetime_value", "DOUBLE", distinct_count=330, cardinality_ratio=0.94, stddev=120.0),
        ],
    )


def test_notebook_applies_updates():
    """Notebook correctly extends existing themes and creates new ones."""
    notebook = Notebook(entries=[
        NotebookEntry(
            theme="Pricing",
            summary="Initial pricing analysis.",
            key_findings=["Brand#51 is 12% above average."],
            open_questions=["Is this driven by product mix?"],
            tables_touched=["part"],
            step_count=1,
        ),
    ], total_steps=1)

    interpretation = InterpretationResult(
        title="Product mix partially explains Brand#51 premium",
        claim="Brand#51 over-indexes on LARGE PLATED TIN (18% vs 6% population).",
        confidence=0.75,
        tags=["pricing", "part"],
        reasoning="Testing whether Brand#51's premium is a mix effect.",
        theme="Pricing",
        summary_update="Brand#51 has 12% higher avg retail, partially explained by product type mix.",
        new_finding="Brand#51 over-indexes on LARGE PLATED TIN (18% vs 6%).",
        new_open_questions=["What is the residual premium after controlling for type?"],
        resolved_questions=["Is this driven by product mix?"],
    )

    updated = apply_notebook_update(notebook, interpretation)
    assert updated.total_steps == 2
    assert len(updated.entries) == 1
    entry = updated.entries[0]
    assert entry.theme == "Pricing"
    assert len(entry.key_findings) == 2
    assert "Is this driven by product mix?" not in entry.open_questions
    assert "What is the residual premium after controlling for type?" in entry.open_questions
    assert entry.step_count == 2


def test_notebook_creates_new_theme():
    """Notebook creates a new theme when the interpretation targets a new one."""
    notebook = Notebook(entries=[
        NotebookEntry(theme="Pricing", summary="Price analysis.", key_findings=[], open_questions=[],
                      tables_touched=["part"], step_count=1),
    ], total_steps=1)

    interpretation = InterpretationResult(
        title="Supply chain concentration",
        claim="Each part has 8 suppliers on average.",
        confidence=0.8,
        tags=["supply_chain", "partsupp"],
        reasoning="Switching to supply chain analysis.",
        theme="Supply Chain",
        summary_update="Parts are broadly multi-sourced with 8 suppliers each on average.",
        new_finding="Average 8 suppliers per part, 160 parts per supplier.",
        new_open_questions=["Is supply cost correlated with supplier count?"],
        resolved_questions=[],
    )

    updated = apply_notebook_update(notebook, interpretation)
    assert updated.total_steps == 2
    assert len(updated.entries) == 2
    assert updated.entries[1].theme == "Supply Chain"
    assert updated.entries[1].step_count == 1


def test_build_schema_context_is_compact():
    """Schema context builder produces compact output from profiles."""
    profiles = [_orders_profile(), _customers_profile()]
    ctx = build_schema_context(profiles)
    assert "orders" in ctx
    assert "customers" in ctx
    assert "1,000 rows" in ctx
    assert "amount" in ctx
    assert "range=2024-01-01..2024-06-30" in ctx


def test_build_coverage_context_reports_unexplored_tables():
    notebook = Notebook(entries=[
        NotebookEntry(
            theme="Revenue",
            summary="Started with orders.",
            key_findings=[],
            open_questions=[],
            tables_touched=["orders"],
            step_count=5,
        ),
    ], total_steps=5)
    text = _build_coverage_context(notebook, ["orders", "customers", "lineitem"], max_steps_per_theme=4)
    assert "Tables explored: orders" in text
    assert "Tables not yet explored: customers, lineitem" in text
    assert "Preferred max depth per theme before pivot: 4" in text


def test_duplicate_detection_helpers():
    recent_questions = [
        "How does monthly revenue evolve over time by order date?",
        "Are revenue swings driven by volume or unit price changes?",
    ]
    assert _is_duplicate_question(
        "Are monthly revenue swings mostly due to volume or unit price?",
        recent_questions,
        threshold=0.5,
    )
    assert not _is_duplicate_question(
        "Which customer segments contribute most gross margin?",
        recent_questions,
        threshold=0.5,
    )

    recent_claims = [
        "Revenue swings are mostly volume-driven with stable unit prices.",
    ]
    assert _is_duplicate_claim(
        "Monthly revenue variation is primarily driven by volume while prices stay stable.",
        recent_claims,
    )


def test_format_notebook_empty():
    """Empty notebook produces a recognizable placeholder."""
    notebook = Notebook()
    text = format_notebook(notebook)
    assert "empty" in text.lower()


def test_format_notebook_with_entries():
    """Notebook with entries formats each theme."""
    notebook = Notebook(entries=[
        NotebookEntry(
            theme="Revenue",
            summary="Revenue is growing.",
            key_findings=["Q1 up 15%"],
            open_questions=["What drove Q1?"],
            tables_touched=["orders"],
            step_count=2,
        ),
    ], total_steps=2)
    text = format_notebook(notebook)
    assert "Revenue" in text
    assert "Q1 up 15%" in text
    assert "What drove Q1?" in text


def test_notebook_persistence(store):
    """Notebook round-trips through the store."""
    run_id = store.insert_run({"mode": "test"})
    notebook = Notebook(entries=[
        NotebookEntry(theme="Test", summary="Testing.", key_findings=["f1"],
                      open_questions=["q1"], tables_touched=["t1"], step_count=1),
    ], total_steps=1)
    store.save_notebook(run_id, notebook.model_dump())

    loaded = store.load_notebook(run_id)
    assert loaded is not None
    restored = Notebook.model_validate(loaded)
    assert restored.total_steps == 1
    assert restored.entries[0].theme == "Test"

    notebook2 = apply_notebook_update(restored, InterpretationResult(
        title="t", claim="c", confidence=0.5, reasoning="r",
        theme="Test", summary_update="Updated.", new_finding="f2",
        new_open_questions=[], resolved_questions=["q1"],
    ))
    store.save_notebook(run_id, notebook2.model_dump())
    loaded2 = store.load_notebook(run_id)
    restored2 = Notebook.model_validate(loaded2)
    assert restored2.total_steps == 2
    assert len(restored2.entries[0].key_findings) == 2


def _col(
    name: str,
    dtype: str,
    *,
    null_count: int = 0,
    null_pct: float = 0.0,
    distinct_count: int = 0,
    cardinality_ratio: float = 0.0,
    stddev: float | None = None,
    min_val: str | None = None,
    max_val: str | None = None,
) -> ColumnProfile:
    return ColumnProfile(
        name=name,
        dtype=dtype,
        nullable=True,
        null_count=null_count,
        null_pct=null_pct,
        distinct_count=distinct_count,
        cardinality_ratio=cardinality_ratio,
        sample_values=[],
        min_val=min_val,
        max_val=max_val,
        mean=None,
        stddev=stddev,
        p25=None,
        p50=None,
        p75=None,
    )
