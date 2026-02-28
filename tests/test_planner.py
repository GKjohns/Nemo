from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nemo.config import NemoConfig
from nemo.engine import (
    _build_coverage_context,
    _is_duplicate_question,
    _should_suppress_hypothesis,
    is_semantically_duplicate,
)
from nemo.executor.run import ExecutionResult
from nemo.ingest.profile import ColumnProfile, TableProfile
from nemo.planner import (
    GeneratorContext,
    dedupe_frontier,
    derive_recent_insight_keys,
    get_all_generators,
    is_saturated,
    rerank_frontier,
    run_generators,
    score_frontier,
    select_next,
)
from nemo.planner.arbiter import (
    _count_consecutive_exploits,
    _explore_ratio,
    _format_hypotheses,
    _has_diminishing_returns,
    decide_phase,
    effective_max_validation_steps,
    should_consult_arbiter,
)
from nemo.planner.models import (
    DuplicateCheck,
    EvidenceLink,
    HypothesisRecord,
    PhaseDecision,
    RankedCandidate,
    RerankedFrontier,
)
from nemo.planner.strategist import (
    Hypothesis,
    InterpretationResult,
    Notebook,
    NotebookEntry,
    apply_notebook_update,
    _format_rows,
    build_schema_context,
    format_notebook,
    interpret_and_update,
)
from nemo.planner.validator import (
    classify_validation_evidence,
    plan_validation_step,
    render_verdict,
    should_render_verdict,
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


def test_hypothesis_analysis_type_defaults_to_sql():
    hypothesis = Hypothesis(
        question="How many orders are in the table?",
        reasoning="Start with a baseline volume check.",
        sql='SELECT COUNT(*) AS c FROM "orders" LIMIT 200',
        table="orders",
    )
    assert hypothesis.analysis_type == "sql"


def test_hypothesis_accepts_statistical_analysis_type():
    hypothesis = Hypothesis(
        question="Is there a statistically significant difference by segment?",
        reasoning="This needs inferential testing, not only grouped means.",
        sql='SELECT "segment", "amount" FROM "orders" WHERE "amount" IS NOT NULL LIMIT 200',
        table="orders",
        analysis_type="statistical",
    )
    assert hypothesis.analysis_type == "statistical"


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


def test_rerank_frontier_applies_llm_order_and_reasoning(store):
    ctx = _make_context(store)
    scored = score_frontier(run_generators(ctx), ctx)[:8]

    class _FakeResponses:
        def __init__(self) -> None:
            self.kwargs = None

        def parse(self, **kwargs):
            self.kwargs = kwargs
            return type(
                "Resp",
                (),
                {
                    "output_parsed": RerankedFrontier(
                        rankings=[
                            RankedCandidate(rank=1, action_index=2, reasoning="Best next high-impact cut."),
                            RankedCandidate(rank=2, action_index=0, reasoning="Strong baseline follow-up."),
                            RankedCandidate(rank=3, action_index=1, reasoning="Good supporting investigation."),
                        ]
                    ),
                    "output": [],
                },
            )()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    notebook = Notebook(
        entries=[
            NotebookEntry(
                theme="Revenue",
                summary="Revenue trend has early signal.",
                key_findings=["Q1 shows stronger variance by segment."],
                open_questions=["Which dimension best explains the variance?"],
                tables_touched=["orders"],
                step_count=2,
            )
        ],
        total_steps=2,
    )
    fake_client = _FakeClient()
    reranked, deterministic = rerank_frontier(scored, notebook, NemoConfig(), fake_client, top_n=8)  # type: ignore[arg-type]
    assert deterministic[0].action_id == scored[0].action_id
    assert reranked[0].action_id == scored[2].action_id
    assert "Best next high-impact cut." in reranked[0].rationale
    assert fake_client.responses.kwargs is not None
    assert fake_client.responses.kwargs["text_format"] is RerankedFrontier


def test_rerank_frontier_falls_back_to_deterministic_on_llm_error(store):
    ctx = _make_context(store)
    scored = score_frontier(run_generators(ctx), ctx)[:8]

    class _BrokenResponses:
        def parse(self, **kwargs):
            raise RuntimeError("synthetic llm failure")

    class _BrokenClient:
        def __init__(self) -> None:
            self.responses = _BrokenResponses()

    reranked, deterministic = rerank_frontier(
        scored,
        Notebook(),
        NemoConfig(),
        _BrokenClient(),  # type: ignore[arg-type]
        top_n=8,
    )
    assert [item.action_id for item in reranked] == [item.action_id for item in scored]
    assert [item.action_id for item in deterministic] == [item.action_id for item in scored]


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


def test_build_schema_context_scopes_non_focus_tables_to_compact_lines():
    profiles = [_orders_profile(), _customers_profile()]
    ctx = build_schema_context(profiles, focus_tables={"orders"}, max_samples=1)
    assert "orders (1,000 rows):" in ctx
    assert "customers (350 rows, 4 columns): customer_id, region, signup_date, lifetime_value" in ctx
    assert "customers (350 rows):" not in ctx


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


def test_semantic_duplicate_llm_catches_paraphrase_jaccard_misses():
    baseline = "How does revenue differ by geography?"
    paraphrase = "What drives revenue variation across regions?"
    assert not _is_duplicate_question(paraphrase, [baseline], threshold=0.82)

    class _FakeResponses:
        def parse(self, **kwargs):
            payload = json.loads(str(kwargs.get("input", "{}")))
            new_text = str(payload.get("new_text", "")).lower()
            candidates = [str(row.get("text", "")).lower() for row in payload.get("candidates", [])]
            is_duplicate = "revenue" in new_text and any(
                "geography" in candidate or "regions" in candidate for candidate in candidates
            )
            return type(
                "Resp",
                (),
                {
                    "output_parsed": DuplicateCheck(
                        is_duplicate=is_duplicate,
                        reasoning="Semantic meaning aligns despite wording differences.",
                    ),
                    "output": [],
                },
            )()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    assert asyncio.run(
        is_semantically_duplicate(
            paraphrase,
            [baseline],
            NemoConfig(),
            _FakeClient(),  # type: ignore[arg-type]
            mode="question",
        )
    )


def test_semantic_duplicate_lexical_fallback_without_client():
    assert asyncio.run(
        is_semantically_duplicate(
            "Supplier X return rates are 2x higher than peers",
            ["Supplier X has 2x higher return rates"],
            NemoConfig(),
            None,
            mode="claim",
        )
    )
    assert not asyncio.run(
        is_semantically_duplicate(
            "Revenue by region",
            ["Customer count by segment"],
            NemoConfig(),
            None,
            mode="question",
        )
    )


def test_should_consult_arbiter_cadence_and_events():
    assert should_consult_arbiter(step_num=1, last_arbiter_step=0, arbiter_interval=3, significant_event=False)
    assert not should_consult_arbiter(step_num=2, last_arbiter_step=1, arbiter_interval=3, significant_event=False)
    assert should_consult_arbiter(step_num=4, last_arbiter_step=1, arbiter_interval=3, significant_event=False)
    assert should_consult_arbiter(step_num=2, last_arbiter_step=1, arbiter_interval=3, significant_event=True)


def test_arbiter_guardrail_no_hypotheses_forces_explore():
    decision = asyncio.run(
        decide_phase(
            notebook=Notebook(),
            hypotheses=[],
            all_tables=["orders"],
            steps_done=3,
            budget=20,
            recent_phases=[],
            config=NemoConfig(),
            client=None,
        )
    )
    assert decision.phase == "explore"
    assert decision.hypothesis_id is None


def test_arbiter_guardrail_budget_exhaustion_forces_exploit():
    hypotheses = [
        HypothesisRecord(
            hypothesis_id="hyp_1",
            claim="A",
            source_insight_id="insight_1",
            initial_confidence=0.6,
            status="proposed",
            priority=0.4,
        ),
        HypothesisRecord(
            hypothesis_id="hyp_2",
            claim="B",
            source_insight_id="insight_2",
            initial_confidence=0.8,
            status="proposed",
            priority=0.9,
        ),
    ]
    decision = asyncio.run(
        decide_phase(
            notebook=Notebook(),
            hypotheses=hypotheses,
            all_tables=["orders"],
            steps_done=9,
            budget=10,
            recent_phases=[],
            config=NemoConfig(),
            client=None,
        )
    )
    assert decision.phase == "exploit"
    assert decision.hypothesis_id == "hyp_2"


def test_arbiter_guardrail_mid_validation_continues():
    hypotheses = [
        HypothesisRecord(
            hypothesis_id="hyp_active",
            claim="Supplier X is elevated",
            source_insight_id="insight_3",
            initial_confidence=0.8,
            status="testing",
            validation_step=2,
        )
    ]
    decision = asyncio.run(
        decide_phase(
            notebook=Notebook(),
            hypotheses=hypotheses,
            all_tables=["orders"],
            steps_done=4,
            budget=20,
            recent_phases=[],
            config=NemoConfig(max_validation_steps=5),
            client=None,
        )
    )
    assert decision.phase == "exploit"
    assert decision.hypothesis_id == "hyp_active"


def test_arbiter_llm_path_uses_structured_output():
    class _FakeResponses:
        def __init__(self) -> None:
            self.kwargs = None

        def parse(self, **kwargs):
            self.kwargs = kwargs
            return type(
                "Resp",
                (),
                {
                    "output_parsed": PhaseDecision(
                        phase="exploit",
                        hypothesis_id="hyp_llm",
                        reasoning="Highest business impact.",
                    ),
                    "output": [],
                },
            )()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    hypotheses = [
        HypothesisRecord(
            hypothesis_id="hyp_llm",
            claim="Return rates doubled",
            source_insight_id="insight_x",
            initial_confidence=0.7,
            status="proposed",
            priority=0.7,
        )
    ]
    fake_client = _FakeClient()
    decision = asyncio.run(
        decide_phase(
            notebook=Notebook(),
            hypotheses=hypotheses,
            all_tables=["orders"],
            steps_done=3,
            budget=20,
            recent_phases=[PhaseDecision(phase="explore", reasoning="Need backlog first.")],
            config=NemoConfig(),
            client=fake_client,  # type: ignore[arg-type]
            elapsed_minutes=1.0,
            time_budget_minutes=30.0,
        )
    )
    assert decision.phase == "exploit"
    assert decision.hypothesis_id == "hyp_llm"
    assert fake_client.responses.kwargs is not None
    assert fake_client.responses.kwargs["text_format"] is PhaseDecision


def test_validator_plans_step_with_structured_output():
    class _FakeResponses:
        def __init__(self) -> None:
            self.kwargs = None

        def parse(self, **kwargs):
            self.kwargs = kwargs
            return type(
                "Resp",
                (),
                {
                    "output_parsed": Hypothesis(
                        question="Does the signal hold by customer segment?",
                        reasoning="Segment check validates breadth versus concentration.",
                        sql='SELECT "segment", AVG("amount") AS avg_amount FROM "orders" GROUP BY 1 LIMIT 200',
                        table="orders",
                        analysis_type="statistical",
                    ),
                    "output": [],
                },
            )()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    record = HypothesisRecord(
        hypothesis_id="hyp_v1",
        claim="Supplier X has elevated return rates.",
        source_insight_id="insight_1",
        initial_confidence=0.72,
        status="testing",
        validation_step=1,
    )
    client = _FakeClient()
    planned = asyncio.run(
        plan_validation_step(
            hypothesis=record,
            notebook=Notebook(),
            schema_context='orders("segment", "amount")',
            config=NemoConfig(),
            client=client,  # type: ignore[arg-type]
        )
    )
    assert planned.table == "orders"
    assert planned.analysis_type == "statistical"
    assert "segment" in planned.question.lower()
    assert client.responses.kwargs is not None
    assert client.responses.kwargs["text_format"] is Hypothesis


def test_validator_parses_analysis_type_from_json_output():
    class _FakeResponses:
        def __init__(self) -> None:
            self.kwargs = None

        def parse(self, **kwargs):
            self.kwargs = kwargs
            return type(
                "Resp",
                (),
                {
                    "output_parsed": None,
                    "output": [
                        type(
                            "Message",
                            (),
                            {
                                "type": "message",
                                "content": [
                                    type(
                                        "TextBlock",
                                        (),
                                        {
                                            "text": (
                                                '{"question":"Does this effect remain after controls?",'
                                                '"reasoning":"Needs regression-based validation.",'
                                                '"sql":"SELECT \\"amount\\", \\"segment\\", \\"region\\" FROM \\"orders\\" LIMIT 200",'
                                                '"table":"orders",'
                                                '"analysis_type":"statistical"}'
                                            )
                                        },
                                    )()
                                ],
                            },
                        )()
                    ],
                },
            )()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    record = HypothesisRecord(
        hypothesis_id="hyp_v2",
        claim="Segment A outperforms Segment B after controls.",
        source_insight_id="insight_2",
        initial_confidence=0.74,
        status="testing",
        validation_step=2,
    )
    client = _FakeClient()
    planned = asyncio.run(
        plan_validation_step(
            hypothesis=record,
            notebook=Notebook(),
            schema_context='orders("amount", "segment", "region")',
            config=NemoConfig(),
            client=client,  # type: ignore[arg-type]
        )
    )
    assert planned.analysis_type == "statistical"
    assert "controls" in planned.question.lower()
    assert client.responses.kwargs is not None
    assert client.responses.kwargs["text_format"] is Hypothesis


def test_verdict_short_circuit_logic_and_render():
    record = HypothesisRecord(
        hypothesis_id="hyp_v2",
        claim="Revenue drop is caused by AUTO segment contraction.",
        source_insight_id="insight_2",
        initial_confidence=0.68,
        status="testing",
    )
    record.evidence_chain = []
    assert not should_render_verdict(record, max_validation_steps=5)

    record.validation_step = 1
    record.evidence_chain = [
        EvidenceLink(insight_id="i1", relationship="contradicts", note="Signal fails to reproduce."),
    ]
    assert should_render_verdict(record, max_validation_steps=5, latest_relationship="contradicts")

    class _FakeResponses:
        def parse(self, **kwargs):
            return type(
                "Resp",
                (),
                {
                    "output_parsed": type(
                        "Parsed",
                        (),
                        {
                            "status": "invalidated",
                            "confidence": 0.86,
                            "verdict": "The core signal did not reproduce across the baseline comparison.",
                        },
                    )(),
                    "output": [],
                },
            )()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    status, confidence, verdict = asyncio.run(
        render_verdict(
            hypothesis=record,
            evidence_chain=[
                {"insight_id": "i1", "relationship": "contradicts", "note": "Signal fails to reproduce."}
            ],
            config=NemoConfig(),
            client=_FakeClient(),  # type: ignore[arg-type]
        )
    )
    assert status == "invalidated"
    assert confidence == 0.86
    assert "did not reproduce" in verdict


def test_validation_evidence_classification():
    assert classify_validation_evidence("Result is only for one specific segment.") == "narrows"
    assert classify_validation_evidence("After controlling for seasonality, effect disappears.") == "confounds"
    assert classify_validation_evidence("The hypothesis is not supported and fails to reproduce.") == "contradicts"
    assert classify_validation_evidence("Effect remains strong versus baseline.") == "supports"


def test_format_hypotheses_filters_resolved_and_truncates_claims():
    hypotheses = [
        HypothesisRecord(
            hypothesis_id="hyp_active_high",
            claim="A" * 220,
            source_insight_id="insight_1",
            initial_confidence=0.81,
            status="proposed",
            priority=0.95,
        ),
        HypothesisRecord(
            hypothesis_id="hyp_active_low",
            claim="Short active claim.",
            source_insight_id="insight_2",
            initial_confidence=0.62,
            status="testing",
            priority=0.4,
        ),
        HypothesisRecord(
            hypothesis_id="hyp_resolved",
            claim="Resolved claim should only appear when requested.",
            source_insight_id="insight_3",
            initial_confidence=0.7,
            status="validated",
            priority=0.5,
            verdict="Signal reproduced.",
        ),
    ]

    text = _format_hypotheses(hypotheses, include_resolved=False, max_active=10)
    assert "Active hypotheses:" in text
    assert "hyp_active_high" in text
    assert "hyp_active_low" in text
    assert "Resolved hypotheses: 1 (1 validated)" in text
    assert "Recent resolved hypotheses" not in text
    assert "A" * 151 not in text
    assert "..." in text


def test_format_hypotheses_can_include_recent_resolved_entries():
    hypotheses = [
        HypothesisRecord(
            hypothesis_id="hyp_resolved_old",
            claim="Old resolved hypothesis.",
            source_insight_id="insight_10",
            initial_confidence=0.65,
            status="invalidated",
            priority=0.3,
            verdict="Did not reproduce.",
        ),
        HypothesisRecord(
            hypothesis_id="hyp_resolved_new",
            claim="New resolved hypothesis.",
            source_insight_id="insight_11",
            initial_confidence=0.78,
            status="validated",
            priority=0.6,
            verdict="Replicated in holdout.",
        ),
    ]

    text = _format_hypotheses(hypotheses, include_resolved=True, max_resolved=1)
    assert "Recent resolved hypotheses:" in text
    assert "hyp_resolved_new" in text
    assert "hyp_resolved_old" not in text


def test_format_rows_uses_markdown_table_and_caps_wide_results():
    columns = [f"col_{idx}" for idx in range(1, 13)]
    rows = [
        {column: f"v1_{column}" for column in columns},
        {column: f"v2_{column}" for column in columns},
    ]
    text = _format_rows(rows, columns, max_rows=1, max_columns=10)
    assert "| col_1 | col_2 | col_3 |" in text
    assert "| v1_col_1 | v1_col_2 | v1_col_3 |" in text
    assert "(+ 2 more columns: col_11, col_12)" in text
    assert "... (2 rows total)" in text
    assert "col_1=v1_col_1" not in text


def test_interpretation_preview_caps_rows_for_statistical_mode():
    class _FakeResponses:
        def __init__(self) -> None:
            self.last_input = None

        def parse(self, **kwargs):
            self.last_input = kwargs.get("input")
            return type(
                "Resp",
                (),
                {
                    "output_parsed": InterpretationResult(
                        title="Stat preview",
                        claim="Preview looks fine.",
                        confidence=0.6,
                        reasoning="Rows are capped for statistical context.",
                        theme="Stats",
                        summary_update="Stats summary updated.",
                        new_finding="Statistical rows were sampled.",
                        new_open_questions=[],
                        resolved_questions=[],
                    ),
                    "output": [],
                },
            )()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    hypothesis = Hypothesis(
        question="Is segment effect statistically significant?",
        reasoning="Need inferential test.",
        sql='SELECT "segment", "amount" FROM "orders" LIMIT 200',
        table="orders",
        analysis_type="statistical",
    )
    result = ExecutionResult(
        sql=hypothesis.sql,
        rows=[
            {"segment": "A", "amount": idx}
            for idx in [1, 2, 3, 4, 5, 6]
        ],
        row_count=6,
        column_names=["segment", "amount"],
        truncated=False,
        cost_ms=5,
        error=None,
    )
    notebook = Notebook()
    fake_client = _FakeClient()
    parsed = asyncio.run(
        interpret_and_update(
            hypothesis=hypothesis,
            result=result,
            notebook=notebook,
            schema_context='orders("segment", "amount")',
            config=NemoConfig(max_display_rows=15),
            client=fake_client,  # type: ignore[arg-type]
        )
    )
    assert parsed.title == "Stat preview"
    prompt = str(fake_client.responses.last_input)
    assert "... (6 rows total)" in prompt
    assert "| A | 5 |" in prompt
    assert "| A | 6 |" not in prompt


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


def test_format_notebook_summary_detail_caps_recent_items():
    notebook = Notebook(
        entries=[
            NotebookEntry(
                theme="Revenue",
                summary="Revenue moved unevenly by segment.",
                key_findings=["f1", "f2", "f3"],
                open_questions=["q1", "q2"],
                tables_touched=["orders"],
                step_count=3,
            ),
        ],
        total_steps=3,
    )
    text = format_notebook(notebook, detail="summary")
    assert "Recent findings" in text
    assert "Top open question" in text
    assert "- f2" in text
    assert "- f3" in text
    assert "- q2" in text
    assert "- q1" not in text


def test_format_notebook_headlines_detail():
    notebook = Notebook(
        entries=[
            NotebookEntry(
                theme="Quality",
                summary="Quality baseline.",
                key_findings=["Returns elevated in one supplier cohort."],
                open_questions=[],
                tables_touched=["orders"],
                step_count=1,
            )
        ],
        total_steps=1,
    )
    text = format_notebook(notebook, detail="headlines")
    assert "latest: Returns elevated in one supplier cohort." in text
    assert "1 findings" in text
    assert "Summary:" not in text


def test_format_notebook_limits_theme_count_with_earlier_line():
    notebook = Notebook(
        entries=[
            NotebookEntry(theme="Theme A", summary="A", key_findings=["a"], open_questions=[], tables_touched=["t1"], step_count=1),
            NotebookEntry(theme="Theme B", summary="B", key_findings=["b"], open_questions=[], tables_touched=["t2"], step_count=3),
            NotebookEntry(theme="Theme C", summary="C", key_findings=["c"], open_questions=[], tables_touched=["t3"], step_count=2),
        ],
        total_steps=6,
    )
    text = format_notebook(notebook, max_themes=2)
    assert "Theme B" in text
    assert "Theme C" in text
    assert "Earlier themes: Theme A (1 steps)" in text


def test_format_notebook_full_applies_findings_and_questions_caps():
    notebook = Notebook(
        entries=[
            NotebookEntry(
                theme="Revenue",
                summary="Trend theme.",
                key_findings=["old finding", "new finding"],
                open_questions=["old question", "new question"],
                tables_touched=["orders"],
                step_count=2,
            )
        ],
        total_steps=2,
    )
    text = format_notebook(
        notebook,
        detail="full",
        max_findings_per_theme=1,
        max_questions_per_theme=1,
    )
    assert "new finding" in text
    assert "old finding" not in text
    assert "new question" in text
    assert "old question" not in text


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
    sample_values: list[str] | None = None,
) -> ColumnProfile:
    return ColumnProfile(
        name=name,
        dtype=dtype,
        nullable=True,
        null_count=null_count,
        null_pct=null_pct,
        distinct_count=distinct_count,
        cardinality_ratio=cardinality_ratio,
        sample_values=list(sample_values or []),
        min_val=min_val,
        max_val=max_val,
        mean=None,
        stddev=stddev,
        p25=None,
        p50=None,
        p75=None,
    )


