"""Typer CLI entrypoint for Nemo."""

from __future__ import annotations

import asyncio
import ast
import json
import os
from pathlib import Path
from typing import Callable

import typer
from openai import OpenAI
from rich.console import Console
from rich.table import Table

from nemo.config import NemoConfig, write_default_config
from nemo.display import DisplaySubscriber
from nemo.engine import NemoEngine
from nemo.events import EventBus
from nemo.graph import find_contradiction_clusters
from nemo.hooks import UserHookSubscriber
from nemo.ingest.add import add_file, add_glob, add_tpch
from nemo.ingest.profile import profile_table
from nemo.report import generate_brief_markdown, write_brief_report
from nemo.store.db import NemoStore, SYSTEM_TABLES

app = typer.Typer(name="nemo", help="Nemo - local-first AI data exploration agent")
graph_app = typer.Typer(help="Evidence graph commands")
app.add_typer(graph_app, name="graph")

console = Console()


@app.command()
def init(path: Path = typer.Argument(Path("."), help="Project directory")) -> None:
    """Initialize a new Nemo project."""
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)

    db_path = root / "nemo.duckdb"
    config_path = root / "nemo.toml"
    nemo_dir = root / ".nemo"
    generators_dir = nemo_dir / "generators"
    hooks_dir = nemo_dir / "hooks"

    generators_dir.mkdir(parents=True, exist_ok=True)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    store = NemoStore(db_path)
    store.initialize()
    store.close()

    if not config_path.exists():
        write_default_config(config_path)

    console.print("[green]Nemo initialized successfully.[/green]")
    console.print(f"Project: [bold]{root}[/bold]")
    console.print(f"Database: [bold]{db_path}[/bold]")
    console.print(f"Config: [bold]{config_path}[/bold]")
    console.print(f"User dir: [bold]{nemo_dir}[/bold]")


@app.command()
def doctor(path: Path = typer.Option(Path("."), "--path", help="Project directory")) -> None:
    """Verify Nemo setup and print pass/fail checks."""
    root = path.resolve()
    db_path = root / "nemo.duckdb"
    config_path = root / "nemo.toml"
    generators_dir = root / ".nemo" / "generators"

    checks: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("DuckDB file exists", lambda: _check_db_exists(db_path)),
        ("System tables initialized", lambda: _check_system_tables(db_path)),
        ("Config file parseable", lambda: _check_config(config_path)),
        ("OpenAI API key set", lambda: _check_llm_key(config_path)),
        ("LLM connectivity", lambda: _check_llm_ping(config_path)),
        ("Datasets loaded", lambda: _check_has_datasets(db_path)),
        ("Custom generators valid", lambda: _check_custom_generators(generators_dir)),
    ]

    table = Table(title="nemo doctor")
    table.add_column("Status")
    table.add_column("Check")
    table.add_column("Details")

    failures = 0
    for label, fn in checks:
        ok, details = fn()
        status = "[green]✓[/green]" if ok else "[red]✗[/red]"
        if not ok:
            failures += 1
        table.add_row(status, label, details)

    console.print(table)
    if failures:
        raise typer.Exit(code=1)


