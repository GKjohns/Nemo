"""Nemo exploration engine — agent-driven and legacy orchestration."""

from __future__ import annotations

import asyncio
import json
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

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
    classify_validation_evidence,
    dedupe_frontier,
    derive_recent_insight_keys,
    get_all_generators,
    is_saturated,
    plan_validation_step,
    rerank_frontier,
    render_verdict,
    score_frontier,
    select_next,
    should_render_verdict,
)
from nemo.planner.arbiter import decide_phase, should_consult_arbiter
from nemo.planner.models import (
    DuplicateCheck,
    EvidenceLink,
    FrontierItem,
    HypothesisRecord,
    PhaseDecision,
)
from nemo.planner.strategist import (
    Hypothesis,
    InterpretationResult,
    Notebook,
    apply_notebook_update,
    build_schema_context,
    interpret_and_update,
    plan_next_step,
)
from nemo.report.brief import generate_run_debrief
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

            learning_count, thread_count = self._safe_post_run_updates(run_id)
            debrief = await self._generate_debrief(run_id, steps_done, insights_created, errors, started)
            await self._emit_run_terminal_event(
                run_id=run_id,
                status=status,
                steps_done=steps_done,
                insights_created=insights_created,
                errors=errors,
                started=started,
                learnings_recorded=learning_count,
                thread_cards_updated=thread_count,
                debrief=debrief,
            )
            self._persist_run_completion(
                run_id=run_id,
                status=status,
                steps_done=steps_done,
                insights_created=insights_created,
                errors=errors,
            )
            return run_id

        except KeyboardInterrupt:
            status = "interrupted"
            learning_count, thread_count = self._safe_post_run_updates(run_id)
            debrief = await self._generate_debrief(run_id, steps_done, insights_created, errors, started)
            await self._emit_run_terminal_event(
                run_id=run_id,
                status=status,
                steps_done=steps_done,
                insights_created=insights_created,
                errors=errors,
                started=started,
                learnings_recorded=learning_count,
                thread_cards_updated=thread_count,
                debrief=debrief,
            )
            self._persist_run_completion(
                run_id=run_id,
                status=status,
                steps_done=steps_done,
                insights_created=insights_created,
                errors=errors,
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
        hypotheses = self._load_hypotheses(run_id)
        all_tables = [p.name for p in profiles]
        recent_questions: list[str] = []
        recent_claims: list[str] = []
        stagnation_count = 0
        force_diversify = False
        recent_phases: list[PhaseDecision] = []
        current_decision: PhaseDecision | None = None
        last_arbiter_step = 0
        significant_event = False

        step_budget = max_steps if max_steps is not None else int(self.config.max_steps)
        time_budget_minutes = (
            float(max_minutes) if max_minutes is not None else float(self.config.max_runtime_minutes)
        )

        if plan_only:
            coverage_context = _build_coverage_context(notebook, all_tables, self.config.max_steps_per_theme)
            frontier_hints = self._build_strategist_frontier_hints(notebook, profiles, joins)
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

            next_step_num = steps_done + 1
            if should_consult_arbiter(
                step_num=next_step_num,
                last_arbiter_step=last_arbiter_step,
                arbiter_interval=int(self.config.arbiter_interval),
                significant_event=significant_event,
            ):
                current_decision = await decide_phase(
                    notebook=notebook,
                    hypotheses=hypotheses,
                    all_tables=all_tables,
                    steps_done=steps_done,
                    budget=step_budget,
                    recent_phases=recent_phases,
                    config=self.config,
                    client=self._llm_client,
                    elapsed_minutes=elapsed_minutes,
                    time_budget_minutes=time_budget_minutes,
                )
                recent_phases = (recent_phases + [current_decision])[-10:]
                last_arbiter_step = next_step_num
                significant_event = False
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.PHASE_DECIDED,
                        run_id=run_id,
                        step_num=next_step_num,
                        payload={
                            "phase": current_decision.phase,
                            "hypothesis_id": current_decision.hypothesis_id,
                            "reasoning": current_decision.reasoning,
                        },
                    )
                )

            if current_decision is None:
                current_decision = PhaseDecision(
                    phase="explore",
                    reasoning="Starting exploration with no prior phase decision.",
                )

            steps_done += 1

            hypothesis, validation_target, current_decision, recent_questions, planning_errors = (
                await self._plan_strategist_step(
                    run_id=run_id,
                    step_num=steps_done,
                    current_decision=current_decision,
                    notebook=notebook,
                    schema_ctx=schema_ctx,
                    profiles=profiles,
                    joins=joins,
                    all_tables=all_tables,
                    hypotheses=hypotheses,
                    recent_questions=recent_questions,
                    force_diversify=force_diversify,
                )
            )
            errors += planning_errors
            if hypothesis is None:
                continue

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
                        "phase": current_decision.phase,
                        "hypothesis_id": validation_target.hypothesis_id if validation_target else None,
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
                            "phase": current_decision.phase,
                            "hypothesis_id": validation_target.hypothesis_id if validation_target else None,
                        },
                    },
                )
            )

            hypothesis, result, execute_errors, should_skip = await self._execute_strategist_step(
                run_id=run_id,
                step_num=steps_done,
                hypothesis=hypothesis,
                notebook=notebook,
                schema_ctx=schema_ctx,
            )
            errors += execute_errors
            if should_skip:
                continue

            interpretation, interpret_errors = await self._interpret_strategist_step(
                run_id=run_id,
                step_num=steps_done,
                hypothesis=hypothesis,
                result=result,
                notebook=notebook,
                schema_ctx=schema_ctx,
            )
            errors += interpret_errors
            if interpretation is None:
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

            proposed_claim = (interpretation.proposed_hypothesis or "").strip()
            if proposed_claim:
                proposed_tables = sorted({
                    *source_tables,
                    *[
                        str(tag)
                        for tag in (interpretation.tags or [])
                        if "." not in str(tag) and str(tag).isidentifier()
                    ],
                })
                hypothesis_record = HypothesisRecord(
                    claim=proposed_claim,
                    source_insight_id=insight_id,
                    initial_confidence=(
                        interpretation.hypothesis_confidence
                        if interpretation.hypothesis_confidence is not None
                        else interpretation.confidence
                    ),
                    priority=float(
                        interpretation.hypothesis_confidence
                        if interpretation.hypothesis_confidence is not None
                        else interpretation.confidence
                    ),
                    tables_involved=proposed_tables,
                )
                hypotheses.append(hypothesis_record)
                self.store.save_hypothesis(run_id, hypothesis_record.model_dump(mode="json"))
                significant_event = True
                await self.bus.emit(
                    NemoEvent(
                        type=EventType.HYPOTHESIS_PROPOSED,
                        run_id=run_id,
                        step_num=steps_done,
                        payload={
                            "hypothesis_id": hypothesis_record.hypothesis_id,
                            "claim": hypothesis_record.claim,
                            "source_insight_id": hypothesis_record.source_insight_id,
                            "initial_confidence": hypothesis_record.initial_confidence,
                            "status": hypothesis_record.status,
                            "priority": hypothesis_record.priority,
                            "tables_involved": hypothesis_record.tables_involved,
                        },
                    )
                )

            if validation_target is not None:
                relationship = classify_validation_evidence(
                    f"{interpretation.claim}\n{interpretation.reasoning}"
                )
                validation_target.evidence_chain.append(
                    EvidenceLink(
                        insight_id=insight_id,
                        relationship=relationship,
                        note=interpretation.claim,
                    )
                )
                validation_target.validation_step += 1
                validation_target.updated_at = datetime.now(tz=timezone.utc)

                await self.bus.emit(
                    NemoEvent(
                        type=EventType.VALIDATION_STEP,
                        run_id=run_id,
                        step_num=steps_done,
                        payload={
                            "hypothesis_id": validation_target.hypothesis_id,
                            "claim": validation_target.claim,
                            "validation_step": validation_target.validation_step,
                            "max_validation_steps": int(self.config.max_validation_steps),
                            "relationship": relationship,
                            "evidence_count": len(validation_target.evidence_chain),
                        },
                    )
                )

                if should_render_verdict(
                    validation_target,
                    max_validation_steps=int(self.config.max_validation_steps),
                    latest_relationship=relationship,
                ):
                    verdict_status, verdict_confidence, verdict_text = await render_verdict(
                        hypothesis=validation_target,
                        evidence_chain=[item.model_dump() for item in validation_target.evidence_chain],
                        config=self.config,
                        client=self._llm_client,
                    )
                    validation_target.status = verdict_status
                    validation_target.verdict_confidence = verdict_confidence
                    validation_target.verdict = verdict_text
                    significant_event = True
                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.HYPOTHESIS_VERDICT,
                            run_id=run_id,
                            step_num=steps_done,
                            payload={
                                "hypothesis_id": validation_target.hypothesis_id,
                                "status": verdict_status,
                                "verdict_confidence": verdict_confidence,
                                "verdict": verdict_text,
                                "evidence_count": len(validation_target.evidence_chain),
                            },
                        )
                    )

                self.store.save_hypothesis(run_id, validation_target.model_dump(mode="json"))

            # --- LINK ---
            await self._emit_step_phase(run_id, steps_done, "linking")
            edge_count = 0
            for edge in link_insight(self.store, full_insight, self.config, self._llm_client):
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

            if await is_semantically_duplicate(
                interpretation.claim,
                recent_claims[-8:],
                self.config,
                self._llm_client,
                mode="claim",
            ):
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
            await self._emit_step_completed(
                run_id,
                steps_done,
                {
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

    async def _plan_strategist_step(
        self,
        *,
        run_id: str,
        step_num: int,
        current_decision: PhaseDecision,
        notebook: Notebook,
        schema_ctx: str,
        profiles: list,
        joins: list,
        all_tables: list[str],
        hypotheses: list[HypothesisRecord],
        recent_questions: list[str],
        force_diversify: bool,
    ) -> tuple[Hypothesis | None, HypothesisRecord | None, PhaseDecision, list[str], int]:
        coverage_context = _build_coverage_context(notebook, all_tables, self.config.max_steps_per_theme)
        frontier_hints = self._build_strategist_frontier_hints(notebook, profiles, joins)
        validation_target: HypothesisRecord | None = None

        if current_decision.phase == "explore":
            planning_feedback_parts: list[str] = []
            if force_diversify:
                planning_feedback_parts.append(
                    "The prior steps are showing diminishing returns. "
                    "Choose a materially different investigation touching a different table/theme."
                )
            planning_feedback = "\n".join(planning_feedback_parts) if planning_feedback_parts else None
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
                await self._emit_step_error(run_id, step_num, "planning", str(exc), will_retry=False)
                return None, None, current_decision, recent_questions, 1

            if await is_semantically_duplicate(
                hypothesis.question,
                recent_questions[-8:],
                self.config,
                self._llm_client,
                mode="question",
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
            return hypothesis, None, current_decision, recent_questions, 0

        if current_decision.hypothesis_id:
            validation_target = next(
                (h for h in hypotheses if h.hypothesis_id == current_decision.hypothesis_id),
                None,
            )
        if validation_target is None:
            candidates = [h for h in hypotheses if h.status in {"proposed", "testing"}]
            validation_target = max(candidates, key=lambda h: float(h.priority)) if candidates else None
        if validation_target is None:
            return (
                None,
                None,
                PhaseDecision(
                    phase="explore",
                    reasoning="No eligible hypothesis found for exploit; continuing explore.",
                ),
                recent_questions,
                0,
            )

        if validation_target.status == "proposed":
            validation_target.status = "testing"
        validation_target.updated_at = datetime.now(tz=timezone.utc)
        try:
            hypothesis = await plan_validation_step(
                hypothesis=validation_target,
                notebook=notebook,
                schema_context=schema_ctx,
                config=self.config,
                client=self._llm_client,
            )
        except Exception as exc:  # noqa: BLE001
            await self._emit_step_error(
                run_id,
                step_num,
                "validation_planning",
                str(exc),
                will_retry=False,
            )
            return None, None, current_decision, recent_questions, 1
        return hypothesis, validation_target, current_decision, recent_questions, 0

    async def _execute_strategist_step(
        self,
        *,
        run_id: str,
        step_num: int,
        hypothesis: Hypothesis,
        notebook: Notebook,
        schema_ctx: str,
    ) -> tuple[Hypothesis, Any, int, bool]:
        await self._emit_step_phase(run_id, step_num, "executing", sql=hypothesis.sql)
        result = execute_query(self.store, hypothesis.sql, self.config)

        if result.error:
            try:
                hypothesis = await plan_next_step(
                    notebook,
                    schema_ctx,
                    self.config,
                    self._llm_client,
                    error_context={"error": result.error, "sql": hypothesis.sql},
                )
                result = execute_query(self.store, hypothesis.sql, self.config)
            except Exception:  # noqa: BLE001
                pass

        if not result.error:
            return hypothesis, result, 0, False

        self.store.insert_learning(
            run_id=run_id,
            category="error_pattern",
            subject=f"STRATEGIST:{hypothesis.table}",
            detail=result.error,
            confidence=0.7,
        )
        await self._emit_step_error(run_id, step_num, "executing", result.error, will_retry=False)
        return hypothesis, result, 1, True

    async def _interpret_strategist_step(
        self,
        *,
        run_id: str,
        step_num: int,
        hypothesis: Hypothesis,
        result: Any,
        notebook: Notebook,
        schema_ctx: str,
    ) -> tuple[InterpretationResult | None, int]:
        await self._emit_step_phase(run_id, step_num, "interpreting")
        try:
            interpretation = await interpret_and_update(
                hypothesis,
                result,
                notebook,
                schema_ctx,
                self.config,
                self._llm_client,
            )
        except Exception as exc:  # noqa: BLE001
            await self._emit_step_error(run_id, step_num, "interpreting", str(exc), will_retry=False)
            return None, 1
        return interpretation, 0

    def _build_frontier_candidates(
        self,
        profiles: list,
        joins: list,
        recent_insights: list[dict[str, Any]],
    ) -> tuple[GeneratorContext, list[FrontierItem], list[FrontierItem], list[FrontierItem]]:
        ctx = GeneratorContext(
            store=self.store,
            profiles=profiles,
            recent_insights=recent_insights,
            join_candidates=joins,
            config=self.config,
        )
        generated: list[FrontierItem] = []
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
        scored = score_frontier(deduped, ctx)
        return ctx, generated, deduped, scored

    def _build_strategist_frontier_hints(self, notebook: Notebook, profiles: list, joins: list) -> str:
        """Build frontier suggestions to guide strategist breadth."""
        recent_insights = self.store.get_recent_insights(limit=50)
        ctx, _, deduped, scored = self._build_frontier_candidates(profiles, joins, recent_insights)
        if not deduped:
            return "(no deterministic suggestions)"
        ranked, _ = rerank_frontier(scored, notebook, self.config, self._llm_client, top_n=8)
        ranked = ranked[:8]
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

    def _load_hypotheses(self, run_id: str) -> list[HypothesisRecord]:
        rows = self.store.load_hypotheses(run_id)
        loaded: list[HypothesisRecord] = []
        for row in rows:
            try:
                loaded.append(HypothesisRecord.from_store_row(row))
            except Exception:  # noqa: BLE001
                continue
        return loaded

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
                await self._emit_step_phase(run_id, steps_done, "compiling")
                sql = compile_action(item, profiles, joins)
                await self._emit_step_phase(run_id, steps_done, "executing", sql=sql)
                result = execute_query(self.store, sql, self.config)
                if result.error:
                    errors += 1
                    self.store.update_frontier_status(item.action_id, "error", result.error)
                    await self._emit_step_error(
                        run_id,
                        steps_done,
                        "executing",
                        result.error,
                        will_retry=False,
                        action_id=item.action_id,
                    )
                    continue

                await self._emit_step_phase(run_id, steps_done, "summarizing")
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

                await self._emit_step_phase(run_id, steps_done, "linking")
                edge_count = 0
                for edge in link_insight(self.store, full_insight, self.config, self._llm_client):
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
                await self._emit_step_completed(
                    run_id,
                    steps_done,
                    {
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
                await self._emit_step_error(
                    run_id,
                    steps_done,
                    "unknown",
                    str(exc),
                    will_retry=False,
                    action_id=item.action_id,
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
        ctx, generated, deduped, scored = self._build_frontier_candidates(
            profiles,
            joins,
            memory["recent_insights"],
        )
        notebook = self._load_or_create_notebook(run_id)
        reranked, deterministic_top = rerank_frontier(
            scored,
            notebook,
            self.config,
            self._llm_client,
            top_n=8,
        )
        for item in reranked:
            self.store.insert_frontier_item(
                action_type=item.action_type, payload_json=item.payload,
                dedupe_key=item.dedupe_key, run_id=run_id,
                thread_id=item.thread_id, score=item.score,
                status="queued", last_error=item.last_error,
                depends_on_action_id=item.depends_on_action_id,
            )
        top_score = reranked[0].score if reranked else 0.0
        top_actions_deterministic = []
        for ranked, item in enumerate(deterministic_top[:8], start=1):
            top_actions_deterministic.append({
                "rank": ranked,
                "score": float(item.score),
                "type": item.action_type,
                "table": str(item.payload.get("table") or ""),
                "target": _target_for_payload(item.payload),
            })
        top_actions = []
        for ranked, item in enumerate(reranked[:15], start=1):
            top_actions.append({
                "rank": ranked, "score": float(item.score),
                "type": item.action_type,
                "table": str(item.payload.get("table") or ""),
                "target": _target_for_payload(item.payload),
                "rationale": item.rationale,
            })
        return {
            "generated": len(generated), "after_dedupe": len(deduped),
            "after_score": len(reranked), "top_score": top_score,
            "plan_only": plan_only, "top_actions": top_actions,
            "top_actions_deterministic": top_actions_deterministic,
        }

    def _stats_payload(self, steps_done: int, insights_created: int, errors: int, started: float) -> dict[str, Any]:
        return {
            "steps": steps_done, "insights_created": insights_created,
            "errors": errors, "duration_ms": int((time.perf_counter() - started) * 1000),
            "frontier_remaining": self.store.count_frontier(status="queued"),
        }

    async def _emit_step_phase(self, run_id: str, step_num: int, phase: str, **payload: Any) -> None:
        phase_payload = {"phase": phase}
        phase_payload.update(payload)
        await self.bus.emit(
            NemoEvent(
                type=EventType.STEP_PHASE,
                run_id=run_id,
                step_num=step_num,
                payload=phase_payload,
            )
        )

    async def _emit_step_error(
        self,
        run_id: str,
        step_num: int,
        phase: str,
        error: str,
        *,
        will_retry: bool,
        action_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"phase": phase, "error": error, "will_retry": will_retry}
        if action_id is not None:
            payload["action_id"] = action_id
        await self.bus.emit(
            NemoEvent(
                type=EventType.STEP_ERROR,
                run_id=run_id,
                step_num=step_num,
                payload=payload,
            )
        )

    async def _emit_step_completed(self, run_id: str, step_num: int, payload: dict[str, Any]) -> None:
        await self.bus.emit(
            NemoEvent(
                type=EventType.STEP_COMPLETED,
                run_id=run_id,
                step_num=step_num,
                payload=payload,
            )
        )

    def _safe_post_run_updates(self, run_id: str) -> tuple[int, int]:
        learning_count = 0
        thread_count = 0
        if self.config.use_learnings:
            try:
                learning_count = len(record_learnings(self.store, run_id))
            except Exception:  # noqa: BLE001
                learning_count = 0
        try:
            thread_count = len(update_thread_cards(self.store, self.config))
        except Exception:  # noqa: BLE001
            thread_count = 0
        return learning_count, thread_count

    async def _generate_debrief(
        self,
        run_id: str,
        steps_done: int,
        insights_created: int,
        errors: int,
        started: float,
    ) -> str:
        """Generate an end-of-run narrative debrief and persist it."""
        notebook = self._load_or_create_notebook(run_id)
        hypotheses = self._load_hypotheses(run_id)
        stats = self._stats_payload(steps_done, insights_created, errors, started)
        goal = getattr(self.config, "goal", "") or ""
        try:
            debrief = await generate_run_debrief(
                notebook=notebook,
                hypotheses=hypotheses,
                stats=stats,
                goal=goal,
                client=self._llm_client,
            )
        except Exception:  # noqa: BLE001
            debrief = ""
        if debrief:
            try:
                self.store.save_debrief(run_id, debrief)
            except Exception:  # noqa: BLE001
                pass
        return debrief

    async def _emit_run_terminal_event(
        self,
        *,
        run_id: str,
        status: str,
        steps_done: int,
        insights_created: int,
        errors: int,
        started: float,
        learnings_recorded: int,
        thread_cards_updated: int,
        debrief: str = "",
    ) -> None:
        stats = self._stats_payload(steps_done, insights_created, errors, started)
        payload = {
            "stats": stats,
            "learnings_recorded": learnings_recorded,
            "thread_cards_updated": thread_cards_updated,
            "debrief": debrief,
        }
        if status == "interrupted":
            payload["reason"] = "signal"
            event_type = EventType.RUN_INTERRUPTED
        else:
            event_type = EventType.RUN_COMPLETED
        await self.bus.emit(NemoEvent(type=event_type, run_id=run_id, payload=payload))

    def _persist_run_completion(
        self,
        *,
        run_id: str,
        status: str,
        steps_done: int,
        insights_created: int,
        errors: int,
    ) -> None:
        self.store.update_run(
            run_id,
            status=status,
            steps_completed=steps_done,
            insights_created=insights_created,
            errors=errors,
            frontier_size=self.store.count_frontier(status="queued"),
            ended=True,
        )


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


async def is_semantically_duplicate(
    new_text: str,
    candidates: list[str],
    config: NemoConfig,
    client: OpenAI | None,
    *,
    mode: str = "generic",
) -> bool:
    """Classify semantic duplication using an LLM, with lexical fallback."""
    candidate_texts = [item.strip() for item in candidates if item and item.strip()]
    text = new_text.strip()
    if not text or not candidate_texts:
        return False

    if client is not None:
        payload = {
            "mode": mode,
            "new_text": text,
            "candidates": [
                {"index": idx, "text": candidate}
                for idx, candidate in enumerate(candidate_texts)
            ],
        }
        instructions = (
            "Determine whether NEW_TEXT is semantically equivalent to any candidate text. "
            "Treat paraphrases and synonym substitutions as duplicates when analytical intent is the same. "
            "Return is_duplicate=true if ANY candidate is effectively the same question/claim; otherwise false. "
            "Keep reasoning concise."
        )
        for attempt in range(3):
            try:
                response = client.responses.parse(
                    model="gpt-5-nano",
                    instructions=instructions,
                    input=json.dumps(payload),
                    text_format=DuplicateCheck,
                )
                parsed = response.output_parsed
                if parsed is not None:
                    return bool(parsed.is_duplicate)
                raise RuntimeError("duplicate check parse produced no output")
            except (APIConnectionError, APITimeoutError):
                if attempt == 2:
                    break
                await asyncio.sleep(2**attempt)
            except RateLimitError:
                if attempt == 2:
                    break
                await asyncio.sleep(5 * (attempt + 1))
            except Exception:  # noqa: BLE001
                break

    if mode == "question":
        threshold = float(config.question_similarity_threshold)
        return _is_duplicate_question(text, candidate_texts, threshold=threshold)
    return _is_duplicate_claim(text, candidate_texts)
