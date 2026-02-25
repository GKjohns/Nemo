from __future__ import annotations

import asyncio

from nemo.config import NemoConfig
from nemo.executor.run import ExecutionResult
from nemo.planner.models import FrontierItem
from nemo.summarize import summarize_result


def test_summarize_result_produces_structured_draft():
    action = FrontierItem(
        action_type="SEGMENT_COMPARE",
        payload={"table": "orders", "metric_col": "amount", "dimension_col": "segment"},
        dedupe_key="segment_compare:orders.segment:amount",
    )
    result = ExecutionResult(
        sql="SELECT segment, AVG(amount) AS metric_avg FROM orders GROUP BY segment",
        rows=[{"segment": "enterprise", "metric_avg": 120.0}],
        row_count=1,
        column_names=["segment", "metric_avg"],
        truncated=False,
        cost_ms=42,
    )
    draft = asyncio.run(
        summarize_result(
            action=action,
            result=result,
            profiles=[],
            recent_insights=[],
            config=NemoConfig(),
        )
    )
    assert draft.title.startswith("SEGMENT_COMPARE")
    assert draft.question
    assert draft.claim
    assert 0.0 <= draft.confidence <= 1.0
    assert isinstance(draft.hypothesis_struct, dict)
    assert isinstance(draft.claim_struct, dict)
    assert draft.result_summary["row_count"] == 1


def test_summarize_empty_result_records_low_confidence():
    action = FrontierItem(
        action_type="TOP_GROUPS",
        payload={"table": "orders", "metric_col": "amount", "group_col": "segment"},
        dedupe_key="top_groups:orders.segment:amount",
    )
    result = ExecutionResult(
        sql="SELECT segment, SUM(amount) AS metric_total FROM orders WHERE 1=0 GROUP BY segment",
        rows=[],
        row_count=0,
        column_names=["segment", "metric_total"],
        truncated=False,
        cost_ms=20,
    )
    draft = asyncio.run(
        summarize_result(
            action=action,
            result=result,
            profiles=[],
            recent_insights=[],
            config=NemoConfig(),
        )
    )
    assert "No rows returned" in draft.claim
    assert draft.confidence <= 0.25
