from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nemo.config import NemoConfig
from nemo.engine import (
    _build_coverage_context,
    _is_duplicate_question,
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
from nemo.planner.arbiter import decide_phase, should_consult_arbiter
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