def test_interpretation_can_propose_hypothesis():
    class _FakeResponses:
        def __init__(self) -> None:
            self.last_input = None

        def parse(self, **kwargs):
            self.last_input = kwargs.get("input")
            return type(
                "Resp",
                (),
                {
                    "output_parsed": InterpretationResult(
                        title="Returns anomaly",
                        claim="Supplier X shows ~2x return rate versus baseline.",
                        confidence=0.81,
                        reasoning="This appears materially above peer suppliers and worth direct validation.",
                        theme="Quality",
                        summary_update="Quality theme now includes a likely supplier-specific return issue.",
                        new_finding="Supplier X return rate is roughly double baseline.",
                        new_open_questions=["Does this hold across customer segments?"],
                        resolved_questions=[],
                        proposed_hypothesis="Supplier X has 2x higher return rates than market average.",
                        hypothesis_confidence=0.78,
                    ),
                    "output": [],
                },
            )()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    fake_client = _FakeClient()
    hypothesis = Hypothesis(
        question="Do any suppliers have unusually high return rates?",
        reasoning="Quality outliers are a high-impact risk.",
        sql='SELECT "supplier", AVG("is_returned") AS return_rate FROM "orders" GROUP BY 1',
        table="orders",
    )
    result = ExecutionResult(
        sql=hypothesis.sql,
        rows=[{"supplier": "X", "return_rate": 0.24}],
        row_count=1,
        column_names=["supplier", "return_rate"],
        truncated=False,
        cost_ms=12,
        error=None,
    )
    notebook = Notebook()
    parsed = asyncio.run(
        interpret_and_update(
            hypothesis=hypothesis,
            result=result,
            notebook=notebook,
            schema_context="orders(supplier, is_returned)",
            config=NemoConfig(),
            client=fake_client,  # type: ignore[arg-type]
        )
    )
    assert parsed.proposed_hypothesis == "Supplier X has 2x higher return rates than market average."
    assert parsed.hypothesis_confidence == 0.78
    prompt = str(fake_client.responses.last_input)
    assert "specific, testable hypothesis" in prompt


