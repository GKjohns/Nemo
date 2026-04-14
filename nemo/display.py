"""Rich event subscriber for terminal output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nemo.events import EventType, NemoEvent


@dataclass
class _RunStats:
    steps: int = 0
    insights_created: int = 0
    errors: int = 0


class DisplaySubscriber:
    """Render Nemo events for quiet, normal, or verbose CLI output."""

    def __init__(self, verbose: bool = False, quiet: bool = False, console: Console | None = None) -> None:
        self.verbose = verbose
        self.quiet = quiet
        self.console = console or Console()
        self.stats = _RunStats()

    async def on_event(self, event: NemoEvent) -> None:
        if event.type == EventType.RUN_STARTED:
            self._show_run_header(event)
        elif event.type == EventType.FRONTIER_REFRESHED:
            if event.payload.get("plan_only"):
                self._show_plan_table(event.payload)
            elif self.verbose:
                self.console.print(
                    f"[cyan]frontier[/cyan] generated={event.payload.get('generated', 0)} "
                    f"deduped={event.payload.get('after_dedupe', 0)} "
                    f"top_score={event.payload.get('top_score', 0):.3f}"
                )
                deterministic = event.payload.get("top_actions_deterministic") or []
                reranked = event.payload.get("top_actions") or []
                if deterministic and reranked:
                    det_types = " > ".join(str(action.get("type", "")) for action in deterministic[:8])
                    reranked_types = " > ".join(str(action.get("type", "")) for action in reranked[:8])
                    self.console.print(f"  deterministic_top8: {det_types}")
                    self.console.print(f"  reranked_top8:      {reranked_types}")
        elif event.type == EventType.FRONTIER_SATURATED:
            self.console.print(
                f"[yellow]saturated[/yellow] top_score={event.payload.get('top_score', 0):.3f} "
                f"threshold={event.payload.get('threshold', 0):.3f}"
            )
        elif event.type == EventType.STEP_STARTED and not self.quiet:
            action = event.payload.get("action", {})
            self.console.print(
                f"[{event.step_num}] [bold]{action.get('action_type', 'UNKNOWN')}[/bold] "
                f"score={float(action.get('score', 0.0)):.3f}"
            )
        elif event.type == EventType.STEP_PHASE:
            phase = str(event.payload.get("phase", ""))
            if self.verbose:
                self.console.print(f"  phase: {phase}")
                if phase == "executing" and event.payload.get("sql"):
                    self.console.print(f"  sql: {event.payload.get('sql')}")
                if phase == "analyzing":
                    iteration = event.payload.get("iteration")
                    max_iterations = event.payload.get("analyst_max_iterations")
                    if iteration is not None and max_iterations is not None:
                        self.console.print(
                            f"  analyst iteration: {iteration}/{max_iterations} "
                            f"stage={event.payload.get('analyst_stage', 'tooling')}"
                        )
                    warning = str(event.payload.get("warning") or "")
                    if warning:
                        self.console.print(f"  [yellow]warning[/yellow] {warning}")
            elif not self.quiet and phase in ("executing", "interpreting", "linking", "analyzing"):
                self.console.print(f"  {phase}...", end="\r")
        elif event.type == EventType.STEP_COMPLETED:
            self.stats.steps += 1
            if not self.quiet:
                self.console.print(
                    f"  [green]insight[/green] {event.payload.get('claim', '')} "
                    f"(confidence={float(event.payload.get('confidence', 0.0)):.2f})"
                )
                if self.verbose and event.payload.get("result_preview"):
                    self.console.print(f"  preview: {event.payload.get('result_preview')}")
        elif event.type == EventType.STEP_ERROR:
            self.stats.errors += 1
            self.console.print(f"[red]step error[/red] {event.payload.get('error', 'unknown error')}")
        elif event.type == EventType.STEP_SKIPPED and not self.quiet:
            self.console.print(f"[yellow]step skipped[/yellow] {event.payload.get('reason', '')}")
        elif event.type == EventType.INSIGHT_CREATED:
            self.stats.insights_created += 1
            if self.verbose:
                self.console.print(f"  created insight_id={event.payload.get('insight_id')}")
        elif event.type == EventType.EDGE_CREATED and self.verbose:
            self.console.print(
                "  linked edge "
                f"{event.payload.get('from_insight_id')} -> {event.payload.get('to_insight_id')} "
                f"({event.payload.get('type')})"
            )
        elif event.type == EventType.CONTRADICTION_DETECTED and not self.quiet:
            cluster = event.payload.get("cluster", {})
            ids = cluster.get("insight_ids", [])
            self.console.print(f"[magenta]contradiction[/magenta] cluster_size={len(ids)}")
        elif event.type == EventType.PHASE_DECIDED and not self.quiet:
            phase = str(event.payload.get("phase", "")).upper()
            hypothesis_id = str(event.payload.get("hypothesis_id") or "")
            details = f" target={hypothesis_id}" if hypothesis_id else ""
            self.console.print(f"[blue]phase[/blue] {phase}{details}")
            if self.verbose:
                reasoning = str(event.payload.get("reasoning") or "")
                if reasoning:
                    self.console.print(f"  reason: {reasoning}")
        elif event.type == EventType.HYPOTHESIS_FORMED and not self.quiet:
            question = str(event.payload.get("question") or "")
            table = str(event.payload.get("table") or "")
            analysis = str(event.payload.get("analysis_type") or "")
            self.console.print(
                f"[cyan]plan[/cyan] {analysis} on [bold]{table}[/bold]: {question[:120]}"
            )
            if self.verbose:
                reasoning = str(event.payload.get("reasoning") or "")
                if reasoning:
                    self.console.print(f"  reasoning: {reasoning}")
                sql = str(event.payload.get("sql") or "")
                if sql:
                    self.console.print(f"  sql: {sql}")
        elif event.type == EventType.HYPOTHESIS_PROPOSED and not self.quiet:
            hypothesis_id = str(event.payload.get("hypothesis_id") or "")
            claim = str(event.payload.get("claim") or "")
            self.console.print(f"[cyan]hypothesis proposed[/cyan] {hypothesis_id} {claim[:100]}")
        elif event.type == EventType.VALIDATION_STEP and not self.quiet:
            hypothesis_id = str(event.payload.get("hypothesis_id") or "")
            step_num = int(event.payload.get("validation_step") or 0)
            max_steps = int(event.payload.get("max_validation_steps") or 0)
            relationship = str(event.payload.get("relationship") or "")
            self.console.print(
                f"[cyan]validation[/cyan] {hypothesis_id} step={step_num}/{max_steps} relation={relationship}"
            )
        elif event.type == EventType.HYPOTHESIS_VERDICT and not self.quiet:
            hypothesis_id = str(event.payload.get("hypothesis_id") or "")
            status = str(event.payload.get("status") or "")
            confidence = float(event.payload.get("verdict_confidence") or 0.0)
            self.console.print(
                f"[bold]{status}[/bold] {hypothesis_id} confidence={confidence:.2f}"
            )
        elif event.type == EventType.RUN_COMPLETED:
            self._show_run_summary(event.payload.get("stats", {}))
            self._show_debrief(event.payload.get("debrief", ""))
        elif event.type == EventType.RUN_INTERRUPTED:
            self.console.print("[yellow]run interrupted[/yellow]")
            self._show_run_summary(event.payload.get("stats", {}))
            self._show_debrief(event.payload.get("debrief", ""))
        elif event.type == EventType.RUN_ERROR:
            self.console.print(f"[red]run failed[/red] {event.payload.get('error', '')}")

    def _show_run_header(self, event: NemoEvent) -> None:
        datasets = event.payload.get("datasets", [])
        self.console.print(
            f"[bold cyan]Nemo run[/bold cyan] id={event.run_id} datasets={len(datasets)} "
            f"frontier={event.payload.get('frontier_size', 0)}"
        )

    def _show_run_summary(self, stats_payload: dict[str, Any]) -> None:
        table = Table(title="run summary")
        table.add_column("metric")
        table.add_column("value", justify="right")
        for key in ["steps", "insights_created", "errors", "duration_ms", "frontier_remaining"]:
            table.add_row(key, str(stats_payload.get(key, 0)))
        self.console.print(table)

    def _show_debrief(self, debrief: str) -> None:
        if not debrief or not debrief.strip() or self.quiet:
            return
        self.console.print()
        self.console.print(Panel(debrief.strip(), title="Investigation Summary", border_style="cyan", padding=(1, 2)))

    def _show_plan_table(self, payload: dict[str, Any]) -> None:
        actions = payload.get("top_actions") or []
        total = int(payload.get("after_score", 0))
        table = Table(title=f"Nemo Plan - {total} actions scored (top {len(actions)})")
        table.add_column("#", justify="right")
        table.add_column("Score", justify="right")
        table.add_column("Type")
        table.add_column("Table")
        table.add_column("Target")
        table.add_column("Rationale")
        for action in actions:
            table.add_row(
                str(action.get("rank", "")),
                f"{float(action.get('score', 0.0)):.2f}",
                str(action.get("type", "")),
                str(action.get("table", "")),
                str(action.get("target", "")),
                str(action.get("rationale", "")),
            )
        self.console.print(table)
