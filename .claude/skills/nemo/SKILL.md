---
name: nemo
description: >
  Autonomous data investigation agent. Invoke when a user has a dataset (CSV, Parquet, or DuckDB)
  and asks an analytical question requiring multiple queries — metric changes, anomaly detection,
  root cause analysis, segmentation, or pattern discovery. Runs SQL against a local DuckDB database,
  builds an evidence graph, validates hypotheses, and produces an auditable brief.
when_to_use: >
  User asks "why did X change", "what's driving Y", "find anomalies in Z", or points at a dataset
  and wants rigorous analysis. Do NOT invoke for single-query answers, tasks without data, or
  dashboard/visualization-only work.
allowed-tools:
  - Bash(nemo *)
  - Bash(python *)
---

# Nemo — Autonomous Data Investigation Skill

---

## Setup

Nemo must be initialized in the project directory before use.

### First-time setup

```bash
# Navigate to the project directory
cd /path/to/project

# Create a virtual environment and install
python -m venv .venv
source .venv/bin/activate
pip install -e /path/to/nemo

# Initialize the project
nemo init

# Verify setup
nemo doctor
```

Requires `OPENAI_API_KEY` set in environment or in a `.env` file.

### Loading data

```bash
# Add CSV or Parquet files
nemo add data.csv
nemo add data.parquet
nemo add "logs/*.csv"               # glob pattern
nemo add --name custom_name data.csv  # custom table name

# Load TPC-H demo dataset for testing
nemo add --tpch --scale 0.01

# Verify what's loaded
nemo ls
nemo schema <table_name>
nemo profile <table_name>            # column-level stats
```

---

## Running an investigation

### Goal-directed (recommended for analytical questions)

```bash
nemo run --steps 20 --goal "Why did activation drop in March?"
nemo run --steps 15 --goal "Which accounts are at churn risk and why?"
nemo run --minutes 10 --goal "Find pricing anomalies in the supplier data"
```

The `--goal` flag steers the strategist toward the user's question. Without it, Nemo explores broadly.

### Open exploration

```bash
nemo run --steps 20          # budget by step count
nemo run --minutes 10        # budget by time
```

### Dry run

```bash
nemo run --plan              # generate & score frontier only, don't execute
nemo plan                    # alias for run --plan
```

### Resume a previous run

```bash
nemo resume                  # resume most recent run
nemo resume <run_id>         # resume specific run
nemo resume --goal "Dig into the outlier in segment X"  # resume with new focus
```

---

## Viewing results

```bash
nemo status                  # project dashboard (datasets, insights, frontier, contradictions)
nemo brief                   # markdown brief to stdout
nemo brief --output report.md  # write to file
nemo report                  # write to reports/ directory
nemo graph stats             # evidence graph summary
nemo graph contradictions    # top contradiction clusters
```

### Interactive TUI

```bash
nemo                         # launches Rich-based terminal dashboard
```

---

## How the engine works

Understanding the internals helps you use Nemo effectively and interpret its output.

### The exploration loop

1. **Plan** — the strategist (LLM) or deterministic generators propose exploration actions and push them onto a frontier queue
2. **Score** — each frontier item is ranked by multi-factor utility (novelty, coverage, confidence gain, feasibility, diversity)
3. **Execute** — top-ranked action is compiled to SQL and run against DuckDB
4. **Interpret** — results are sent to the LLM for structured summarization into claims, hypotheses, and confidence scores
5. **Link** — new insights are connected to the evidence graph with support/contradiction/refinement edges
6. **Reflect** — periodically, the strategist reviews progress and adjusts strategy
7. **Debrief** — at run end, LLM synthesizes findings into a narrative summary

### Explore vs. Exploit

The engine alternates between:
- **Explore** — discover new patterns, scan untouched tables/columns
- **Exploit** — validate existing hypotheses with reproduce/segment/confound/counter steps

The arbiter decides which phase to enter based on hypothesis maturity, stagnation, and diversity ratios.

### Deterministic generators