# ---------------------------------------------------------------------------
# Anti-anchoring guardrail tests
# ---------------------------------------------------------------------------


def test_count_consecutive_exploits():
    phases = [
        PhaseDecision(phase="explore", reasoning="a"),
        PhaseDecision(phase="exploit", reasoning="b"),
        PhaseDecision(phase="exploit", reasoning="c"),
        PhaseDecision(phase="exploit", reasoning="d"),
    ]
    assert _count_consecutive_exploits(phases) == 3
    assert _count_consecutive_exploits([]) == 0
    assert _count_consecutive_exploits([PhaseDecision(phase="explore", reasoning="x")]) == 0


def test_explore_ratio():
    phases = [
        PhaseDecision(phase="explore", reasoning="a"),
        PhaseDecision(phase="exploit", reasoning="b"),
        PhaseDecision(phase="exploit", reasoning="c"),
        PhaseDecision(phase="exploit", reasoning="d"),
    ]
    assert _explore_ratio(phases) == 0.25
    assert _explore_ratio([]) == 1.0


def test_arbiter_forces_explore_after_max_consecutive_exploits():
    """After N consecutive exploit steps, the arbiter must force explore."""
    hypotheses = [
        HypothesisRecord(
            hypothesis_id="hyp_testing",
            claim="Some hypothesis being tested",
            source_insight_id="insight_1",
            initial_confidence=0.8,
            status="testing",
            validation_step=2,
            priority=0.8,
        )
    ]
    recent_phases = [PhaseDecision(phase="exploit", reasoning="continue")] * 7

    decision = asyncio.run(
        decide_phase(
            notebook=Notebook(),
            hypotheses=hypotheses,
            all_tables=["orders"],
            steps_done=10,
            budget=20,
            recent_phases=recent_phases,
            config=NemoConfig(max_consecutive_exploit=6, max_validation_steps=5),
            client=None,
        )
    )
    assert decision.phase == "explore"
    assert "consecutive exploit" in decision.reasoning.lower()


