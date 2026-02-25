from __future__ import annotations

from pathlib import Path

from nemo.config import NemoConfig
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


def test_generators_and_loader_cover_sprint3_scope(store):
    ctx = _make_context(store)
    items = run_generators(ctx)

    assert items
    assert {item.action_type for item in items} >= {
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
    }
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
            _col("order_date", "DATE", distinct_count=180, cardinality_ratio=0.18),
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


def _col(
    name: str,
    dtype: str,
    *,
    null_count: int = 0,
    null_pct: float = 0.0,
    distinct_count: int = 0,
    cardinality_ratio: float = 0.0,
    stddev: float | None = None,
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
        min_val=None,
        max_val=None,
        mean=None,
        stddev=stddev,
        p25=None,
        p50=None,
        p75=None,
    )