@app.command()
def add(
    path: str | None = typer.Argument(None, help="Path to CSV/Parquet file or glob pattern"),
    name: str | None = typer.Option(None, "--name", "-n", help="Table name"),
    format: str = typer.Option("auto", "--format", "-f", help="File format: auto/csv/parquet"),
    tpch: bool = typer.Option(False, "--tpch", help="Load TPC-H demo data"),
    scale: float = typer.Option(1.0, "--scale", help="TPC-H scale factor"),
) -> None:
    """Add dataset(s) to the current project."""
    store: NemoStore | None = None
    try:
        store = _open_store()
        if tpch:
            dataset_ids = add_tpch(store, scale=scale)
            console.print(f"[green]Loaded TPC-H tables:[/green] {len(dataset_ids)}")
            return

        if not path:
            raise typer.BadParameter("path is required unless --tpch is provided")

        table_name = name or _default_table_name(path)
        if any(token in path for token in ["*", "?", "["]):
            dataset_id = add_glob(store, pattern=path, name=table_name, format=format)
        else:
            dataset_id = add_file(store, path=Path(path), name=table_name, format=format)

        console.print(f"[green]Added dataset:[/green] {table_name}")
        console.print(f"dataset_id: [bold]{dataset_id}[/bold]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]add failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if store is not None:
            store.close()


@app.command()
def ls() -> None:
    """List loaded datasets."""
    store: NemoStore | None = None
    try:
        store = _open_store()
        datasets = store.get_datasets()
        if not datasets:
            console.print("[yellow]No datasets loaded yet. Use `nemo add` first.[/yellow]")
            return

        table = Table(title="datasets")
        table.add_column("Name")
        table.add_column("Rows", justify="right")
        table.add_column("Cols", justify="right")
        table.add_column("Format")
        table.add_column("Source")
        table.add_column("Added At")

        for dataset in datasets:
            table_name = str(dataset["name"])
            if not store.table_exists(table_name):
                row_count = "n/a"
                col_count = "n/a"
            else:
                safe_name = _quote_ident(table_name)
                row_count = str(store.execute(f"SELECT COUNT(*) FROM {safe_name}").fetchone()[0])
                col_count = str(store.execute(f"SELECT COUNT(*) FROM pragma_table_info({safe_name})").fetchone()[0])

            table.add_row(
                table_name,
                row_count,
                col_count,
                str(dataset["format"]),
                str(dataset["source_uri"]),
                str(dataset["created_at"]),
            )
        console.print(table)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]ls failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if store is not None:
            store.close()


@app.command()
def schema(table_name: str = typer.Argument(..., help="Table name")) -> None:
    """Show schema for a table."""
    store: NemoStore | None = None
    try:
        store = _open_store()
        if not store.table_exists(table_name):
            raise ValueError(f"table not found: {table_name}")

        safe_name = _quote_ident(table_name)
        rows = store.execute(f"PRAGMA table_info({safe_name})").fetchall()
        table = Table(title=f"schema: {table_name}")
        table.add_column("Column")
        table.add_column("Type")
        table.add_column("Nullable")
        table.add_column("Default")

        for row in rows:
            table.add_row(str(row[1]), str(row[2]), "yes" if not bool(row[3]) else "no", str(row[4] or ""))
        console.print(table)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]schema failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if store is not None:
            store.close()


@app.command()
def profile(table_name: str = typer.Argument(..., help="Table name")) -> None:
    """Show a rich profile for a table."""
    store: NemoStore | None = None
    try:
        store = _open_store()
        table_profile = profile_table(store, table_name)
        table = Table(title=f"profile: {table_profile.name} ({table_profile.row_count} rows)")
        table.add_column("Column")
        table.add_column("Type")
        table.add_column("Null %", justify="right")
        table.add_column("Distinct", justify="right")
        table.add_column("Min/Max")
        table.add_column("P25/P50/P75")
        table.add_column("Samples")

        for column in table_profile.columns:
            min_max = "-"
            if column.min_val is not None or column.max_val is not None:
                min_max = f"{column.min_val} .. {column.max_val}"

            quantiles = "-"
            if column.p25 is not None or column.p50 is not None or column.p75 is not None:
                quantiles = f"{column.p25} / {column.p50} / {column.p75}"

            samples = ", ".join(str(sample) for sample in column.sample_values) if column.sample_values else "-"
            table.add_row(
                column.name,
                column.dtype,
                f"{column.null_pct:.2%}",
                str(column.distinct_count),
                min_max,
                quantiles,
                samples,
            )
        console.print(table)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]profile failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if store is not None:
            store.close()