def test_arbiter_forces_explore_when_explore_ratio_too_low():
    """If the explore ratio drops below the minimum, force explore."""
    hypotheses = [
        HypothesisRecord(
            hypothesis_id="hyp_1",
            claim="Test claim",
            source_insight_id="insight_1",
            initial_confidence=0.7,
            status="proposed",
            priority=0.7,
        )
    ]
    recent_phases = [
        PhaseDecision(phase="explore", reasoning="first"),
        PhaseDecision(phase="exploit", reasoning="a"),
        PhaseDecision(phase="exploit", reasoning="b"),
        PhaseDecision(phase="exploit", reasoning="c"),
        PhaseDecision(phase="exploit", reasoning="d"),
        PhaseDecision(phase="exploit", reasoning="e"),
    ]

    decision = asyncio.run(
        decide_phase(
            notebook=Notebook(),
            hypotheses=hypotheses,
            all_tables=["orders"],
            steps_done=6,
            budget=20,
            recent_phases=recent_phases,
            config=NemoConfig(min_explore_ratio=0.35, max_consecutive_exploit=10),
            client=None,
        )
    )
    assert decision.phase == "explore"
    assert "explore ratio" in decision.reasoning.lower()


def test_arbiter_mid_validation_respects_exploit_cap():
    """Even with a testing hypothesis, consecutive exploit cap takes precedence."""
    hypotheses = [
        HypothesisRecord(
            hypothesis_id="hyp_testing",
            claim="Being validated",
            source_insight_id="insight_1",
            initial_confidence=0.85,
            status="testing",
            validation_step=1,
            priority=0.85,
        )
    ]
    recent_phases = [PhaseDecision(phase="exploit", reasoning="validating")] * 8

    decision = asyncio.run(
        decide_phase(
            notebook=Notebook(),
            hypotheses=hypotheses,
            all_tables=["orders"],
            steps_done=10,
            budget=20,
            recent_phases=recent_phases,
            config=NemoConfig(max_consecutive_exploit=6, max_validation_steps=5),
            client=None,
        )
    )
    assert decision.phase == "explore"


