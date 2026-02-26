from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from nemo.config import NemoConfig
from nemo.engine import NemoEngine
from nemo.events import EventBus
from nemo.executor import execute_query
from nemo.ingest.add import add_tpch
from nemo.report import generate_brief_markdown
from nemo.store import NemoStore


def _make_config(**overrides) -> NemoConfig:
    """Build a NemoConfig that picks up OPENAI_API_KEY from env."""
    defaults = dict(
        max_steps=15,
        max_runtime_minutes=10,
        max_scan_rows=200,
        max_query_runtime_ms=15000,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
    )
    defaults.update(overrides)
    return NemoConfig(**defaults)


def test_tpch_golden(project_dir: Path):
    store = NemoStore(project_dir / "nemo.duckdb")
    store.initialize()
    try:
        try:
            add_tpch(store, scale=0.01)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"tpch extension unavailable: {exc}")

        config = _make_config(max_steps=15)
        engine = NemoEngine(store, config, EventBus())
        asyncio.run(engine.run(max_steps=15))

        insight_rows = store.execute("SELECT insight_id, sql, claim, result_summary_json FROM insights").fetchall()
        assert len(insight_rows) >= 5

        brief = generate_brief_markdown(store, top_n=10)
        assert "## Top Insights" in brief
        assert "## Contradictions" in brief
        assert "## Coverage" in brief
        assert "## Recommendations" in brief

        forbidden = ("insert ", "update ", "delete ", "drop ", "create ", "alter ", "truncate ", "attach ", "copy ")
        for insight_id, sql, claim, _ in insight_rows:
            sql_text = str(sql or "").strip().lower()
            claim_text = str(claim or "").strip()
            assert sql_text
            assert claim_text
            assert sql_text.startswith("select") or sql_text.startswith("with") or sql_text.startswith("-- action_id:")
            assert not any(token in sql_text for token in forbidden)
            assert str(insight_id).strip()

        if config.openai_api_key:
            reasoning_rows = store.execute(
                "SELECT reasoning FROM insights WHERE reasoning IS NOT NULL"
            ).fetchall()
            assert len(reasoning_rows) >= 1, "strategist loop should produce insights with reasoning"

            notebook_rows = store.execute("SELECT notebook_json FROM notebooks").fetchall()
            assert len(notebook_rows) >= 1, "strategist loop should persist a notebook"
    finally:
        store.close()


def test_insight_reproducibility(project_dir: Path):
    store = NemoStore(project_dir / "nemo.duckdb")
    store.initialize()
    try:
        try:
            add_tpch(store, scale=0.01)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"tpch extension unavailable: {exc}")

        config = _make_config(max_steps=12)
        engine = NemoEngine(store, config, EventBus())
        asyncio.run(engine.run(max_steps=12))

        rows = store.execute("SELECT insight_id, sql, result_summary_json FROM insights ORDER BY created_at ASC").fetchall()
        assert rows, "expected at least one insight"
        for insight_id, sql, result_summary_json in rows:
            sql_text = str(sql or "").strip()
            assert sql_text, f"insight {insight_id} missing SQL"
            rerun = execute_query(store, sql_text, config)
            assert rerun.error is None, f"insight {insight_id} failed to re-execute: {rerun.error}"

            expected_row_count = None
            if isinstance(result_summary_json, str) and result_summary_json.strip():
                try:
                    summary = json.loads(result_summary_json)
                except json.JSONDecodeError:
                    summary = {}
                if isinstance(summary, dict):
                    expected_row_count = summary.get("row_count")
            if expected_row_count is not None:
                assert abs(int(rerun.row_count) - int(expected_row_count)) <= 0
    finally:
        store.close()
