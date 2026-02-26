"""Rich-based interactive Nemo dashboard (inline REPL, no alternate screen)."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.markdown import Markdown as RichMarkdown
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table
from nemo.config import NemoConfig
from nemo.engine import NemoEngine
from nemo.events import EventBus, EventType, NemoEvent
from nemo.ingest.add import add_file, add_tpch
from nemo.tui import data

console = Console()

HELP_TEXT = """\
[bold]Nemo interactive dashboard[/bold]

Commands:
  [cyan]dashboard[/cyan] / [cyan]d[/cyan]   — project status at a glance
  [cyan]datasets[/cyan]  / [cyan]ds[/cyan]  — list loaded datasets
  [cyan]add[/cyan] <path>       — add a CSV/Parquet file
  [cyan]tpch[/cyan]             — load TPC-H demo data (scale 0.01)
  [cyan]profile[/cyan] <table>  — column-level profile for a table
  [cyan]insights[/cyan] / [cyan]i[/cyan]    — browse insights (sort: confidence)
  [cyan]insight[/cyan] <id>     — detail view for one insight
  [cyan]repro[/cyan] <id>       — re-execute an insight's SQL
  [cyan]graph[/cyan]  / [cyan]g[/cyan]      — evidence graph stats + contradictions
  [cyan]edges[/cyan] [type]     — edge list (optional filter: supports/contradicts/refines/depends_on)
  [cyan]brief[/cyan]  / [cyan]b[/cyan]      — render the markdown brief
  [cyan]save[/cyan] [path]      — save brief to file (default: reports/brief_latest.md)
  [cyan]runs[/cyan]             — recent run history
  [cyan]run[/cyan] [steps]      — start an exploration run (default 15 steps)
  [cyan]search[/cyan] <query>   — search insights by keyword
  [cyan]help[/cyan]  / [cyan]?[/cyan]       — this help
  [cyan]quit[/cyan]  / [cyan]q[/cyan]       — exit