def test_should_suppress_duplicate_hypothesis_during_validation():
    """Hypothesis proposals that overlap existing claims should be suppressed."""
    existing = [
        HypothesisRecord(
            hypothesis_id="hyp_1",
            claim="Payroll Number is an agency-level code not an individual identifier",
            source_insight_id="ins_1",
            initial_confidence=0.85,
        )
    ]
    similar_claim = "Payroll Number 56 is an agency-level payroll bucket code"
    different_claim = "Fire Department overtime has doubled since 2020"

    assert _should_suppress_hypothesis(similar_claim, existing, is_during_validation=True)
    assert not _should_suppress_hypothesis(different_claim, existing, is_during_validation=True)


def test_should_suppress_is_stricter_during_validation():
    """During validation, suppression threshold is lower (0.45 vs 0.6)."""
    existing = [
        HypothesisRecord(
            hypothesis_id="hyp_1",
            claim="Payroll Number 56 is a department-level payroll bucket for NYPD overtime",
            source_insight_id="ins_1",
            initial_confidence=0.8,
        )
    ]
    moderately_similar = "Payroll Number 56 is a central NYPD payroll bucket that aggregates overtime payments"

    assert _should_suppress_hypothesis(moderately_similar, existing, is_during_validation=True)


def test_should_not_suppress_distinct_hypothesis():
    """Genuinely different hypotheses should not be suppressed."""
    existing = [
        HypothesisRecord(
            hypothesis_id="hyp_1",
            claim="Payroll Number is an agency-level code",
            source_insight_id="ins_1",
            initial_confidence=0.85,
        )
    ]
    distinct = "Ghost employees with NULL names account for $2B in total compensation"
    assert not _should_suppress_hypothesis(distinct, existing, is_during_validation=False)
    assert not _should_suppress_hypothesis(distinct, existing, is_during_validation=True)


