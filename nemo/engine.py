"""Nemo exploration engine and working-memory orchestration."""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

from nemo.config import NemoConfig
from nemo.events import EventBus, EventType, NemoEvent
from nemo.executor import compile_action, execute_query
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
from nemo.store import NemoStore
from nemo.summarize import summarize_result
from nemo.summarize.summarize import make_client


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
    """Outer loop implementation for plan/run/resume workflows."""

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
            frontier_meta = self._refresh_frontier(run_id, memory, plan_only=plan_only)
            await self.bus.emit(
                NemoEvent(type=EventType.FRONTIER_REFRESHED, run_id=run_id, payload=frontier_meta)
            )

            if plan_only:
                stats = self._stats_payload(steps_done, insights_created, errors, started)
                await self.bus.emit(NemoEvent(type=EventType.RUN_COMPLETED, run_id=run_id, payload={"stats": stats}))
                self.store.update_run(
                    run_id,
                    status="completed",
                    steps_completed=0,
                    insights_created=0,
                    errors=0,
                    frontier_size=self.store.count_frontier(status="queued"),
                    notes="plan_only",
                    ended=True,
                )
                return run_id

            step_budget = max_steps if max_steps is not None else int(self.config.max_steps)
            time_budget_minutes = (
                float(max_minutes) if max_minutes is not None else float(self.config.max_runtime_minutes)
            )
            while steps_done < step_budget:
                elapsed_minutes = (time.perf_counter() - started) / 60.0
                if elapsed_minutes >= time_budget_minutes:
                    break

                if is_saturated(self.store, self.config):
                    top_score = self._top_frontier_score()
                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.FRONTIER_SATURATED,
                            run_id=run_id,
                            payload={"top_score": top_score, "threshold": self.config.saturation_threshold},
                        )
                    )
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
                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.STEP_SKIPPED,
                            run_id=run_id,
                            step_num=steps_done,
                            payload={"action_id": item.action_id, "reason": "hook blocked"},
                        )
                    )
                    continue

                try:
                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.STEP_PHASE,
                            run_id=run_id,
                            step_num=steps_done,
                            payload={"phase": "compiling"},
                        )
                    )
                    profiles = profile_all(self.store)
                    joins = discover_joins(self.store, profiles)
                    sql = compile_action(item, profiles, joins)

                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.STEP_PHASE,
                            run_id=run_id,
                            step_num=steps_done,
                            payload={"phase": "executing", "sql": sql},
                        )
                    )
                    result = execute_query(self.store, sql, self.config)
                    if result.error:
                        errors += 1
                        self.store.update_frontier_status(item.action_id, "error", result.error)
                        table_name = str(item.payload.get("table", ""))
                        subject = f"{item.action_type}:{table_name}"
                        self.store.insert_learning(
                            run_id=run_id,
                            category="error_pattern",
                            subject=subject,
                            detail=result.error,
                            confidence=0.7,
                        )
                        if "timeout" in result.error.lower():
                            self.store.insert_learning(
                                run_id=run_id,
                                category="query_timeout",
                                subject=subject,
                                detail=result.error,
                                confidence=0.8,
                            )
                        await self.bus.emit(
                            NemoEvent(
                                type=EventType.STEP_ERROR,
                                run_id=run_id,
                                step_num=steps_done,
                                payload={
                                    "action_id": item.action_id,
                                    "phase": "executing",
                                    "error": result.error,
                                    "will_retry": False,
                                },
                            )
                        )
                        continue

                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.STEP_PHASE,
                            run_id=run_id,
                            step_num=steps_done,
                            payload={"phase": "summarizing"},
                        )
                    )
                    draft = await summarize_result(
                        action=item,
                        result=result,
                        profiles=profiles,
                        recent_insights=self.store.get_recent_insights(limit=20),
                        config=self.config,
                        client=self._llm_client,
                    )
                    insight_id = self.store.insert_insight(
                        title=draft.title,
                        question=draft.question,
                        sql=result.sql,
                        result_summary_json=draft.result_summary,
                        claim=draft.claim,
                        run_id=run_id,
                        thread_id=item.thread_id,
                        confidence=draft.confidence,
                        status="ok",
                        hypothesis_struct_json=draft.hypothesis_struct,
                        result_sample_json=draft.result_sample,
                        claim_struct_json=draft.claim_struct,
                        effect_size=draft.effect_size,
                        cost_ms=result.cost_ms,
                        source_tables_json=[str(item.payload.get("table"))] if item.payload.get("table") else [],
                        tags_json=draft.tags,
                    )
                    insights_created += 1
                    full_insight = self.store.get_insight_by_id(insight_id) or {"insight_id": insight_id}
                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.INSIGHT_CREATED,
                            run_id=run_id,
                            step_num=steps_done,
                            payload=full_insight,
                        )
                    )

                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.STEP_PHASE,
                            run_id=run_id,
                            step_num=steps_done,
                            payload={"phase": "linking"},
                        )
                    )
                    edge_count = 0
                    for edge in link_insight(self.store, full_insight, self.config):
                        edge_id = self.store.insert_edge(
                            from_insight_id=edge["from_insight_id"],
                            to_insight_id=edge["to_insight_id"],
                            edge_type=edge["type"],
                            weight=float(edge["weight"]),
                            rationale=str(edge["rationale"]),
                        )
                        edge_count += 1
                        full_edge = self.store.get_edge_by_id(edge_id) or {"edge_id": edge_id, **edge}
                        await self.bus.emit(
                            NemoEvent(
                                type=EventType.EDGE_CREATED,
                                run_id=run_id,
                                step_num=steps_done,
                                payload=full_edge,
                            )
                        )

                    clusters = find_contradiction_clusters(self.store)
                    if clusters:
                        await self.bus.emit(
                            NemoEvent(
                                type=EventType.CONTRADICTION_DETECTED,
                                run_id=run_id,
                                step_num=steps_done,
                                payload={"cluster": clusters[0]},
                            )
                        )
                    self.store.update_frontier_status(item.action_id, "done")
                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.STEP_COMPLETED,
                            run_id=run_id,
                            step_num=steps_done,
                            payload={
                                "insight_id": insight_id,
                                "claim": draft.claim,
                                "confidence": draft.confidence,
                                "duration_ms": result.cost_ms,
                                "edges_created": edge_count,
                                "sql": result.sql,
                                "result_preview": result.rows[:5],
                            },
                        )
                    )
                    if steps_done % max(1, int(self.config.reflect_every)) == 0:
                        frontier_meta = self._refresh_frontier(
                            run_id,
                            load_working_memory(self.store, self.config, run_id),
                            plan_only=False,
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
                    self.store.insert_learning(
                        run_id=run_id,
                        category="error_pattern",
                        subject=f"{item.action_type}:{item.payload.get('table', '')}",
                        detail=str(exc),
                        confidence=0.7,
                    )
                    await self.bus.emit(
                        NemoEvent(
                            type=EventType.STEP_ERROR,
                            run_id=run_id,
                            step_num=steps_done,
                            payload={"action_id": item.action_id, "phase": "unknown", "error": str(exc), "will_retry": False},
                        )
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
                run_id,
                status=status,
                steps_completed=steps_done,
                insights_created=insights_created,
                errors=errors,
                frontier_size=self.store.count_frontier(status="queued"),
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
                run_id,
                status="error",
                steps_completed=steps_done,
                insights_created=insights_created,
                errors=errors + 1,
                frontier_size=self.store.count_frontier(status="queued"),
                notes=str(exc),
                ended=True,
            )
            raise

    def _refresh_frontier(self, run_id: str, memory: dict[str, Any], plan_only: bool = False) -> dict[str, Any]:
        profiles = profile_all(self.store)
        joins = discover_joins(self.store, profiles)
        ctx = GeneratorContext(
            store=self.store,
            profiles=profiles,
            recent_insights=memory["recent_insights"],
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
            recent_insight_keys=derive_recent_insight_keys(memory["recent_insights"]),
        )
        scored = score_frontier(deduped, ctx)
        for item in scored:
            self.store.insert_frontier_item(
                action_type=item.action_type,
                payload_json=item.payload,
                dedupe_key=item.dedupe_key,
                run_id=run_id,
                thread_id=item.thread_id,
                score=item.score,
                status="queued",
                last_error=item.last_error,
                depends_on_action_id=item.depends_on_action_id,
            )
        top_score = scored[0].score if scored else 0.0
        top_actions = []
        for ranked, item in enumerate(scored[:15], start=1):
            table_name = str(item.payload.get("table") or "")
            target = _target_for_payload(item.payload)
            top_actions.append(
                {
                    "rank": ranked,
                    "score": float(item.score),
                    "type": item.action_type,
                    "table": table_name,
                    "target": target,
                    "rationale": item.rationale,
                }
            )
        return {
            "generated": len(generated),
            "after_dedupe": len(deduped),
            "after_score": len(scored),
            "top_score": top_score,
            "plan_only": plan_only,
            "top_actions": top_actions,
        }

    def _stats_payload(
        self,
        steps_done: int,
        insights_created: int,
        errors: int,
        started: float,
    ) -> dict[str, Any]:
        return {
            "steps": steps_done,
            "insights_created": insights_created,
            "errors": errors,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "frontier_remaining": self.store.count_frontier(status="queued"),
        }

    def _top_frontier_score(self) -> float:
        rows = self.store.get_frontier_queue(status="queued", limit=1)
        if not rows:
            return 0.0
        return float(rows[0].get("score") or 0.0)


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