"""


def _banner() -> None:
    console.print()
    console.print(
        Panel(
            "[bold cyan]Nemo[/bold cyan]  —  local-first AI data exploration",
            subtitle="type [bold]help[/bold] or [bold]?[/bold] for commands",
            border_style="cyan",
            expand=False,
            padding=(0, 2),
        )
    )
    console.print()


def _welcome_flow(project_dir: Path) -> bool:
    """Inline welcome flow when no project exists. Returns True if a project was set up."""
    console.print()
    console.print(
        Panel(
            "[bold]Welcome to Nemo[/bold]\n\n"
            "No project found in this directory.\n\n"
            "  [cyan][Q][/cyan] Quick start with TPC-H demo data\n"
            "  [cyan][I][/cyan] Initialize empty project\n"
            "  [cyan][O][/cyan] Open existing project\n\n"
            "[dim]Quick start will:[/dim]\n"
            "  1. Initialize a new Nemo project here\n"
            "  2. Load TPC-H demo tables (scale 0.01)\n"
            "  3. Start a 15-step exploration run\n"
            "  4. Drop you into the dashboard",
            border_style="cyan",
            expand=False,
            padding=(1, 3),
        )
    )
    console.print()
    choice = Prompt.ask("Choose", choices=["q", "i", "o"], default="q")

    if choice == "q":
        with console.status("[cyan]Initializing project...[/cyan]", spinner="dots"):
            data.initialize_project(project_dir)
        console.print("  [green]✓[/green] Project initialized")
        with console.status("[cyan]Loading TPC-H demo tables (scale 0.01)...[/cyan]", spinner="dots"):
            store = data.open_store(project_dir)
            try:
                add_tpch(store, scale=0.01)
            finally:
                store.close()
        console.print("  [green]✓[/green] 8 TPC-H tables loaded")
        console.print()
        _run_engine(project_dir, steps=15, minutes=None, resume_run_id=None)
        return True
    elif choice == "i":
        with console.status("[cyan]Initializing project...[/cyan]", spinner="dots"):
            data.initialize_project(project_dir)
        console.print("[green]✓ Project initialized.[/green]")
        return True
    elif choice == "o":
        raw = Prompt.ask("Project directory path")
        target = Path(raw).expanduser().resolve()
        if not data.has_project(target):
            console.print(f"[red]No nemo.duckdb in {target}[/red]")
            return False
        console.print(f"[green]Opened project: {target}[/green]")
        return True
    return False


# ── View renderers ──────────────────────────────────────────────────────────


def _show_dashboard(store, project_dir: Path) -> None:
    status = data.dashboard_status(store, project_dir)
    latest = status.get("latest_run")

    table = Table(title="Dashboard", show_edge=False, border_style="cyan", expand=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Project", status["project_path"])
    table.add_row("Database size", f"{status['db_size_bytes']:,} bytes")
    table.add_row("Datasets", f"{status['dataset_count']} ({status['dataset_rows_total']:,} rows)")
    table.add_row("Insights", str(status["insights_count"]))
    table.add_row("Frontier queued", str(status["frontier_queued"]))
    table.add_row("Contradictions", str(status["contradictions"]))
    table.add_row("Learnings", str(status["learnings_count"]))
    if latest:
        table.add_row("", "")
        table.add_row("Latest run", str(latest.get("run_id", "")))
        table.add_row("Status", str(latest.get("status", "")))
        table.add_row("Steps", str(int(latest.get("steps_completed") or 0)))
        table.add_row("Insights", str(int(latest.get("insights_created") or 0)))
        table.add_row("Errors", str(int(latest.get("errors") or 0)))
    else:
        table.add_row("Latest run", "[dim]none[/dim]")
    console.print(table)


def _show_datasets(store) -> None:
    rows = data.list_datasets(store)
    if not rows:
        console.print("[yellow]No datasets loaded. Use [bold]add <path>[/bold] or [bold]tpch[/bold].[/yellow]")
        return
    table = Table(title="Datasets", show_edge=False, border_style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Rows", justify="right")
    table.add_column("Cols", justify="right")
    table.add_column("Format")
    table.add_column("Source")
    table.add_column("Added At")
    for row in rows:
        r = "n/a" if int(row["rows"]) < 0 else f"{int(row['rows']):,}"
        c = "n/a" if int(row["cols"]) < 0 else str(row["cols"])
        table.add_row(row["name"], r, c, row["format"], row["source_uri"], row["created_at"])
    console.print(table)


def _show_profile(store, table_name: str) -> None:
    prof = data.profile_dataset(store, table_name)
    table = Table(title=f"Profile: {prof['table']} ({prof['row_count']:,} rows)", show_edge=False, border_style="cyan")
    table.add_column("Column", style="bold")
    table.add_column("Type")
    table.add_column("Null %", justify="right")
    table.add_column("Distinct", justify="right")
    table.add_column("Min / Max")
    table.add_column("Samples")
    for col in prof["columns"]:
        min_max = "-"
        if col["min_val"] is not None or col["max_val"] is not None:
            min_max = f"{col['min_val']} .. {col['max_val']}"
        samples = ", ".join(str(v) for v in (col["sample_values"] or [])[:4])
        table.add_row(
            col["name"],
            col["dtype"],
            f"{col['null_pct']:.1%}",
            str(col["distinct_count"]),
            min_max,
            samples or "-",
        )
    console.print(table)


def _show_insights(store, search: str = "", sort: str = "confidence") -> None:
    rows = data.list_insights(store, search=search, sort=sort, limit=30)
    if not rows:
        console.print("[yellow]No insights found.[/yellow]")
        return
    table = Table(title="Insights", show_edge=False, border_style="cyan")
    table.add_column("#", justify="right", style="dim")
    table.add_column("ID", style="bold")
    table.add_column("Title")
    table.add_column("Confidence", justify="right")
    table.add_column("Tables")
    table.add_column("Claim", max_width=50)
    for idx, row in enumerate(rows, 1):
        conf_color = "green" if float(row["confidence"]) >= 0.7 else ("yellow" if float(row["confidence"]) >= 0.4 else "red")
        table.add_row(
            str(idx),
            row["insight_id"][-8:],
            row["title"][:40],
            f"[{conf_color}]{float(row['confidence']):.2f}[/{conf_color}]",
            ", ".join(row["tables"])[:30],
            row["claim"][:50],
        )
    console.print(table)


def _show_insight_detail(store, insight_id: str) -> None:
    detail = data.get_insight_detail(store, insight_id)
    if detail is None:
        console.print(f"[red]Insight not found: {insight_id}[/red]")
        return
    import json

    sample = detail.get("result_sample")
    if isinstance(sample, (list, dict)):
        sample_str = json.dumps(sample, indent=2)[:500]
    else:
        sample_str = str(sample)[:300] if sample else "-"

    edges = detail.get("edges", [])
    edge_lines = []
    for e in edges[:5]:
        edge_lines.append(f"  {e.get('from_insight_id', '')[-8:]} → {e.get('to_insight_id', '')[-8:]} [{e.get('type')}] {e.get('rationale', '')[:60]}")

    text = (
        f"[bold]{detail.get('title')}[/bold]\n"
        f"ID: {detail.get('insight_id')}\n"
        f"Confidence: {float(detail.get('confidence') or 0):.3f}\n"
        f"Tables: {', '.join(detail.get('source_tables', []))}\n"
        f"Thread: {detail.get('thread_id') or '-'}\n\n"
        f"[bold]Claim:[/bold] {detail.get('claim', '')}\n\n"
        f"[bold]SQL:[/bold]\n{str(detail.get('sql') or '')[:500]}\n\n"
        f"[bold]Edges ({len(edges)}):[/bold]\n" + ("\n".join(edge_lines) if edge_lines else "  none") + "\n\n"
        f"[bold]Result sample:[/bold]\n{sample_str}"
    )
    console.print(Panel(text, border_style="cyan", expand=False, padding=(1, 2)))


def _show_graph(store) -> None:
    stats = data.graph_stats(store)
    clusters = data.contradiction_clusters(store)

    table = Table(title="Evidence Graph", show_edge=False, border_style="cyan", expand=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Insights", str(stats["insights"]))
    table.add_row("Edges", str(stats["edges"]))
    table.add_row("Supports", str(stats["supports"]))
    table.add_row("Contradictions", str(stats["contradictions"]))
    table.add_row("Refines", str(stats["refines"]))
    table.add_row("Depends on", str(stats["depends_on"]))
    table.add_row("Avg confidence", f"{stats['avg_confidence']:.3f}")
    table.add_row("Coverage", f"{stats['coverage_touched']}/{stats['coverage_total']} ({stats['coverage_ratio']:.0%})")
    console.print(table)

    if clusters:
        console.print()
        console.print("[bold]Contradiction clusters:[/bold]")
        for idx, cluster in enumerate(clusters[:10], 1):
            claims = cluster.get("claims", [])
            preview = " | ".join(claims[:2]) if claims else "-"
            console.print(f"  {idx}. {len(cluster.get('insight_ids', []))} insights — {preview}")
    else:
        console.print("[dim]No contradiction clusters.[/dim]")


def _show_edges(store, edge_type: str | None = None) -> None:
    edges = data.list_edges(store, edge_type=edge_type, limit=30)
    if not edges:
        label = f" (type={edge_type})" if edge_type else ""
        console.print(f"[yellow]No edges found{label}.[/yellow]")
        return
    table = Table(title=f"Edges{' [' + edge_type + ']' if edge_type else ''}", show_edge=False, border_style="cyan")
    table.add_column("From", style="bold")
    table.add_column("To", style="bold")
    table.add_column("Type")
    table.add_column("Weight", justify="right")
    table.add_column("Rationale", max_width=60)
    for edge in edges:
        table.add_row(
            edge["from"][-8:],
            edge["to"][-8:],
            edge["type"],
            f"{edge['weight']:.2f}",
            edge["rationale"][:60],
        )
    console.print(table)


def _show_brief(store) -> None:
    md = data.brief_markdown(store, top_n=10)
    console.print(RichMarkdown(md))


def _show_runs(store) -> None:
    runs = data.list_runs(store, limit=10)
    if not runs:
        console.print("[yellow]No runs yet.[/yellow]")
        return
    table = Table(title="Recent Runs", show_edge=False, border_style="cyan")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Run ID", style="bold")
    table.add_column("Status")
    table.add_column("Steps", justify="right")
    table.add_column("Insights", justify="right")
    table.add_column("Errors", justify="right")
    table.add_column("Started")
    for idx, run in enumerate(runs, 1):
        table.add_row(
            str(idx),
            str(run.get("run_id", ""))[-12:],
            str(run.get("status", "")),
            str(int(run.get("steps_completed") or 0)),
            str(int(run.get("insights_created") or 0)),
            str(int(run.get("errors") or 0)),
            str(run.get("started_at", "")),
        )
    console.print(table)


# ── Engine runner ───────────────────────────────────────────────────────────


_ACTION_LABELS: dict[str, str] = {
    "METRIC_TREND_SCAN": "Scanning for trends",
    "CHANGEPOINT_DETECT": "Detecting change-points",
    "TOP_GROUPS": "Ranking top groups",
    "OUTLIER_GROUPS": "Looking for outlier groups",
    "DATA_QUALITY_CHECK": "Checking data quality",
    "SEGMENT_COMPARE": "Comparing segments",
    "CORRELATION_SCAN": "Scanning for correlations",
    "COVERAGE_EXPLORER": "Mapping coverage",
    "ROBUSTNESS_CHECK": "Testing robustness",
    "DRILL_DOWN": "Drilling deeper",
    "JOIN_EXPLORE": "Exploring across tables",
}


def _describe_step(action_type: str, payload: dict) -> str:
    """Human-readable one-liner: what is this step investigating?"""
    label = _ACTION_LABELS.get(action_type, action_type.replace("_", " ").title())
    table = payload.get("table", "")
    metric = payload.get("metric_col", "")
    dim = payload.get("dimension_col") or payload.get("group_col") or ""
    if metric and dim:
        return f"{label} in [bold]{metric}[/bold] by {dim} on [cyan]{table}[/cyan]"
    if metric:
        return f"{label} in [bold]{metric}[/bold] on [cyan]{table}[/cyan]"
    if table:
        return f"{label} on [cyan]{table}[/cyan]"
    return label


def _conf_badge(conf: float) -> str:
    """Compact inline confidence: colored dot + value."""
    if conf >= 0.7:
        return f"[green]●[/green] {conf:.0%} confidence"
    if conf >= 0.4:
        return f"[yellow]●[/yellow] {conf:.0%} confidence"
    return f"[red]●[/red] {conf:.0%} confidence"


def _wrap_reasoning(text: str, indent: int = 4, width: int = 88) -> str:
    """Wrap reasoning text to fit terminal width with consistent indentation."""
    import textwrap

    prefix = " " * indent
    lines = textwrap.wrap(text.strip(), width=width - indent)
    return ("\n" + prefix).join(lines)


def _human_duration(ms: int) -> str:
    """Friendly duration: '<1s', '2.3s', '1m 5s'."""
    if ms < 1000:
        return "<1s"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    m = int(s // 60)
    remainder = s - m * 60
    return f"{m}m {remainder:.0f}s"


class _LiveEventSubscriber:
    """Prints rich per-step output with hypothesis reasoning and findings."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.steps_done = 0
        self.insights_created = 0
        self.errors = 0
        self._current_action: dict[str, Any] = {}
        self._current_hypothesis: dict[str, Any] = {}
        self._current_phase = ""
        self._current_sql = ""
        self._step_start: float = 0.0
        self._status: Any | None = None

    def set_status(self, status: Any) -> None:
        self._status = status

    def _update_spinner(self, text: str) -> None:
        if self._status is not None:
            self._status.update(text)

    async def on_event(self, event: NemoEvent) -> None:
        if event.type == EventType.RUN_STARTED:
            ds_count = len(event.payload.get("datasets", []))
            frontier = event.payload.get("frontier_size", 0)
            console.print(
                f"  [dim]datasets: {ds_count}, frontier queued: {frontier}[/dim]"
            )

        elif event.type == EventType.MEMORY_LOADED:
            tables = event.payload.get("tables", [])
            learnings = event.payload.get("learnings_count", 0)
            errs = len(event.payload.get("error_patterns", []))
            self._update_spinner(
                f"[cyan]Loading working memory... "
                f"{len(tables)} tables, {learnings} learnings, {errs} error patterns[/cyan]"
            )

        elif event.type == EventType.FRONTIER_REFRESHED:
            generated = event.payload.get("generated", 0)
            scored = event.payload.get("after_score", 0)
            top = float(event.payload.get("top_score", 0))
            console.print(
                f"  [dim]frontier refreshed: {generated} generated → "
                f"{scored} scored (top score {top:.2f})[/dim]"
            )

        elif event.type == EventType.HYPOTHESIS_FORMED:
            self._step_start = time.perf_counter()
            self._current_hypothesis = event.payload
            question = event.payload.get("question", "")
            reasoning = event.payload.get("reasoning", "")
            table = event.payload.get("table", "")
            step_label = f"Step {event.step_num}/{self.budget}"
            console.print()
            table_tag = f" [dim]({table})[/dim]" if table else ""
            console.print(
                f"  [cyan]→[/cyan] [bold]{step_label}[/bold]  {question}{table_tag}"
            )
            if reasoning:
                wrapped = _wrap_reasoning(reasoning, indent=4, width=88)
                console.print(f"    [dim]{wrapped}[/dim]")
            self._update_spinner(
                f"[cyan]{step_label} — Investigating...[/cyan]"
            )

        elif event.type == EventType.STEP_STARTED:
            self._step_start = time.perf_counter()
            action = event.payload.get("action", {})
            self._current_action = action
            hypothesis = event.payload.get("hypothesis")
            if hypothesis:
                self._current_hypothesis = hypothesis
            elif not self._current_hypothesis:
                action_payload = action.get("payload", {})
                description = _describe_step(action.get("action_type", "?"), action_payload)
                step_label = f"Step {event.step_num}/{self.budget}"
                self._update_spinner(
                    f"[cyan]{step_label} — {description}[/cyan]"
                )

        elif event.type == EventType.STEP_PHASE:
            phase = event.payload.get("phase", "")
            self._current_phase = phase
            sql = event.payload.get("sql", "")
            if sql:
                self._current_sql = sql
            step_label = f"Step {event.step_num}/{self.budget}"
            phase_label = {
                "compiling": "Writing query",
                "executing": "Running query",
                "summarizing": "Interpreting results",
                "interpreting": "Interpreting results",
                "linking": "Connecting to evidence graph",
                "agent-exploring": "Agent exploring",
            }.get(phase, phase)
            self._update_spinner(
                f"[cyan]{step_label} — {phase_label}...[/cyan]"
            )

        elif event.type == EventType.STEP_COMPLETED:
            self.steps_done += 1
            self.insights_created += 1
            payload = event.payload
            title = str(payload.get("title", ""))
            claim = str(payload.get("claim", ""))
            reasoning = str(payload.get("reasoning", ""))
            conf = float(payload.get("confidence") or 0)
            duration = int(payload.get("duration_ms") or 0)
            edges = int(payload.get("edges_created") or 0)
            row_count = int(payload.get("row_count") or 0)

            console.print(
                f"  [green]✓[/green] [bold]{title}[/bold]"
            )
            console.print(f"    {claim}")
            if reasoning:
                wrapped = _wrap_reasoning(reasoning, indent=4, width=88)
                console.print(f"    [dim italic]{wrapped}[/dim italic]")
            stats_parts = [
                _conf_badge(conf),
                f"{row_count} row{'s' if row_count != 1 else ''}",
                f"{edges} edge{'s' if edges != 1 else ''}",
                _human_duration(duration),
            ]
            console.print(f"    [dim]{' · '.join(stats_parts)}[/dim]")

        elif event.type == EventType.STEP_ERROR:
            self.errors += 1
            err = str(event.payload.get("error", ""))
            phase = str(event.payload.get("phase", ""))
            console.print()
            console.print(
                f"  [red]✗[/red] [bold]Step {event.step_num}/{self.budget}[/bold]  "
                f"Error during {phase}"
            )
            console.print(f"    [red]{err[:120]}[/red]")

        elif event.type == EventType.NOTEBOOK_UPDATED:
            themes = event.payload.get("themes", [])
            total = event.payload.get("total_steps", 0)
            if themes:
                theme_str = ", ".join(themes)
                console.print(f"    [dim]notebook: {theme_str} ({total} steps)[/dim]")

        elif event.type == EventType.CONTRADICTION_DETECTED:
            cluster = event.payload.get("cluster", {})
            claims = cluster.get("claims", [])
            if claims:
                console.print(f"    [yellow]⚡ contradiction:[/yellow] {claims[0][:80]}")

        elif event.type == EventType.RUN_COMPLETED:
            stats = event.payload.get("stats", {})
            learnings = event.payload.get("learnings_recorded", 0)
            threads = event.payload.get("thread_cards_updated", 0)
            duration_ms = stats.get("duration_ms", 0)
            duration_s = duration_ms / 1000.0
            console.print()
            console.print(Rule("[bold green]Run complete[/bold green]", style="green"))
            summary = Table(show_header=False, show_edge=False, box=None, padding=(0, 2))
            summary.add_column(style="bold")
            summary.add_column()
            summary.add_row("Steps", str(stats.get("steps", 0)))
            summary.add_row("Insights created", str(stats.get("insights_created", 0)))
            summary.add_row("Errors", str(stats.get("errors", 0)))
            summary.add_row("Duration", f"{duration_s:.1f}s")
            summary.add_row("Frontier remaining", str(stats.get("frontier_remaining", 0)))
            summary.add_row("Learnings recorded", str(learnings))
            summary.add_row("Thread cards updated", str(threads))
            console.print(summary)

        elif event.type == EventType.RUN_INTERRUPTED:
            stats = event.payload.get("stats", {})
            console.print()
            console.print(Rule("[bold yellow]Run interrupted[/bold yellow]", style="yellow"))
            console.print(
                f"  {stats.get('steps', 0)} steps, "
                f"{stats.get('insights_created', 0)} insights saved"
            )