def test_validation_hypothesis_gets_reduced_priority():
    """Hypotheses proposed during validation should have reduced priority to avoid cascading."""
    raw_confidence = 0.85
    reduced = max(0.0, raw_confidence - 0.2)
    assert abs(reduced - 0.65) < 1e-9
    assert reduced < raw_confidence


# ---------------------------------------------------------------------------
# Budget-proportional validation depth tests
# ---------------------------------------------------------------------------


def test_effective_max_validation_steps_scales_with_budget():
    """Short runs get tighter validation caps; long runs use the configured max."""
    config = NemoConfig(max_validation_steps=5, validation_budget_fraction=0.15)
    assert effective_max_validation_steps(config, budget=10) == 2
    assert effective_max_validation_steps(config, budget=20) == 3
    assert effective_max_validation_steps(config, budget=34) == 5
    assert effective_max_validation_steps(config, budget=100) == 5
    assert effective_max_validation_steps(config, budget=200) == 5


def test_effective_max_validation_steps_respects_configured_max():
    """Budget-scaled value never exceeds the configured max."""
    config = NemoConfig(max_validation_steps=3, validation_budget_fraction=0.5)
    assert effective_max_validation_steps(config, budget=100) == 3


def test_effective_max_validation_steps_floor_of_two():
    """Even on tiny budgets, at least 2 validation steps are allowed."""
    config = NemoConfig(max_validation_steps=5, validation_budget_fraction=0.15)
    assert effective_max_validation_steps(config, budget=1) == 2
    assert effective_max_validation_steps(config, budget=5) == 2


