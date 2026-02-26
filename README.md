# Nemo

**Local-first AI data exploration agent.**

Nemo autonomously explores your datasets — generating SQL queries, executing them against a local DuckDB database, interpreting results with LLMs, and assembling an evidence graph of insights, contradictions, and thematic threads. Point it at CSV or Parquet files, give it a budget, and get back a structured brief of what it found.

## Features

- **Autonomous exploration** — dual-mode engine with LLM-driven strategic planning and deterministic generators
- **Evidence graph** — tracks relationships between insights (supports, contradicts, refines) and surfaces contradiction clusters
- **Thread cards** — groups related insights into thematic narratives
- **Cross-run learning** — retains lessons across exploration runs so future runs start smarter
- **Local-first** — all data stays on your machine in a DuckDB database; only LLM calls leave the box
- **Interactive TUI** — Rich-based terminal dashboard for browsing datasets, insights, and running exploration
- **Extensible** — drop custom generators into `.nemo/generators/` and hook shell commands into lifecycle events
- **Markdown reports** — auto-generated briefs with top insights, graph stats, and contradiction summaries

## Installation

Requires Python 3.11+.

```bash
git clone <repo-url> && cd Nemo
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="sk-..."
```

Or add it to a `.env` file in the project root.

## Quickstart

```bash
# Initialize a new project
nemo init

# Verify everything is wired up
nemo doctor

# Load the TPC-H demo dataset
nemo add --tpch --scale 0.01

# Run 15 exploration steps
nemo run --steps 15

# See what Nemo found
nemo brief
nemo graph stats
```

## Usage

### Adding data

```bash
nemo add data.csv                  # single CSV file
nemo add data.parquet              # Parquet file
nemo add "logs/*.csv"              # glob pattern
nemo add --tpch --scale 0.01      # TPC-H demo tables
nemo ls                            # list loaded datasets
nemo schema <table>                # inspect table schema
nemo profile <table>               # column-level statistics
```

### Running exploration

```bash
nemo run --steps 20                # budget by step count
nemo run --minutes 10              # budget by wall-clock time
nemo run --plan                    # dry run — generate and score frontier only
nemo resume                        # resume an interrupted run
nemo status                        # project dashboard
```

### Viewing results

```bash
nemo brief                         # print markdown brief to stdout
nemo brief --output brief.md       # write to file
nemo report                        # write brief to reports/ directory
nemo graph stats                   # evidence graph summary
nemo graph contradictions --top 5  # top contradiction clusters
```

### Interactive TUI

Launch the TUI by running `nemo` with no subcommand:

```bash
nemo
```

The TUI provides a dashboard view with commands for browsing datasets, insights, the evidence graph, and triggering exploration runs.

## Architecture

```
nemo/
├── cli.py               # Typer CLI entry point
├── engine.py            # Exploration engine (strategist + legacy loops)
├── config.py            # Configuration (nemo.toml)
├── events.py            # Event bus for decoupled components
├── executor/            # SQL compilation + query execution
├── graph/               # Evidence graph: linking, contradictions, threads, learnings
├── ingest/              # Data ingestion, profiling, join discovery
├── planner/             # Frontier generation, LLM strategist, scoring, scheduling
├── summarize/           # LLM summarization + claim canonicalization
├── store/               # DuckDB persistence layer + schema
├── report/              # Markdown brief generation
└── tui/                 # Rich interactive terminal UI
```

### Exploration loop

1. **Plan** — the strategist (LLM) or deterministic generators propose exploration actions and push them onto the frontier queue
2. **Score** — each frontier item is ranked by a multi-factor utility score (novelty, coverage, confidence gain, etc.)
3. **Execute** — the top-ranked action is compiled to SQL and run against DuckDB
4. **Interpret** — query results are sent to the LLM for structured summarization into claims, hypotheses, and confidence scores
5. **Link** — new insights are connected to the evidence graph with support/contradiction/refinement edges
6. **Reflect** — periodically, the strategist reviews progress and adjusts strategy

This loop repeats until the step or time budget is exhausted.

## Configuration

Nemo reads `nemo.toml` from the project root. Key sections:

| Section | Options | Description |
|---|---|---|
| `[budget]` | `max_steps`, `max_runtime_minutes`, `max_query_runtime_ms` | Exploration budget limits |
| `[scoring]` | `weight_novelty`, `weight_coverage`, `weight_confidence`, ... | Frontier ranking weights |
| `[llm]` | `model`, `openai_api_key` | LLM provider settings |
| `[exploration]` | `reflection_frequency`, `use_learnings`, `join_threshold` | Exploration behavior |
| `[hints]` | `metrics`, `dimensions`, `time_columns` | Domain hints for planners |
| `[hooks]` | `on_run_start`, `on_step_end`, `on_run_end`, ... | Shell hooks for lifecycle events |

Environment variable overrides: `NEMO_MAX_STEPS`, `NEMO_MAX_RUNTIME_MINUTES`, `NEMO_MAX_QUERY_RUNTIME_MS`.

## Custom generators

Place Python files in `.nemo/generators/`. Each file should expose a callable that receives planner context and returns a list of `FrontierItem` objects:

```python
def my_generator(ctx):
    return []
```

Nemo discovers and runs these automatically during frontier refresh.

## Testing

```bash
pip install -e .
python -m pytest -q
```

## Tech stack

- [DuckDB](https://duckdb.org/) — in-process analytical database
- [OpenAI](https://platform.openai.com/) — LLM summarization and strategic planning
- [Typer](https://typer.tiangolo.com/) — CLI framework
- [Rich](https://rich.readthedocs.io/) — terminal UI and formatting
- [Pydantic](https://docs.pydantic.dev/) — data validation

## License

Private — all rights reserved.