def _run_engine(
    project_dir: Path,
    steps: int | None,
    minutes: float | None,
    resume_run_id: str | None,
) -> None:
    budget = steps or 15
    store = data.open_store(project_dir)
    try:
        config = NemoConfig.load(project_dir / "nemo.toml")
        bus = EventBus()
        subscriber = _LiveEventSubscriber(budget)
        bus.subscribe(subscriber)
        engine = NemoEngine(store, config, bus)
        console.print()
        console.print(Rule(f"[bold cyan]Exploration run ({budget} steps)[/bold cyan]", style="cyan"))
        with console.status("[cyan]Starting run...[/cyan]", spinner="dots") as status:
            subscriber.set_status(status)
            run_id = asyncio.run(
                engine.run(max_steps=steps, max_minutes=minutes, resume_run_id=resume_run_id)
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]Run interrupted — progress saved.[/yellow]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"\n[red]Run failed:[/red] {exc}")
    finally:
        store.close()


# ── Main REPL ───────────────────────────────────────────────────────────────


def _repl(project_dir: Path) -> None:
    """Read-eval-print loop with Rich output."""
    _banner()
    _with_store(project_dir, lambda s: _show_dashboard(s, project_dir))

    while True:
        try:
            raw = console.input("\n[bold cyan]nemo>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye.[/dim]")
            break

        if not raw:
            continue

        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("quit", "q", "exit"):
            console.print("[dim]Bye.[/dim]")
            break
        elif cmd in ("help", "?"):
            console.print(HELP_TEXT)
        elif cmd in ("dashboard", "d"):
            _with_store(project_dir, lambda s: _show_dashboard(s, project_dir))
        elif cmd in ("datasets", "ds"):
            _with_store(project_dir, lambda s: _show_datasets(s))
        elif cmd == "add" and arg:
            _do_add(project_dir, arg)
        elif cmd == "tpch":
            _do_tpch(project_dir)
        elif cmd == "profile" and arg:
            _with_store(project_dir, lambda s, t=arg: _show_profile(s, t))
        elif cmd in ("insights", "i"):
            _with_store(project_dir, lambda s: _show_insights(s))
        elif cmd == "insight" and arg:
            _resolve_and_show_insight(project_dir, arg)
        elif cmd == "repro" and arg:
            _do_repro(project_dir, arg)
        elif cmd == "search" and arg:
            _with_store(project_dir, lambda s, q=arg: _show_insights(s, search=q))
        elif cmd in ("graph", "g"):
            _with_store(project_dir, lambda s: _show_graph(s))
        elif cmd == "edges":
            etype = arg if arg in ("supports", "contradicts", "refines", "depends_on") else None
            _with_store(project_dir, lambda s, et=etype: _show_edges(s, edge_type=et))
        elif cmd in ("brief", "b"):
            _with_store(project_dir, lambda s: _show_brief(s))
        elif cmd == "save":
            _do_save_brief(project_dir, arg)
        elif cmd == "runs":
            _with_store(project_dir, lambda s: _show_runs(s))
        elif cmd == "run":
            steps = int(arg) if arg.isdigit() else 15
            _run_engine(project_dir, steps=steps, minutes=None, resume_run_id=None)
        else:
            console.print(f"[red]Unknown command:[/red] {raw}  (type [bold]help[/bold] for commands)")