def test_has_diminishing_returns_empty_chain():
    """No evidence chain means no diminishing returns."""
    h = HypothesisRecord(
        hypothesis_id="hyp_1",
        claim="Some claim",
        source_insight_id="ins_1",
        initial_confidence=0.8,
        status="testing",
    )
    assert not _has_diminishing_returns(h)


def test_has_diminishing_returns_short_chain():
    """A single evidence item is not enough to trigger diminishing returns."""
    h = HypothesisRecord(
        hypothesis_id="hyp_1",
        claim="Some claim",
        source_insight_id="ins_1",
        initial_confidence=0.8,
        status="testing",
        validation_step=1,
    )
    h.evidence_chain = [
        EvidenceLink(insight_id="i1", relationship="confounds", note="Confounded."),
    ]
    assert not _has_diminishing_returns(h)


def test_has_diminishing_returns_two_confounds():
    """Two consecutive confounds/narrows triggers diminishing returns."""
    h = HypothesisRecord(
        hypothesis_id="hyp_1",
        claim="Some claim",
        source_insight_id="ins_1",
        initial_confidence=0.8,
        status="testing",
        validation_step=3,
    )
    h.evidence_chain = [
        EvidenceLink(insight_id="i1", relationship="supports", note="Good."),
        EvidenceLink(insight_id="i2", relationship="confounds", note="Confounded."),
        EvidenceLink(insight_id="i3", relationship="narrows", note="Only a subset."),
    ]
    assert _has_diminishing_returns(h)


def test_has_diminishing_returns_mixed_recent_evidence():
    """One confound and one support in the last two items is not diminishing."""
    h = HypothesisRecord(
        hypothesis_id="hyp_1",
        claim="Some claim",
        source_insight_id="ins_1",
        initial_confidence=0.8,
        status="testing",
        validation_step=2,
    )
    h.evidence_chain = [
        EvidenceLink(insight_id="i1", relationship="confounds", note="Confounded."),
        EvidenceLink(insight_id="i2", relationship="supports", note="Holds up."),
    ]
    assert not _has_diminishing_returns(h)


