"""Nemo exploration engine — agent-driven and legacy orchestration."""

from __future__ import annotations

import json
import re
import time
import traceback
from pathlib import Path
from typing import Any

from nemo.config import NemoConfig
from nemo.events import EventBus, EventType, NemoEvent
from nemo.executor import compile_action, execute_query
from nemo.executor.agent import AGENT_ACTION_TYPES, AgentResult, run_agent_exploration
from nemo.graph import find_contradiction_clusters, link_insight, record_learnings, update_thread_cards
from nemo.hooks import HookResult
from nemo.ingest.joins import discover_joins
from nemo.ingest.profile import profile_all
from nemo.planner import (
    GeneratorContext,
    dedupe_frontier,
    derive_recent_insight_keys,
    get_all_generators,
    is_saturated,
    score_frontier,
    select_next,
)
from nemo.planner.models import FrontierItem
from nemo.planner.strategist import (
    Hypothesis,
    InterpretationResult,
    Notebook,
    apply_notebook_update,
    build_schema_context,
    interpret_and_update,
    plan_next_step,
)
from nemo.store import NemoStore
from nemo.summarize import summarize_result
from nemo.summarize.summarize import InsightDraft, make_client


def load_working_memory(store: NemoStore, config: NemoConfig, run_id: str) -> dict[str, Any]:
    """Load iteration context with error feedback and learnings."""
    datasets = store.get_datasets()
    recent_insights = store.get_recent_insights(limit=20)
    rows = store.execute(
        """
        SELECT action_type, last_error
        FROM frontier
        WHERE run_id = ?
          AND status = 'error'
          AND last_error IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 20
        """,
        [run_id],
    ).fetchall()
    error_patterns = [{"action_type": str(row[0]), "error": str(row[1])} for row in rows]
    learnings = []
    if config.use_learnings:
        learning_rows = store.execute(
            """
            SELECT category, subject, detail, confidence
            FROM learnings
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()
        learnings = [
            {
                "category": str(row[0]),
                "subject": str(row[1]),
                "detail": str(row[2]),
                "confidence": float(row[3]),
            }
            for row in learning_rows
        ]
    return {
        "tables": [dataset["name"] for dataset in datasets],
        "recent_insights": recent_insights,
        "recent_insights_count": len(recent_insights),
        "error_patterns": error_patterns,
        "learnings": learnings,
    }


class NemoEngine:
    """Outer loop: agent-driven strategist when LLM available, legacy fallback otherwise."""

    def __init__(self, store: NemoStore, config: NemoConfig, bus: EventBus):
        self.store = store
        self.config = config
        self.bus = bus
        self._llm_client = make_client(config)

    async def run(
        self,
        max_steps: int | None = None,
        max_minutes: float | None = None,
        plan_only: bool = False,
        resume_run_id: str | None = None,
    ) -> str:
        started = time.perf_counter()
        run_id = resume_run_id or self.store.insert_run(self.config.to_dict(), status="running")
        status = "completed"
        steps_done = 0
        insights_created = 0
        errors = 0

        try:
            profiles = profile_all(self.store)
            joins = discover_joins(self.store, profiles)

            await self.bus.emit(
                NemoEvent(
                    type=EventType.RUN_STARTED,
                    run_id=run_id,
                    payload={
                        "config": self.config.to_dict(),
                        "datasets": self.store.get_datasets(),
                        "frontier_size": self.store.count_frontier(status="queued"),
                    },
                )
            )
            memory = load_working_memory(self.store, self.config, run_id)
            await self.bus.emit(
                NemoEvent(
                    type=EventType.MEMORY_LOADED,
                    run_id=run_id,
                    payload={
                        "tables": memory["tables"],
                        "recent_insights_count": memory["recent_insights_count"],
                        "learnings_count": len(memory["learnings"]),
                        "error_patterns": memory["error_patterns"],
                    },
                )
            )

            if self._llm_client is not None:
                steps_done, insights_created, errors, status = await self._run_strategist_loop(
                    run_id=run_id,
                    profiles=profiles,
                    joins=joins,
                    max_steps=max_steps,
                    max_minutes=max_minutes,
                    plan_only=plan_only,
                    started=started,
                )
            else:
                steps_done, insights_created, errors, status = await self._run_legacy_loop(
                    run_id=run_id,
                    profiles=profiles,
                    joins=joins,
                    memory=memory,
                    max_steps=max_steps,
                    max_minutes=max_minutes,
                    plan_only=plan_only,
                    started=started,
                )

            learning_ids: list[str] = []
            thread_ids: list[str] = []
            if self.config.use_learnings:
                try:
                    learning_ids = record_learnings(self.store, run_id)
                except Exception:  # noqa: BLE001
                    learning_ids = []
            try:
                thread_ids = update_thread_cards(self.store, self.config)
            except Exception:  # noqa: BLE001
                thread_ids = []

            stats = self._stats_payload(steps_done, insights_created, errors, started)
            if status == "interrupted":
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.RUN_INTERRUPTED,
                        run_id=run_id,
                        payload={
                            "reason": "signal",
                            "stats": stats,
                            "learnings_recorded": len(learning_ids),
                            "thread_cards_updated": len(thread_ids),
                        },
                    )
                )
            else:
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.RUN_COMPLETED,
                        run_id=run_id,
                        payload={
                            "stats": stats,
                            "learnings_recorded": len(learning_ids),
                            "thread_cards_updated": len(thread_ids),
                        },
                    )
                )
            self.store.update_run(
                run_id,
                status=status,
                steps_completed=steps_done,
                insights_created=insights_created,
                errors=errors,
                frontier_size=self.store.count_frontier(status="queued"),
                ended=True,
            )
            return run_id

        except KeyboardInterrupt:
            status = "interrupted"
            learning_ids = []
            thread_ids = []
            if self.config.use_learnings:
                try:
                    learning_ids = record_learnings(self.store, run_id)
                except Exception:  # noqa: BLE001
                    pass
            try:
                thread_ids = update_thread_cards(self.store, self.config)
            except Exception:  # noqa: BLE001
                pass
            stats = self._stats_payload(steps_done, insights_created, errors, started)
            await self.bus.emit(
                NemoEvent(
                    type=EventType.RUN_INTERRUPTED,
                    run_id=run_id,
                    payload={
                        "reason": "signal",
                        "stats": stats,
                        "learnings_recorded": len(learning_ids),
                        "thread_cards_updated": len(thread_ids),
                    },
                )
            )
            self.store.update_run(
                run_id, status=status,
                steps_completed=steps_done, insights_created=insights_created,
                errors=errors, frontier_size=self.store.count_frontier(status="queued"),
                ended=True,
            )
            return run_id

        except Exception as exc:  # noqa: BLE001
            await self.bus.emit(
                NemoEvent(
                    type=EventType.RUN_ERROR,
                    run_id=run_id,
                    payload={"error": str(exc), "traceback": traceback.format_exc()},
                )
            )
            self.store.update_run(
                run_id, status="error",
                steps_completed=steps_done, insights_created=insights_created,
                errors=errors + 1, frontier_size=self.store.count_frontier(status="queued"),
                notes=str(exc), ended=True,
            )
            raise

    # ------------------------------------------------------------------
    # Agent-driven strategist loop (primary path when LLM available)
    # ------------------------------------------------------------------

    async def _run_strategist_loop(
        self,
        run_id: str,
        profiles: list,
        joins: list,
        max_steps: int | None,
        max_minutes: float | None,
        plan_only: bool,
        started: float,
    ) -> tuple[int, int, int, str]:
        steps_done = 0
        insights_created = 0
        errors = 0
        status = "completed"

        schema_ctx = build_schema_context(profiles)
        notebook = self._load_or_create_notebook(run_id)
        all_tables = [p.name for p in profiles]
        recent_questions: list[str] = []
        recent_claims: list[str] = []
        stagnation_count = 0
        force_diversify = False

        step_budget = max_steps if max_steps is not None else int(self.config.max_steps)
        time_budget_minutes = (
            float(max_minutes) if max_minutes is not None else float(self.config.max_runtime_minutes)
        )

        if plan_only:
            coverage_context = _build_coverage_context(notebook, all_tables, self.config.max_steps_per_theme)
            frontier_hints = self._build_strategist_frontier_hints(profiles, joins)
            hypothesis = await plan_next_step(
                notebook,
                schema_ctx,
                self.config,
                self._llm_client,
                coverage_context=coverage_context,
                frontier_hints=frontier_hints,
            )
            await self.bus.emit(
                NemoEvent(
                    type=EventType.HYPOTHESIS_FORMED,
                    run_id=run_id,
                    step_num=1,
                    payload={
                        "question": hypothesis.question,
                        "reasoning": hypothesis.reasoning,
                        "sql": hypothesis.sql,
                        "table": hypothesis.table,
                    },
                )
            )
            return 0, 0, 0, "completed"

        while steps_done < step_budget:
            elapsed_minutes = (time.perf_counter() - started) / 60.0
            if elapsed_minutes >= time_budget_minutes:
                break

            steps_done += 1

            # --- PLAN: ask the LLM what to investigate next ---
            coverage_context = _build_coverage_context(notebook, all_tables, self.config.max_steps_per_theme)
            frontier_hints = self._build_strategist_frontier_hints(profiles, joins)
            planning_feedback = (
                "The prior steps are showing diminishing returns. "
                "Choose a materially different investigation touching a different table/theme."
                if force_diversify
                else None
            )
            try:
                hypothesis = await plan_next_step(
                    notebook,
                    schema_ctx,
                    self.config,
                    self._llm_client,
                    coverage_context=coverage_context,
                    frontier_hints=frontier_hints,
                    planning_feedback=planning_feedback,
                )
            except Exception as exc:  # noqa: BLE001
                errors += 1
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.STEP_ERROR,
                        run_id=run_id, step_num=steps_done,
                        payload={"phase": "planning", "error": str(exc), "will_retry": False},
                    )
                )
                continue

            if _is_duplicate_question(
                hypothesis.question,
                recent_questions,
                threshold=float(self.config.question_similarity_threshold),
            ):
                try:
                    hypothesis = await plan_next_step(
                        notebook,
                        schema_ctx,
                        self.config,
                        self._llm_client,
                        coverage_context=coverage_context,
                        frontier_hints=frontier_hints,
                        planning_feedback=(
                            "Your prior question is too similar to recent questions. "
                            "Ask a genuinely different question, ideally on a different table."
                        ),
                    )
                except Exception:  # noqa: BLE001
                    pass
            recent_questions = (recent_questions + [hypothesis.question])[-20:]

            await self.bus.emit(
                NemoEvent(
                    type=EventType.HYPOTHESIS_FORMED,
                    run_id=run_id,
                    step_num=steps_done,
                    payload={
                        "question": hypothesis.question,
                        "reasoning": hypothesis.reasoning,
                        "sql": hypothesis.sql,
                        "table": hypothesis.table,
                    },
                )
            )

            await self.bus.emit(
                NemoEvent(
                    type=EventType.STEP_STARTED,
                    run_id=run_id,
                    step_num=steps_done,
                    payload={
                        "action": {
                            "action_type": "STRATEGIST",
                            "payload": {"table": hypothesis.table, "question": hypothesis.question},
                            "score": 0.0,
                        },
                        "hypothesis": {
                            "question": hypothesis.question,
                            "reasoning": hypothesis.reasoning,
                        },
                    },
                )
            )

            # --- EXECUTE ---
            await self.bus.emit(
                NemoEvent(
                    type=EventType.STEP_PHASE,
                    run_id=run_id, step_num=steps_done,
                    payload={"phase": "executing", "sql": hypothesis.sql},
                )
            )
            result = execute_query(self.store, hypothesis.sql, self.config)

            if result.error:
                try:
                    hypothesis = await plan_next_step(
                        notebook, schema_ctx, self.config, self._llm_client,
                        error_context={"error": result.error, "sql": hypothesis.sql},
                    )
                    result = execute_query(self.store, hypothesis.sql, self.config)
                except Exception:  # noqa: BLE001
                    pass

            if result.error:
                errors += 1
                self.store.insert_learning(
                    run_id=run_id, category="error_pattern",
                    subject=f"STRATEGIST:{hypothesis.table}",
                    detail=result.error, confidence=0.7,
                )
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.STEP_ERROR,
                        run_id=run_id, step_num=steps_done,
                        payload={
                            "phase": "executing", "error": result.error,
                            "will_retry": False,
                        },
                    )
                )
                continue

            # --- INTERPRET + UPDATE NOTEBOOK ---
            await self.bus.emit(
                NemoEvent(
                    type=EventType.STEP_PHASE,
                    run_id=run_id, step_num=steps_done,
                    payload={"phase": "interpreting"},
                )
            )
            try:
                interpretation = await interpret_and_update(
                    hypothesis, result, notebook, schema_ctx, self.config, self._llm_client,
                )
            except Exception as exc:  # noqa: BLE001
                errors += 1
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.STEP_ERROR,
                        run_id=run_id, step_num=steps_done,
                        payload={"phase": "interpreting", "error": str(exc), "will_retry": False},
                    )
                )
                continue

            # --- RECORD ---
            source_tables = [hypothesis.table] if hypothesis.table else []
            insight_id = self.store.insert_insight(
                title=interpretation.title,
                question=hypothesis.question,
                sql=result.sql,
                result_summary_json={
                    "row_count": result.row_count,
                    "columns": result.column_names,
                    "truncated": result.truncated,
                    "cost_ms": result.cost_ms,
                },
                claim=interpretation.claim,
                run_id=run_id,
                thread_id=interpretation.theme,
                confidence=interpretation.confidence,
                status="ok",
                effect_size=interpretation.effect_size,
                cost_ms=result.cost_ms,
                source_tables_json=source_tables,
                tags_json=interpretation.tags,
                result_sample_json=result.rows[:10],
                reasoning=interpretation.reasoning,
            )
            insights_created += 1

            full_insight = self.store.get_insight_by_id(insight_id) or {"insight_id": insight_id}
            await self.bus.emit(
                NemoEvent(type=EventType.INSIGHT_CREATED, run_id=run_id, step_num=steps_done,
                          payload=full_insight)
            )

            # --- LINK ---
            await self.bus.emit(
                NemoEvent(
                    type=EventType.STEP_PHASE,
                    run_id=run_id, step_num=steps_done,
                    payload={"phase": "linking"},
                )
            )
            edge_count = 0
            for edge in link_insight(self.store, full_insight, self.config):
                self.store.insert_edge(
                    from_insight_id=edge["from_insight_id"],
                    to_insight_id=edge["to_insight_id"],
                    edge_type=edge["type"],
                    weight=float(edge["weight"]),
                    rationale=str(edge["rationale"]),
                )
                edge_count += 1

            clusters = find_contradiction_clusters(self.store)
            if clusters:
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.CONTRADICTION_DETECTED,
                        run_id=run_id, step_num=steps_done,
                        payload={"cluster": clusters[0]},
                    )
                )

            # --- UPDATE NOTEBOOK ---
            notebook = apply_notebook_update(notebook, interpretation)
            self.store.save_notebook(run_id, notebook.model_dump_json())

            if _is_duplicate_claim(interpretation.claim, recent_claims):
                stagnation_count += 1
            else:
                stagnation_count = 0
            recent_claims = (recent_claims + [interpretation.claim])[-20:]
            force_diversify = stagnation_count >= max(1, int(self.config.stagnation_step_limit))

            await self.bus.emit(
                NemoEvent(
                    type=EventType.NOTEBOOK_UPDATED,
                    run_id=run_id, step_num=steps_done,
                    payload={
                        "themes": [e.theme for e in notebook.entries],
                        "total_steps": notebook.total_steps,
                    },
                )
            )

            # --- EMIT STEP COMPLETED ---
            await self.bus.emit(
                NemoEvent(
                    type=EventType.STEP_COMPLETED,
                    run_id=run_id,
                    step_num=steps_done,
                    payload={
                        "insight_id": insight_id,
                        "title": interpretation.title,
                        "question": hypothesis.question,
                        "claim": interpretation.claim,
                        "confidence": interpretation.confidence,
                        "reasoning": interpretation.reasoning,
                        "duration_ms": result.cost_ms,
                        "edges_created": edge_count,
                        "row_count": result.row_count,
                        "effect_size": interpretation.effect_size,
                        "tags": interpretation.tags,
                        "sql": result.sql,
                        "result_preview": result.rows[:5],
                    },
                )
            )

            # Record in frontier for audit trail
            self.store.insert_frontier_item(
                action_type="STRATEGIST",
                payload_json={"table": hypothesis.table, "question": hypothesis.question},
                dedupe_key=f"strategist:{steps_done}:{hypothesis.question[:80]}",
                run_id=run_id,
                thread_id=interpretation.theme,
                score=interpretation.confidence,
                status="done",
            )

        return steps_done, insights_created, errors, status

    def _build_strategist_frontier_hints(self, profiles: list, joins: list) -> str:
        """Build deterministic frontier suggestions to guide strategist breadth."""
        recent_insights = self.store.get_recent_insights(limit=50)
        ctx = GeneratorContext(
            store=self.store,
            profiles=profiles,
            recent_insights=recent_insights,
            join_candidates=joins,
            config=self.config,
        )
        generated = []
        for generator in get_all_generators(Path(".nemo/generators")):
            try:
                generated.extend(generator(ctx))
            except Exception:  # noqa: BLE001
                continue
        deduped = dedupe_frontier(
            generated,
            existing_keys=self.store.get_frontier_existing_keys(),
            recent_insight_keys=derive_recent_insight_keys(recent_insights),
        )
        if not deduped:
            return "(no deterministic suggestions)"
        ranked = score_frontier(deduped, ctx)[:8]
        lines: list[str] = []
        for item in ranked:
            table = str(item.payload.get("table") or "-")
            lines.append(f"- {item.action_type} on {table}: {_target_for_payload(item.payload)}")
        return "\n".join(lines)

    def _load_or_create_notebook(self, run_id: str) -> Notebook:
        raw = self.store.load_notebook(run_id)
        if raw:
            try:
                return Notebook.model_validate(raw)
            except Exception:  # noqa: BLE001
                pass
        return Notebook()

    # ------------------------------------------------------------------
    # Legacy loop (fallback when no LLM client)
    # ------------------------------------------------------------------

    async def _run_legacy_loop(
        self,
        run_id: str,
        profiles: list,
        joins: list,
        memory: dict[str, Any],
        max_steps: int | None,
        max_minutes: float | None,
        plan_only: bool,
        started: float,
    ) -> tuple[int, int, int, str]:
        steps_done = 0
        insights_created = 0
        errors = 0
        status = "completed"

        frontier_meta = self._refresh_frontier(run_id, memory, profiles, joins, plan_only=plan_only)
        await self.bus.emit(
            NemoEvent(type=EventType.FRONTIER_REFRESHED, run_id=run_id, payload=frontier_meta)
        )

        if plan_only:
            return 0, 0, 0, "completed"

        step_budget = max_steps if max_steps is not None else int(self.config.max_steps)
        time_budget_minutes = (
            float(max_minutes) if max_minutes is not None else float(self.config.max_runtime_minutes)
        )

        while steps_done < step_budget:
            elapsed_minutes = (time.perf_counter() - started) / 60.0
            if elapsed_minutes >= time_budget_minutes:
                break
            if is_saturated(self.store, self.config):
                break

            item = select_next(self.store, self.config)
            if item is None:
                break
            steps_done += 1
            self.store.update_frontier_status(item.action_id, "running")
            step_start_event = NemoEvent(
                type=EventType.STEP_STARTED,
                run_id=run_id,
                step_num=steps_done,
                payload={
                    "action": {
                        "action_id": item.action_id,
                        "action_type": item.action_type,
                        "payload": item.payload,
                        "score": item.score,
                    }
                },
            )
            hook_results = await self.bus.emit(step_start_event)
            if _has_blocking_hook(hook_results):
                self.store.update_frontier_status(item.action_id, "skipped", "blocked by hook")
                continue

            try:
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.STEP_PHASE,
                        run_id=run_id, step_num=steps_done,
                        payload={"phase": "compiling"},
                    )
                )
                sql = compile_action(item, profiles, joins)
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.STEP_PHASE,
                        run_id=run_id, step_num=steps_done,
                        payload={"phase": "executing", "sql": sql},
                    )
                )
                result = execute_query(self.store, sql, self.config)
                if result.error:
                    errors += 1
                    self.store.update_frontier_status(item.action_id, "error", result.error)
                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.STEP_ERROR,
                            run_id=run_id, step_num=steps_done,
                            payload={"action_id": item.action_id, "phase": "executing",
                                     "error": result.error, "will_retry": False},
                        )
                    )
                    continue

                await self.bus.emit(
                    NemoEvent(
                        type=EventType.STEP_PHASE,
                        run_id=run_id, step_num=steps_done,
                        payload={"phase": "summarizing"},
                    )
                )
                draft = await summarize_result(
                    action=item, result=result, profiles=profiles,
                    recent_insights=self.store.get_recent_insights(limit=20),
                    config=self.config, client=self._llm_client,
                )
                insight_id = self.store.insert_insight(
                    title=draft.title, question=draft.question,
                    sql=result.sql, result_summary_json=draft.result_summary,
                    claim=draft.claim, run_id=run_id, thread_id=item.thread_id,
                    confidence=draft.confidence, status="ok",
                    hypothesis_struct_json=draft.hypothesis_struct,
                    result_sample_json=draft.result_sample,
                    claim_struct_json=draft.claim_struct,
                    effect_size=draft.effect_size, cost_ms=result.cost_ms,
                    source_tables_json=[str(item.payload.get("table"))] if item.payload.get("table") else [],
                    tags_json=draft.tags,
                )
                insights_created += 1
                full_insight = self.store.get_insight_by_id(insight_id) or {"insight_id": insight_id}
                await self.bus.emit(
                    NemoEvent(type=EventType.INSIGHT_CREATED, run_id=run_id,
                              step_num=steps_done, payload=full_insight)
                )

                await self.bus.emit(
                    NemoEvent(
                        type=EventType.STEP_PHASE,
                        run_id=run_id, step_num=steps_done,
                        payload={"phase": "linking"},
                    )
                )
                edge_count = 0
                for edge in link_insight(self.store, full_insight, self.config):
                    self.store.insert_edge(
                        from_insight_id=edge["from_insight_id"],
                        to_insight_id=edge["to_insight_id"],
                        edge_type=edge["type"],
                        weight=float(edge["weight"]),
                        rationale=str(edge["rationale"]),
                    )
                    edge_count += 1

                clusters = find_contradiction_clusters(self.store)
                if clusters:
                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.CONTRADICTION_DETECTED,
                            run_id=run_id, step_num=steps_done,
                            payload={"cluster": clusters[0]},
                        )
                    )
                self.store.update_frontier_status(item.action_id, "done")
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.STEP_COMPLETED,
                        run_id=run_id, step_num=steps_done,
                        payload={
                            "insight_id": insight_id,
                            "title": draft.title,
                            "question": draft.question,
                            "claim": draft.claim,
                            "confidence": draft.confidence,
                            "duration_ms": result.cost_ms,
                            "edges_created": edge_count,
                            "row_count": result.row_count,
                            "effect_size": draft.effect_size,
                            "tags": draft.tags,
                            "sql": result.sql,
                            "result_preview": result.rows[:5],
                        },
                    )
                )
                if steps_done % max(1, int(self.config.reflect_every)) == 0:
                    updated_memory = load_working_memory(self.store, self.config, run_id)
                    frontier_meta = self._refresh_frontier(
                        run_id, updated_memory, profiles, joins, plan_only=False,
                    )
                    await self.bus.emit(
                        NemoEvent(type=EventType.FRONTIER_REFRESHED, run_id=run_id, payload=frontier_meta)
                    )
            except KeyboardInterrupt:
                status = "interrupted"
                break
            except Exception as exc:  # noqa: BLE001
                errors += 1
                self.store.update_frontier_status(item.action_id, "error", str(exc))
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.STEP_ERROR,
                        run_id=run_id, step_num=steps_done,
                        payload={"action_id": item.action_id, "phase": "unknown",
                                 "error": str(exc), "will_retry": False},
                    )
                )

        return steps_done, insights_created, errors, status

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _refresh_frontier(
        self, run_id: str, memory: dict[str, Any],
        profiles: list, joins: list,
        plan_only: bool = False,
    ) -> dict[str, Any]:
        ctx = GeneratorContext(
            store=self.store, profiles=profiles,
            recent_insights=memory["recent_insights"],
            join_candidates=joins, config=self.config,
        )
        generated = []
        for generator in get_all_generators(Path(".nemo/generators")):
            try:
                generated.extend(generator(ctx))
            except Exception:  # noqa: BLE001
                continue
        deduped = dedupe_frontier(
            generated,
            existing_keys=self.store.get_frontier_existing_keys(),
            recent_insight_keys=derive_recent_insight_keys(memory["recent_insights"]),
        )
        scored = score_frontier(deduped, ctx)
        for item in scored:
            self.store.insert_frontier_item(
                action_type=item.action_type, payload_json=item.payload,
                dedupe_key=item.dedupe_key, run_id=run_id,
                thread_id=item.thread_id, score=item.score,
                status="queued", last_error=item.last_error,
                depends_on_action_id=item.depends_on_action_id,
            )
        top_score = scored[0].score if scored else 0.0
        top_actions = []
        for ranked, item in enumerate(scored[:15], start=1):
            top_actions.append({
                "rank": ranked, "score": float(item.score),
                "type": item.action_type,
                "table": str(item.payload.get("table") or ""),
                "target": _target_for_payload(item.payload),
                "rationale": item.rationale,
            })
        return {
            "generated": len(generated), "after_dedupe": len(deduped),
            "after_score": len(scored), "top_score": top_score,
            "plan_only": plan_only, "top_actions": top_actions,
        }

    def _stats_payload(self, steps_done: int, insights_created: int, errors: int, started: float) -> dict[str, Any]:
        return {
            "steps": steps_done, "insights_created": insights_created,
            "errors": errors, "duration_ms": int((time.perf_counter() - started) * 1000),
            "frontier_remaining": self.store.count_frontier(status="queued"),
        }


def _has_blocking_hook(raw_results: list[Any]) -> bool:
    for item in raw_results:
        if isinstance(item, HookResult):
            if item.blocked:
                return True
        elif isinstance(item, list):
            for nested in item:
                if isinstance(nested, HookResult) and nested.blocked:
                    return True
                if isinstance(nested, dict) and nested.get("blocked"):
                    return True
        elif isinstance(item, dict) and item.get("blocked"):
            return True
    return False


def _target_for_payload(payload: dict[str, Any]) -> str:
    table = str(payload.get("table") or "")
    metric = str(payload.get("metric_col") or "")
    dim = str(payload.get("dimension_col") or payload.get("group_col") or "")
    if metric and dim:
        return f"{metric} by {dim}"
    if metric:
        return metric
    if table:
        return table
    return "-"


def _build_coverage_context(notebook: Notebook, all_tables: list[str], max_steps_per_theme: int) -> str:
    touched = sorted({t for entry in notebook.entries for t in entry.tables_touched if t})
    untouched = sorted([t for t in all_tables if t not in set(touched)])
    deepest_theme = None
    if notebook.entries:
        deepest = max(notebook.entries, key=lambda e: e.step_count)
        deepest_theme = f"{deepest.theme} ({deepest.step_count} steps)"
    return (
        f"Total tables: {len(all_tables)}\n"
        f"Tables explored: {', '.join(touched) if touched else '(none)'}\n"
        f"Tables not yet explored: {', '.join(untouched) if untouched else '(none)'}\n"
        f"Deepest theme: {deepest_theme or '(none)'}\n"
        f"Preferred max depth per theme before pivot: {max_steps_per_theme}"
    )


def _normalize_tokens(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    stopwords = {
        "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "by", "with",
        "is", "are", "was", "were", "be", "this", "that", "how", "what", "which",
        "do", "does", "did", "from", "over", "under", "month", "monthly",
    }
    return {tok for tok in tokens if tok and tok not in stopwords}


def _jaccard_similarity(left: str, right: str) -> float:
    a = _normalize_tokens(left)
    b = _normalize_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _overlap_similarity(left: str, right: str) -> float:
    a = _normalize_tokens(left)
    b = _normalize_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / float(min(len(a), len(b)))


def _is_duplicate_question(question: str, recent_questions: list[str], threshold: float) -> bool:
    if not recent_questions:
        return False
    return any(_jaccard_similarity(question, prior) >= threshold for prior in recent_questions[-8:])


def _is_duplicate_claim(claim: str, recent_claims: list[str]) -> bool:
    if not recent_claims:
        return False
    return any(
        max(_jaccard_similarity(claim, prior), _overlap_similarity(claim, prior)) >= 0.6
        for prior in recent_claims[-5:]
    )
