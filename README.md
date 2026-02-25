# Nemo

Local-first AI data exploration agent.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quickstart

```bash
nemo init
nemo doctor
nemo add --tpch --scale 0.01
nemo run --steps 15
nemo brief
nemo graph stats
```

To write reports to disk:

```bash
nemo report
```

## Core Commands

- `nemo add`: load CSV/Parquet files, glob patterns, or TPC-H demo tables.
- `nemo run`: execute exploration (`--steps`, `--minutes`, `--plan`).
- `nemo resume`: continue a prior run.
- `nemo status`: show datasets, run state, frontier and contradiction count.
- `nemo brief`: print markdown brief (`--output` to write a file).
- `nemo report`: write brief markdown into the `reports/` directory.
- `nemo graph stats`: summarize node/edge counts, coverage, confidence.
- `nemo graph contradictions --top N`: list top contradiction clusters.

## Configuration

Nemo reads `nemo.toml` from the current project.

Common options:

- `[budget].max_steps`: exploration step budget.
- `[budget].max_runtime_minutes`: wall-clock budget.
- `[budget].max_query_runtime_ms`: query timeout cap.
- `[scoring].weight_*`: utility weights for frontier ranking.
- `[llm].model`: LLM model used by summarization.
- `[exploration].use_learnings`: enable cross-run learning recall.
- `[hints].metrics`: user-defined key metrics used by planners/threads.
- `[hooks]`: shell hooks for lifecycle events.

Environment variables:

- `OPENAI_API_KEY`: OpenAI API key.
- `NEMO_MAX_STEPS`, `NEMO_MAX_RUNTIME_MINUTES`, `NEMO_MAX_QUERY_RUNTIME_MS`: optional runtime overrides.

## Custom Generators

Place generator files in `.nemo/generators/*.py`.

Each file should expose a callable that receives planner context and returns a list of `FrontierItem` objects:

```python
def my_generator(ctx):
    return []
```

Nemo automatically discovers and runs these generators during frontier refresh.