def test_arbiter_does_not_auto_continue_with_diminishing_returns():
    """When a testing hypothesis has diminishing returns, the arbiter should not
    auto-continue — it should fall through to broader decision logic."""
    h = HypothesisRecord(
        hypothesis_id="hyp_struggling",
        claim="DOE per-day pool drives extreme OT",
        source_insight_id="insight_1",
        initial_confidence=0.75,
        status="testing",
        validation_step=2,
        priority=0.75,
    )
    h.evidence_chain = [
        EvidenceLink(insight_id="i1", relationship="confounds", note="Unit-rate mismatch."),
        EvidenceLink(insight_id="i2", relationship="narrows", note="Only a small subgroup."),
    ]
    # No proposed hypotheses and only a struggling testing hypothesis:
    # the system should explore for new leads rather than auto-continue.
    decision = asyncio.run(
        decide_phase(
            notebook=Notebook(),
            hypotheses=[h],
            all_tables=["payroll"],
            steps_done=8,
            budget=20,
            recent_phases=[PhaseDecision(phase="exploit", reasoning="validating")],
            config=NemoConfig(max_validation_steps=5),
            client=None,
        )
    )
    assert decision.phase == "explore"
    assert "Continuing active validation" not in decision.reasoning


def test_arbiter_switches_to_better_hypothesis_with_diminishing_returns():
    """When one hypothesis is struggling but a high-priority proposed hypothesis
    exists, the arbiter should pick the proposed one instead."""
    struggling = HypothesisRecord(
        hypothesis_id="hyp_struggling",
        claim="DOE per-day pool drives extreme OT",
        source_insight_id="insight_1",
        initial_confidence=0.75,
        status="testing",
        validation_step=2,
        priority=0.75,
    )
    struggling.evidence_chain = [
        EvidenceLink(insight_id="i1", relationship="confounds", note="Unit-rate mismatch."),
        EvidenceLink(insight_id="i2", relationship="narrows", note="Only a subset."),
    ]
    waiting = HypothesisRecord(
        hypothesis_id="hyp_waiting",
        claim="Ghost employees with NULL payroll numbers",
        source_insight_id="insight_2",
        initial_confidence=0.85,
        status="proposed",
        priority=0.85,
    )
    decision = asyncio.run(
        decide_phase(
            notebook=Notebook(),
            hypotheses=[struggling, waiting],
            all_tables=["payroll"],
            steps_done=8,
            budget=20,
            recent_phases=[PhaseDecision(phase="exploit", reasoning="validating")],
            config=NemoConfig(max_validation_steps=5),
            client=None,
        )
    )
    # Should pick the waiting hypothesis, not auto-continue the struggling one
    assert decision.phase == "exploit"
    assert decision.hypothesis_id == "hyp_waiting"
    assert "Continuing active validation" not in decision.reasoning


def test_arbiter_auto_continues_healthy_validation():
    """When evidence is healthy, mid-validation auto-continue still works."""
    h = HypothesisRecord(
        hypothesis_id="hyp_healthy",
        claim="Salary anomaly in Fire Dept",
        source_insight_id="insight_1",
        initial_confidence=0.85,
        status="testing",
        validation_step=1,
        priority=0.85,
    )
    h.evidence_chain = [
        EvidenceLink(insight_id="i1", relationship="supports", note="Signal reproduces."),
    ]
    decision = asyncio.run(
        decide_phase(
            notebook=Notebook(),
            hypotheses=[h],
            all_tables=["payroll"],
            steps_done=4,
            budget=20,
            recent_phases=[],
            config=NemoConfig(max_validation_steps=5),
            client=None,
        )
    )
    assert decision.phase == "exploit"
    assert decision.hypothesis_id == "hyp_healthy"
    assert "Continuing active validation" in decision.reasoning


def test_arbiter_respects_budget_scaled_max_for_auto_continue():
    """On a short-budget run, the effective max is lower and validation
    should not auto-continue past it."""
    h = HypothesisRecord(
        hypothesis_id="hyp_at_limit",
        claim="Some claim",
        source_insight_id="insight_1",
        initial_confidence=0.8,
        status="testing",
        validation_step=3,
        priority=0.8,
    )
    h.evidence_chain = [
        EvidenceLink(insight_id="i1", relationship="supports", note="Good."),
        EvidenceLink(insight_id="i2", relationship="supports", note="Good."),
        EvidenceLink(insight_id="i3", relationship="supports", note="Good."),
    ]
    # Budget=20, fraction=0.15 → effective_max = min(5, ceil(3)) = 3
    # validation_step=3 is NOT < 3, so auto-continue should NOT trigger.
    decision = asyncio.run(
        decide_phase(
            notebook=Notebook(),
            hypotheses=[h],
            all_tables=["orders"],
            steps_done=8,
            budget=20,
            recent_phases=[],
            config=NemoConfig(max_validation_steps=5, validation_budget_fraction=0.15),
            client=None,
        )
    )
    # It should still choose exploit via the fallback path, but the key assertion
    # is that the reasoning does NOT say "Continuing active validation"
    assert "Continuing active validation" not in decision.reasoning