def _with_store(project_dir: Path, fn) -> None:  # type: ignore[no-untyped-def]
    """Open a store, call fn(store), close. Catches errors."""
    try:
        store = data.open_store(project_dir)
        try:
            fn(store)
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Error:[/red] {exc}")


def _resolve_and_show_insight(project_dir: Path, raw_id: str) -> None:
    """Resolve a partial insight ID and show detail."""

    def _inner(store) -> None:  # type: ignore[no-untyped-def]
        full = _resolve_insight_id(store, raw_id)
        if full is None:
            console.print(f"[red]No insight matching: {raw_id}[/red]")
            return
        _show_insight_detail(store, full)

    _with_store(project_dir, _inner)


def _do_repro(project_dir: Path, raw_id: str) -> None:
    def _inner(store) -> None:  # type: ignore[no-untyped-def]
        full = _resolve_insight_id(store, raw_id)
        if full is None:
            console.print(f"[red]No insight matching: {raw_id}[/red]")
            return
        result = data.rerun_insight_sql(store, full)
        if result["ok"]:
            console.print(f"[green]✓ Reproducibility OK[/green] — {result['row_count']} rows returned")
        else:
            console.print(f"[red]✗ Reproducibility FAILED[/red] — {result['error']}")

    _with_store(project_dir, _inner)


