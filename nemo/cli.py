"""Typer CLI entrypoint for Nemo."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Callable

import typer
from openai import OpenAI
from rich.console import Console
from rich.table import Table

from nemo.config import NemoConfig, write_default_config
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
def add() -> None:
    _coming_soon("add")


@app.command()
def ls() -> None:
    _coming_soon("ls")


@app.command()
def schema() -> None:
    _coming_soon("schema")


@app.command()
def profile() -> None:
    _coming_soon("profile")


@app.command()
def run() -> None:
    _coming_soon("run")


@app.command()
def resume() -> None:
    _coming_soon("resume")


@app.command()
def status() -> None:
    _coming_soon("status")


@app.command()
def plan() -> None:
    _coming_soon("plan")


@app.command()
def brief() -> None:
    _coming_soon("brief")


@app.command()
def report() -> None:
    _coming_soon("report")


@graph_app.command()
def stats() -> None:
    _coming_soon("graph stats")


@graph_app.command()
def contradictions() -> None:
    _coming_soon("graph contradictions")


def _coming_soon(command_name: str) -> None:
    console.print(f"[yellow]{command_name}[/yellow] is coming soon in a later sprint.")
    raise typer.Exit(code=0)


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