These produce frontier items without LLM calls:
- `SCHEMA_PROFILE` — baseline table stats
- `METRIC_TREND_SCAN` — time-series trends
- `CHANGEPOINT_DETECT` — sudden shifts
- `SEGMENT_COMPARE` — metric comparison across categories
- `TOP_GROUPS` / `OUTLIER_GROUPS` — high-leverage segments
- `CORRELATION_SCAN` — pairwise column correlations
- `DATA_QUALITY_CHECK` — nulls, duplicates, type issues
- `COVERAGE_EXPLORER` — join discovery between tables
- `ROBUSTNESS_CHECK` — result validity verification
- `CONTRADICTION_RESOLVE` — evidence chain disambiguation

### The evidence graph

Every insight is a node. Relationships between insights are edges:

| Edge type | Meaning |
|-----------|---------|
| `supports` | Evidence favors a prior finding |
| `contradicts` | Evidence argues against a prior finding |
| `refines` | Narrows or qualifies a prior finding |
| `depends_on` | Validity requires another finding to hold |

Contradiction clusters are auto-detected and can surface unresolved tensions in the data.

### Hypotheses

The strategist generates testable hypotheses from insights. Each hypothesis goes through validation steps (reproduce, segment, confound, counter) and receives a verdict: confirmed, refuted, or inconclusive.

---

## Configuration

Nemo reads `nemo.toml` from the project root. Key sections:

```toml
[budget]
max_steps = 100               # step limit per run
max_runtime_minutes = 30      # wall-clock limit
max_query_runtime_ms = 15000  # per-query timeout

[scoring]
weight_info_gain = 0.3        # how much new info?
weight_impact = 0.25          # how relevant to goal?
weight_novelty = 0.2          # unexplored tables/columns?
weight_feasibility = 0.15     # likely to succeed?
weight_diversity = 0.1        # spread across tables?

[llm]
model = "gpt-4o-mini"         # model for summarization
# plan_model = "gpt-4o"       # optional: separate model for strategist

[exploration]
reflect_every = 10            # steps between reflection
arbiter_interval = 3          # steps between explore/exploit decisions
max_validation_steps = 5      # max steps to validate a hypothesis
use_learnings = true          # recall patterns from prior runs

[hints]
goal = ""                     # default investigation goal
time_columns = []             # hint: which columns are temporal
important_dimensions = []     # hint: key categorical columns

[hooks]
# on_run_start = ["echo 'starting'"]
# on_step_end = ["echo 'step done'"]
# on_run_end = ["echo 'finished'"]
```

---

## Custom generators

Drop Python files in `.nemo/generators/` to add domain-specific exploration actions:

```python
def my_generator(ctx):
    """ctx has: store, config, recent_insights, datasets, frontier_items."""
    return []  # return list of FrontierItem objects
```

Files are auto-discovered and run during frontier refresh.

---

## Cross-run learning

Nemo extracts patterns from completed runs (generator hit rates, error patterns, metric signals) and recalls them in future runs. This means subsequent investigations start smarter. Controlled by `use_learnings = true` in config.

---

## Database schema

All data lives in a local DuckDB file (`nemo.duckdb`). System tables:

| Table | Purpose |
|-------|---------|
| `datasets` | Registered data sources |
| `insights` | Every finding (claim, SQL, confidence, source tables) |
| `edges` | Relationships between insights |
| `frontier` | Queued exploration actions |
| `runs` | Run metadata and debrief text |
| `hypotheses` | Testable claims with validation state |
| `thread_cards` | Thematic groupings of related insights |
| `notebooks` | Per-run strategist notebook |
| `learnings` | Cross-run extracted patterns |

---

## Typical workflow for Claude Code

When a user points you at a dataset and asks an analytical question:

1. **Check if Nemo is initialized** — look for `nemo.duckdb` in the project directory. If not, run `nemo init`.
2. **Load the data** — `nemo add <file>`. Verify with `nemo ls` and `nemo profile <table>`.
3. **Run the investigation** — `nemo run --steps 20 --goal "<user's question>"`. Use `--verbose` if the user wants to see what's happening.
4. **Review results** — `nemo brief` for the summary. `nemo graph stats` for the evidence graph. `nemo graph contradictions` if there are unresolved tensions.
5. **Iterate** — if the user has follow-up questions, `nemo resume --goal "<new question>"` to build on what was already found.
6. **Export** — `nemo report` to write a markdown brief to `reports/`.

For quick data questions that don't need the full investigation loop, just write SQL directly against the DuckDB file using Python or the DuckDB CLI.