def _resolve_insight_id(store, partial: str) -> str | None:
    """Match a partial insight ID suffix to a full ID."""
    insight = store.get_insight_by_id(partial)
    if insight:
        return str(insight["insight_id"])
    rows = store.execute(
        "SELECT insight_id FROM insights WHERE insight_id LIKE ? ORDER BY created_at DESC LIMIT 1",
        [f"%{partial}"],
    ).fetchall()
    if rows:
        return str(rows[0][0])
    return None


def _do_add(project_dir: Path, raw_path: str) -> None:
    source = Path(raw_path).expanduser()
    if not source.exists():
        console.print(f"[red]File not found: {source}[/red]")
        return
    table_name = source.stem or "dataset"
    try:
        with console.status(f"[cyan]Adding {table_name}...[/cyan]", spinner="dots"):
            store = data.open_store(project_dir)
            try:
                add_file(store, path=source, name=table_name, format="auto")
            finally:
                store.close()
        console.print(f"[green]✓ Added:[/green] {table_name}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Add failed:[/red] {exc}")


def _do_tpch(project_dir: Path) -> None:
    try:
        with console.status("[cyan]Loading TPC-H demo tables (scale 0.01)...[/cyan]", spinner="dots"):
            store = data.open_store(project_dir)
            try:
                ids = add_tpch(store, scale=0.01)
            finally:
                store.close()
        console.print(f"[green]✓ TPC-H loaded:[/green] {len(ids)} tables")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]TPC-H load failed:[/red] {exc}")


def _do_save_brief(project_dir: Path, raw_path: str) -> None:
    rel = raw_path or "reports/brief_latest.md"
    output = (project_dir / rel).resolve()
    try:
        with console.status("[cyan]Generating brief...[/cyan]", spinner="dots"):
            store = data.open_store(project_dir)
            try:
                written = data.save_brief_markdown(store, output, top_n=10)
            finally:
                store.close()
        console.print(f"[green]✓ Saved:[/green] {written}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Save failed:[/red] {exc}")


def launch_tui() -> None:
    """Entry point: show welcome if no project, then start REPL."""
    project_dir = Path(".").resolve()
    if not data.has_project(project_dir):
        ok = _welcome_flow(project_dir)
        if not ok:
            return
    _repl(project_dir)