@app.command()
def run(
    steps: int | None = typer.Option(None, "--steps", "-s", help="Max exploration steps"),
    minutes: float | None = typer.Option(None, "--minutes", "-m", help="Max runtime in minutes"),
    plan: bool = typer.Option(False, "--plan", help="Dry run: generate + score frontier only"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show full phase output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output"),
) -> None:
    """Run Nemo exploration loop."""
    store: NemoStore | None = None
    try:
        store = _open_store()
        config = _load_config()
        bus = EventBus()
        bus.subscribe(DisplaySubscriber(verbose=verbose or config.verbose, quiet=quiet or config.quiet))
        bus.subscribe(UserHookSubscriber(config))
        engine = NemoEngine(store, config, bus)
        run_id = asyncio.run(engine.run(max_steps=steps, max_minutes=minutes, plan_only=plan))
        console.print(f"[green]run complete[/green] {run_id}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]run failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if store is not None:
            store.close()


@app.command()
def resume(
    run_id: str | None = typer.Argument(None, help="Run ID to resume (omit to use most recent)"),
    steps: int | None = typer.Option(None, "--steps", "-s"),
    minutes: float | None = typer.Option(None, "--minutes", "-m"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    """Resume an interrupted or completed run."""
    store: NemoStore | None = None
    try:
        store = _open_store()
        config = _load_config()
        if run_id is None:
            runs = store.list_runs(limit=10)
            if not runs:
                raise RuntimeError("no runs available")
            _print_recent_runs(runs)
            selection = typer.prompt("Select a run to resume", default="1")
            try:
                idx = max(1, int(selection))
            except ValueError:
                idx = 1
            idx = min(idx, len(runs))
            run_id = str(runs[idx - 1]["run_id"])
        if store.get_run(run_id) is None:
            raise RuntimeError(f"run not found: {run_id}")
        bus = EventBus()
        bus.subscribe(DisplaySubscriber(verbose=verbose or config.verbose, quiet=quiet or config.quiet))
        bus.subscribe(UserHookSubscriber(config))
        engine = NemoEngine(store, config, bus)
        resumed_id = asyncio.run(engine.run(max_steps=steps, max_minutes=minutes, resume_run_id=run_id))
        console.print(f"[green]resumed[/green] {resumed_id}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]resume failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if store is not None:
            store.close()


@app.command()
def status() -> None:
    """Show current project dashboard."""
    store: NemoStore | None = None
    try:
        store = _open_store()
        datasets = store.get_datasets()
        latest = store.list_runs(limit=1)
        latest_run = latest[0] if latest else None
        table = Table(title="nemo status")
        table.add_column("Metric")
        table.add_column("Value")
        table.add_row("datasets", str(len(datasets)))
        table.add_row("insights", str(store.execute("SELECT COUNT(*) FROM insights").fetchone()[0]))
        table.add_row("frontier queued", str(store.count_frontier(status="queued")))
        table.add_row("contradictions", str(store.count_contradictions()))
        if latest_run:
            table.add_row("latest run", str(latest_run["run_id"]))
            table.add_row("latest status", str(latest_run["status"]))
            table.add_row("latest steps", str(latest_run["steps_completed"]))
            table.add_row("latest insights", str(latest_run["insights_created"]))
        console.print(table)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]status failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if store is not None:
            store.close()


@app.command()
def plan(
    steps: int | None = typer.Option(None, "--steps", "-s"),
    minutes: float | None = typer.Option(None, "--minutes", "-m"),
) -> None:
    """Alias for `run --plan`."""
    run(steps=steps, minutes=minutes, plan=True, verbose=False, quiet=False)


@app.command()
def brief(
    output: Path | None = typer.Option(None, "--output", "-o", help="Write markdown to file"),
    top: int = typer.Option(10, "--top", "-n", help="Number of top insights to include"),
) -> None:
    """Generate a markdown brief from recent results."""
    store: NemoStore | None = None
    try:
        store = _open_store()
        if output is not None:
            out = write_brief_report(store, output, top_n=top)
            console.print(f"[green]brief written[/green] {out}")
            return
        console.print(generate_brief_markdown(store, top_n=top))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]brief failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if store is not None:
            store.close()


@app.command()
def report(
    output_dir: Path = typer.Option(Path("reports"), "--output-dir", "-o", help="Output directory"),
    top: int = typer.Option(10, "--top", "-n", help="Number of top insights to include"),
) -> None:
    """Write a markdown brief to reports/."""
    store: NemoStore | None = None
    try:
        store = _open_store()
        latest_run = store.list_runs(limit=1)
        suffix = "latest"
        if latest_run:
            suffix = str(latest_run[0].get("run_id", "latest"))
        output_path = output_dir / f"brief_{suffix}.md"
        out = write_brief_report(store, output_path, top_n=top)
        console.print(f"[green]report written[/green] {out}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]report failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if store is not None:
            store.close()


@graph_app.command()
def stats() -> None:
    """Show evidence graph statistics."""
    store: NemoStore | None = None
    try:
        store = _open_store()
        insights = int(store.execute("SELECT COUNT(*) FROM insights").fetchone()[0])
        edges = int(store.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
        contradictions = int(store.execute("SELECT COUNT(*) FROM edges WHERE type = 'contradicts'").fetchone()[0])
        supports = int(store.execute("SELECT COUNT(*) FROM edges WHERE type = 'supports'").fetchone()[0])
        refines = int(store.execute("SELECT COUNT(*) FROM edges WHERE type = 'refines'").fetchone()[0])
        avg_conf = float(store.execute("SELECT AVG(confidence) FROM insights WHERE status = 'ok'").fetchone()[0] or 0.0)

        datasets = [str(row[0]) for row in store.execute("SELECT name FROM datasets").fetchall()]
        touched: set[str] = set()
        for row in store.execute("SELECT source_tables_json FROM insights WHERE source_tables_json IS NOT NULL").fetchall():
            touched.update(_json_list(row[0]))
        coverage_ratio = (len(touched) / len(datasets)) if datasets else 0.0

        table = Table(title="graph stats")
        table.add_column("Metric")
        table.add_column("Value")
        table.add_row("insight nodes", str(insights))
        table.add_row("edges", str(edges))
        table.add_row("supports edges", str(supports))
        table.add_row("contradicts edges", str(contradictions))
        table.add_row("refines edges", str(refines))
        table.add_row("avg confidence", f"{avg_conf:.3f}")
        table.add_row("table coverage", f"{len(touched)}/{len(datasets)} ({coverage_ratio:.1%})")
        console.print(table)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]graph stats failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if store is not None:
            store.close()


@graph_app.command()
def contradictions(top: int = typer.Option(10, "--top", "-n", help="Number of clusters to show")) -> None:
    """Show top unresolved contradiction clusters."""
    store: NemoStore | None = None
    try:
        store = _open_store()
        clusters = find_contradiction_clusters(store)
        if not clusters:
            console.print("[green]No contradiction clusters found.[/green]")
            return
        table = Table(title="graph contradictions")
        table.add_column("#", justify="right")
        table.add_column("Cluster Size", justify="right")
        table.add_column("Insights")
        table.add_column("Sample Claims")
        for idx, cluster in enumerate(clusters[: max(1, int(top))], start=1):
            insight_ids = [str(v) for v in cluster.get("insight_ids", [])]
            claims = [str(v) for v in cluster.get("claims", [])]
            table.add_row(
                str(idx),
                str(len(insight_ids)),
                ", ".join(insight_ids[:4]) + ("..." if len(insight_ids) > 4 else ""),
                " | ".join(claims[:2]) if claims else "-",
            )
        console.print(table)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]graph contradictions failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if store is not None:
            store.close()


def _coming_soon(command_name: str) -> None:
    console.print(f"[yellow]{command_name}[/yellow] is coming soon in a later sprint.")
    raise typer.Exit(code=0)


def _open_store() -> NemoStore:
    db_path = Path(".").resolve() / "nemo.duckdb"
    if not db_path.exists():
        raise RuntimeError(f"missing {db_path}; run `nemo init` first")
    return NemoStore(db_path)


def _load_config() -> NemoConfig:
    return NemoConfig.load(Path(".").resolve() / "nemo.toml")


def _print_recent_runs(runs: list[dict]) -> None:
    table = Table(title="recent runs")
    table.add_column("#", justify="right")
    table.add_column("Run ID")
    table.add_column("Status")
    table.add_column("Started")
    table.add_column("Steps", justify="right")
    table.add_column("Insights", justify="right")
    table.add_column("Frontier", justify="right")
    for idx, run in enumerate(runs, start=1):
        table.add_row(
            str(idx),
            str(run.get("run_id")),
            str(run.get("status")),
            str(run.get("started_at")),
            str(run.get("steps_completed")),
            str(run.get("insights_created")),
            str(run.get("frontier_size")),
        )
    console.print(table)


def _default_table_name(path: str) -> str:
    leaf = Path(path).stem
    if leaf and leaf not in {"*", "."}:
        return leaf
    return "dataset"


def _quote_ident(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _json_list(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(v) for v in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [part.strip() for part in raw.split(",") if part.strip()]
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    return []


def _check_db_exists(db_path: Path) -> tuple[bool, str]:
    if db_path.exists():
        return True, str(db_path)
    return False, f"missing {db_path}"


def _check_system_tables(db_path: Path) -> tuple[bool, str]:
    if not db_path.exists():
        return False, "database does not exist"
    try:
        store = NemoStore(db_path)
        missing = [table for table in SYSTEM_TABLES if not store.table_exists(table)]
        store.close()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if missing:
        return False, f"missing tables: {', '.join(sorted(missing))}"
    return True, f"{len(SYSTEM_TABLES)} tables present"


def _check_config(config_path: Path) -> tuple[bool, str]:
    try:
        NemoConfig.load(config_path)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if config_path.exists():
        return True, str(config_path)
    return True, "using defaults (nemo.toml not found)"


def _check_llm_key(config_path: Path) -> tuple[bool, str]:
    config = NemoConfig.load(config_path)
    key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
    if key:
        return True, "OPENAI_API_KEY available"
    return False, "set OPENAI_API_KEY in env or [llm].openai_api_key"


def _check_llm_ping(config_path: Path) -> tuple[bool, str]:
    config = NemoConfig.load(config_path)
    key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        return False, "skipped: no API key"
    try:
        client = OpenAI(api_key=key)
        models = client.models.list()
        first_model = next(iter(models.data), None)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if first_model is None:
        return True, "connected, model list empty"
    return True, f"connected ({first_model.id})"


def _check_has_datasets(db_path: Path) -> tuple[bool, str]:
    if not db_path.exists():
        return False, "database does not exist"
    try:
        store = NemoStore(db_path)
        count = store.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        store.close()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if count > 0:
        return True, f"{count} dataset(s) registered"
    return False, "no datasets loaded yet (expected before exploration)"


def _check_custom_generators(generators_dir: Path) -> tuple[bool, str]:
    if not generators_dir.exists():
        return True, "no .nemo/generators directory yet"
    py_files = sorted(generators_dir.glob("*.py"))
    if not py_files:
        return True, "no custom generators found"
    for file_path in py_files:
        try:
            source = file_path.read_text(encoding="utf-8")
            ast.parse(source)
        except SyntaxError as exc:
            return False, f"{file_path.name}: {exc.msg} (line {exc.lineno})"
    return True, f"{len(py_files)} generator file(s) valid"


if __name__ == "__main__":
    app()
