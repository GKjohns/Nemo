# Nemo — Claude Code Project Guide

## What this is

Nemo is a local-first AI data exploration agent. It autonomously explores datasets by generating SQL, executing against DuckDB, interpreting results with LLMs, and building an evidence graph. Output is a structured brief with findings, contradictions, and hypothesis verdicts.

## Quick orientation

- **CLI entry:** `nemo/cli.py` — all user-facing commands via Typer
- **Engine:** `nemo/engine.py` — the main explore/exploit loop
- **Planner:** `nemo/planner/` — strategist (LLM), arbiter, validator, scoring, generators
- **Executor:** `nemo/executor/` — SQL compilation, agent execution, statistical analysis
- **Graph:** `nemo/graph/` — edge linking, contradiction clustering, thread cards, cross-run learnings
- **Store:** `nemo/store/db.py` — DuckDB persistence layer; schema in `nemo/store/schema.sql`
- **Ingest:** `nemo/ingest/` — CSV/Parquet loading, profiling, join discovery
- **Report:** `nemo/report/` — markdown brief generation
- **TUI:** `nemo/tui/` — Rich-based interactive dashboard
- **Config:** `nemo/config.py` reads `nemo.toml`

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
export OPENAI_API_KEY="sk-..."
nemo init
nemo add data.csv
nemo run --steps 20 --goal "What patterns exist in this data?"
nemo brief
```

## Running tests

```bash
pip install -e .
python -m pytest -q              # fast tests only
python -m pytest -m e2e          # end-to-end (slow, needs API key)
```

## Key conventions

- All data stays local in `nemo.duckdb` — only LLM API calls leave the machine
- The evidence graph (insights + edges) is the core analytical artifact
- Hypotheses go through validation steps: reproduce, segment, confound, counter
- Cross-run learnings persist so future runs start smarter
- Custom generators go in `.nemo/generators/`
- Shell hooks go in `nemo.toml` under `[hooks]`

## Skill instructions

See `SKILL.md` for detailed instructions on how to use Nemo as a Claude Code skill — when to invoke it, CLI commands, the exploration loop, and configuration.
