# PyNemo Implementation Plan

**Date:** 2026-02-25  
**Status:** Not Started  
**Author:** AI Assistant

## Overview

Build **PyNemo** — a local-first, long-running AI insight agent distributed as a Python CLI. Users point it at data (CSV, Parquet, or TPC-H demo), let it run overnight, and get a brief of discoveries in the morning. No cloud upload, no SaaS — just a single `nemo.duckdb` file and a stream of reproducible insights.

### Goals
- Python package with a Typer CLI: `nemo init`, `nemo add`, `nemo run`, `nemo brief`
- DuckDB as the single storage + analytics engine (system tables + user data in one file)
- 11 frontier generators that propose experiments, scored and deduplicated
- An outer loop: generate → score → select → execute → summarize → link → repeat
- An evidence graph of insights with supports/contradicts/refines edges
- Morning brief markdown report with top findings, contradictions, and open questions
- TPC-H demo that produces a meaningful brief in under 20 minutes

### Design Influences from Claude Code

Several UX and architectural patterns are borrowed from [Claude Code](https://github.com/anthropics/claude-code) — Anthropic's agentic CLI tool — adapted for the graph-exploration paradigm:

| Claude Code Pattern | PyNemo Adaptation |
|---|---|
| **Session resume** (`/resume` with picker) | `nemo resume` — list recent runs, pick one, continue from persisted frontier |
| **Doctor / diagnostics** (`/doctor`) | `nemo doctor` — verify DuckDB, LLM key, config, system tables |
| **Plan mode** (think before acting) | `nemo run --plan` — generate + score frontier, show what *would* run, don't execute |
| **Hooks system** (PreToolUse / PostToolUse) | Event bus with 17 typed events; user-defined hooks subscribe to any event type |
| **Skills / Plugins** (user-defined extensions) | Custom generators — drop a `.py` file in `.nemo/generators/`, auto-loaded |
| **Rich live display** (spinners, status bar) | Phased progress: status bar + collapsible step details + confidence badges |
| **Ralph loop** (autonomous iteration with self-correction) | Error feedback in working memory; saturation detection as a natural stop signal |
| **Agent memory** (persistent learnings across sessions) | `learnings` table — cross-run patterns (good joins, noisy columns, useful metrics) |
| **Verbose / quiet modes** | `--verbose` (full SQL + results + reasoning) / `--quiet` (summary only) |
| **Context compaction** (summarize when context grows) | Working memory refresh — periodically re-summarize graph state into compact form |

These patterns don't change Nemo's core graph exploration paradigm. They improve the developer experience of a long-running autonomous loop: making it observable, resumable, extensible, and self-improving.

### Architecture (High-Level)

```
┌──────────────────────────────────────────────────────────────┐
│                         CLI (Typer)                          │
│  nemo init · add · ls · profile · run · resume · brief       │
│  nemo doctor · nemo graph stats · nemo graph contradictions  │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                      Outer Loop                              │
│                                                              │
│  ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌───────────────┐   │
│  │ Planner │→ │Executor │→ │Summarizer│→ │ Graph Linker  │   │
│  │ (gen +  │  │ (compile│  │ (LLM →   │  │ (edges +      │   │
│  │  score) │  │  + run) │  │ insight) │  │ contradictions│   │
│  └─────────┘  └─────────┘  └──────────┘  └───────────────┘   │
│       ↑            │             │              │            │
│  ┌────┴────────────┴─────────────┴──────────────┴─────────┐  │
│  │ Event Bus (emit at every state transition)             │  │
│  │ → run:* · frontier:* · step:* · insight:* · edge:* ... │  │
│  └────┬────────────┬─────────────┬────────────────────────┘  │
│       │            │             │                           │
│  ┌────▼────┐  ┌────▼─────┐  ┌──-─▼─────────────────────┐     │
│  │ Display │  │ User     │  │ Future: WebSocket/SSE,   │     │
│  │ (Rich)  │  │ Hooks    │  │ Postgres, Supabase, etc. │     │
│  └─────────┘  └──────────┘  └──────────────────────────┘     │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Working Memory: schema summary, recent insights,       │  │
│  │ thread card, graph neighborhood, error patterns,       │  │
│  │ cross-run learnings                                    │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌───────────────────────────────┐                           │
│  │ Custom Generators (.nemo/gen/)│                           │
│  │ Auto-discovered Python files  │                           │
│  │ Same signature as built-ins   │                           │
│  └───────────────────────────────┘                           │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                     DuckDB (nemo.duckdb)                     │
│                                                              │
│  System tables: datasets · insights · edges · frontier ·     │
│                 runs · thread_cards · learnings              │
│  User tables:   loaded from CSV / Parquet / TPC-H            │
└──────────────────────────────────────────────────────────────┘
```

---

## Sprint 1: Project Scaffold + Storage Layer [✅ Completed]

Set up the Python package, CLI entrypoint, DuckDB system tables, config parsing, and the `nemo init` command. Everything subsequent builds on this foundation.

### 1.1 Package Structure + Dependencies [✅ Completed]

**File:** `pyproject.toml`

Standard Python packaging with Typer for CLI, DuckDB for storage, and a few utilities.

```toml
[project]
name = "pynemo"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "typer[all]",
    "duckdb",
    "rich",
    "tomli-w",
    "openai",
    "pydantic",
]

[project.scripts]
nemo = "nemo.cli:app"
```

Initial file tree:

```
nemo/
  __init__.py
  cli.py                  # Typer CLI entrypoint
  config.py               # NemoConfig, profiles, budgets
  engine.py               # Outer loop orchestrator
  events.py               # Event types, EventBus, subscriber protocol
  hooks.py                # User-defined hook subscriber (shell/Python)
  display.py              # Rich live display subscriber (status bar, step renderer)
  store/
    __init__.py
    db.py                 # DuckDB connection + migrations
    schema.sql            # System table DDL
    migrations/           # Optional versioned migrations
  ingest/
    __init__.py
    add.py                # add dataset(s), create tables/views
    profile.py            # Schema + stats profiling
    joins.py              # Join key discovery
  planner/
    __init__.py
    generators.py         # Frontier generators (built-in)
    loader.py             # Custom generator discovery (.nemo/generators/)
    scoring.py            # Scoring function (utility)
    dedupe.py             # Avoid duplicates
    scheduler.py          # Pick next action(s)
  executor/
    __init__.py
    compile.py            # Action → SQL plan
    run.py                # Execute query, capture timings
  summarize/
    __init__.py
    summarize.py          # Results → insight node (LLM)
    canonicalize.py       # Structured hypothesis/claim format
  graph/
    __init__.py
    link.py               # supports/contradicts/refines edges
    contradictions.py     # Detect contradiction clusters
    threads.py            # Thread cards (v1 scaffold)
    learnings.py          # Cross-run memory (record + recall)
  report/
    __init__.py
    brief.py              # Morning brief markdown generator
    render.py             # Optional HTML later
tests/
  __init__.py
  conftest.py             # Shared fixtures (tmp DuckDB, TPC-H scale 0.01)
  test_store.py
  test_ingest.py
  test_planner.py
  test_executor.py
  test_summarize.py
  test_graph.py
  test_report.py
  test_golden.py          # End-to-end TPC-H golden test
examples/
  tpch_quickstart.md
pyproject.toml
README.md

# Per-project user directory (created by `nemo init`)
.nemo/
  generators/             # User-defined generator .py files (auto-loaded)
  hooks/                  # User-defined hook scripts
```

### 1.2 DuckDB System Tables

**File:** `nemo/store/schema.sql`

All Nemo metadata lives in DuckDB alongside user data. One file, zero external dependencies.

```sql
-- 1) datasets
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id   VARCHAR PRIMARY KEY,
    name         VARCHAR NOT NULL,
    source_uri   VARCHAR NOT NULL,
    format       VARCHAR NOT NULL DEFAULT 'csv',   -- csv / parquet / view / tpch
    created_at   TIMESTAMP NOT NULL DEFAULT now(),
    notes        VARCHAR,
    schema_json  VARCHAR                            -- JSON: column profiles
);

-- 2) insights
CREATE TABLE IF NOT EXISTS insights (
    insight_id             VARCHAR PRIMARY KEY,
    created_at             TIMESTAMP NOT NULL DEFAULT now(),
    run_id                 VARCHAR,                  -- which run produced this
    thread_id              VARCHAR,
    title                  VARCHAR NOT NULL,
    question               VARCHAR NOT NULL,
    hypothesis_struct_json VARCHAR,                 -- canonical hypothesis JSON
    sql                    VARCHAR NOT NULL,
    result_summary_json    VARCHAR NOT NULL,        -- counts, aggregates, etc.
    result_sample_json     VARCHAR,                 -- small sample rows
    claim                  VARCHAR NOT NULL,
    claim_struct_json      VARCHAR,                 -- canonical claim JSON
    confidence             DOUBLE NOT NULL DEFAULT 0.5,
    effect_size            DOUBLE,
    coverage               DOUBLE,
    cost_ms                INTEGER,
    source_tables_json     VARCHAR,                 -- JSON: tables referenced
    tags_json              VARCHAR,                 -- JSON: string[]
    status                 VARCHAR NOT NULL DEFAULT 'ok',  -- ok / error
    error_text             VARCHAR
);

-- 3) edges
CREATE TABLE IF NOT EXISTS edges (
    edge_id          VARCHAR PRIMARY KEY,
    created_at       TIMESTAMP NOT NULL DEFAULT now(),
    from_insight_id  VARCHAR NOT NULL REFERENCES insights(insight_id),
    to_insight_id    VARCHAR NOT NULL REFERENCES insights(insight_id),
    type             VARCHAR NOT NULL,              -- supports / contradicts / refines / depends_on / duplicate_of
    weight           DOUBLE NOT NULL DEFAULT 0.5,
    rationale        VARCHAR
);

-- 4) frontier
CREATE TABLE IF NOT EXISTS frontier (
    action_id            VARCHAR PRIMARY KEY,
    created_at           TIMESTAMP NOT NULL DEFAULT now(),
    run_id               VARCHAR,                    -- which run queued this
    thread_id            VARCHAR,
    action_type          VARCHAR NOT NULL,           -- SCHEMA_PROFILE, METRIC_TREND_SCAN, etc.
    payload_json         VARCHAR NOT NULL,            -- JSON: action parameters
    score                DOUBLE NOT NULL DEFAULT 0.0,
    status               VARCHAR NOT NULL DEFAULT 'queued',  -- queued / running / done / skipped / error
    last_error           VARCHAR,
    depends_on_action_id VARCHAR,
    dedupe_key           VARCHAR NOT NULL
);

-- 5) runs
CREATE TABLE IF NOT EXISTS runs (
    run_id            VARCHAR PRIMARY KEY,
    started_at        TIMESTAMP NOT NULL DEFAULT now(),
    ended_at          TIMESTAMP,
    status            VARCHAR NOT NULL DEFAULT 'running',  -- running / completed / interrupted / error
    config_json       VARCHAR NOT NULL,
    steps_completed   INTEGER NOT NULL DEFAULT 0,
    insights_created  INTEGER NOT NULL DEFAULT 0,
    errors            INTEGER NOT NULL DEFAULT 0,
    frontier_size     INTEGER NOT NULL DEFAULT 0,
    notes             VARCHAR
);

-- 6) thread_cards (v0 stub)
CREATE TABLE IF NOT EXISTS thread_cards (
    thread_id              VARCHAR PRIMARY KEY,
    updated_at             TIMESTAMP NOT NULL DEFAULT now(),
    title                  VARCHAR NOT NULL,
    summary_text           VARCHAR,
    key_insight_ids_json   VARCHAR,                  -- JSON: string[]
    open_questions_json    VARCHAR,                  -- JSON: string[]
    contradictions_json    VARCHAR                   -- JSON: string[]
);

-- 7) learnings (cross-run memory — inspired by Claude Code's auto-memory)
CREATE TABLE IF NOT EXISTS learnings (
    learning_id   VARCHAR PRIMARY KEY,
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    run_id        VARCHAR,
    category      VARCHAR NOT NULL,                 -- join_quality / noisy_column / useful_metric / error_pattern / generator_hit_rate
    subject       VARCHAR NOT NULL,                 -- what this learning is about (table.column, generator name, etc.)
    detail        VARCHAR NOT NULL,                 -- the learning itself
    confidence    DOUBLE NOT NULL DEFAULT 0.5,
    times_confirmed INTEGER NOT NULL DEFAULT 1      -- reinforced across runs
);
```

The `learnings` table is inspired by Claude Code's automatic memory system. As Nemo runs, it records patterns: "joins on `orders.o_custkey → customer.c_custkey` always succeed," "column `comment` is free-text and always produces noisy results," "CORRELATION_SCAN on this dataset yields low-confidence insights." These learnings feed back into generators and scoring in subsequent runs, making Nemo smarter over time without any user intervention.

### 1.3 Database Connection + Migration Runner

**File:** `nemo/store/db.py`

Thin wrapper around DuckDB. Opens `nemo.duckdb` in the project directory, applies schema on first run, and exposes a connection accessor.

```python
class NemoStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = duckdb.connect(str(db_path))

    def initialize(self) -> None:
        """Apply schema.sql to create system tables."""

    def execute(self, sql: str, params=None) -> duckdb.DuckDBPyRelation:
        """Execute a query and return results."""

    def insert_dataset(self, ...) -> str: ...
    def insert_insight(self, ...) -> str: ...
    def insert_edge(self, ...) -> str: ...
    def insert_frontier_item(self, ...) -> str: ...
    def insert_run(self, ...) -> str: ...

    def get_frontier_queue(self, status='queued', limit=50) -> list[dict]: ...
    def get_recent_insights(self, limit=20) -> list[dict]: ...
    def get_edges_for_insight(self, insight_id: str) -> list[dict]: ...
    def get_datasets(self) -> list[dict]: ...

    def close(self) -> None: ...
```

### 1.4 Config System

**File:** `nemo/config.py`

Parse `nemo.toml` and provide defaults. Users override any of these.

```python
@dataclass
class NemoConfig:
    # Budget
    max_steps: int = 100
    max_runtime_minutes: int = 30
    max_query_runtime_ms: int = 15000
    max_scan_rows: int | None = None
    saturation_threshold: float = 0.15  # stop if top frontier score drops below this

    # Scoring weights
    weight_info_gain: float = 0.3
    weight_impact: float = 0.25
    weight_novelty: float = 0.2
    weight_feasibility: float = 0.15
    weight_diversity: float = 0.1

    # LLM
    model: str = "gpt-4.1-mini"
    openai_api_key: str | None = None

    # Exploration
    reflect_every: int = 10
    max_actions_per_thread: int = 5
    join_confidence_threshold: float = 0.7
    use_learnings: bool = True        # incorporate cross-run memory

    # User-defined hints
    time_columns: list[str] = field(default_factory=list)
    key_metrics: dict[str, str] = field(default_factory=dict)   # name → SQL expr
    important_dimensions: list[str] = field(default_factory=list)
    join_overrides: dict = field(default_factory=dict)

    # Event hooks (subscribe commands to event types)
    hooks: dict = field(default_factory=dict)
    # Example in nemo.toml:
    # [hooks]
    # "step:started" = ["python .nemo/hooks/validate_sql.py"]
    # "insight:created" = ["python .nemo/hooks/notify_slack.py"]
    # "run:completed" = ["python .nemo/hooks/export_report.py"]
    # "*" = ["python .nemo/hooks/sse_bridge.py"]  # all events → frontend

    # Output
    verbose: bool = False
    quiet: bool = False

    @classmethod
    def load(cls, path: Path) -> "NemoConfig": ...

    def to_dict(self) -> dict: ...
```

Example `nemo.toml`:

```toml
[budget]
max_steps = 100
max_runtime_minutes = 30
saturation_threshold = 0.15

[llm]
model = "gpt-4.1-mini"

[hints]
time_columns = ["o_orderdate", "l_shipdate"]
important_dimensions = ["n_name", "r_name", "p_type"]

[hints.metrics]
revenue = "l_extendedprice * (1 - l_discount)"
total_cost = "l_extendedprice * (1 - l_discount) * (1 + l_tax)"

[hooks]
# Map event types (or "*" for all) to commands.
# Commands receive the full event as JSON on stdin.
# "step:started" = ["python .nemo/hooks/validate_sql.py"]
# "insight:created" = ["python .nemo/hooks/notify_slack.py"]
# "run:completed" = ["python .nemo/hooks/export_report.py"]
# "*" = ["python .nemo/hooks/sse_bridge.py"]
```

### 1.5 CLI Scaffold + `nemo init` + `nemo doctor`

**File:** `nemo/cli.py`

Typer entrypoint with all commands stubbed. `nemo init` and `nemo doctor` are the first real implementations.

```python
app = typer.Typer(name="nemo", help="Nemo — local-first AI data exploration agent")

@app.command()
def init(path: Path = typer.Argument(".", help="Project directory")):
    """Initialize a new Nemo project. Creates nemo.duckdb, nemo.toml, and .nemo/ directory."""

@app.command()
def doctor():
    """Verify Nemo setup: database, config, LLM connectivity, system tables."""

@app.command()
def add(...): ...          # Sprint 2

@app.command()
def ls(): ...              # Sprint 2

@app.command()
def schema(...): ...       # Sprint 2

@app.command()
def profile(...): ...      # Sprint 2

@app.command()
def run(...): ...          # Sprint 4

@app.command()
def resume(...): ...       # Sprint 4

@app.command()
def status(): ...          # Sprint 4

@app.command()
def plan(...): ...         # Sprint 4

@app.command()
def brief(...): ...        # Sprint 5

@app.command()
def report(...): ...       # Sprint 5

graph_app = typer.Typer(help="Evidence graph commands")
app.add_typer(graph_app, name="graph")

@graph_app.command()
def stats(): ...           # Sprint 5

@graph_app.command()
def contradictions(...): ... # Sprint 5
```

**`nemo init`** behavior:
- Create `nemo.duckdb` via `NemoStore.initialize()`
- Write default `nemo.toml` if not present
- Create `.nemo/` directory with `generators/` and `hooks/` subdirectories
- Print confirmation with Rich

**`nemo doctor`** (inspired by Claude Code's `/doctor` command):
Runs a series of health checks and reports pass/fail with Rich formatting:

```python
def doctor():
    """Verify Nemo setup."""
    checks = [
        ("DuckDB file exists",        check_db_exists),
        ("System tables initialized",  check_system_tables),
        ("Config file parseable",       check_config),
        ("OpenAI API key set",          check_llm_key),
        ("LLM connectivity",           check_llm_ping),      # quick model list call
        ("Datasets loaded",            check_has_datasets),
        ("Custom generators valid",    check_custom_generators),
    ]
    # Each check returns (bool, detail_message)
    # Render as a Rich table with ✓/✗ status
```

### Sprint 1 Deliverables
- [x] `pyproject.toml` with all dependencies, `nemo` script entrypoint
- [x] Full package directory structure created (including `.nemo/generators/` and `.nemo/hooks/`)
- [x] DuckDB system tables defined in `schema.sql` (7 tables including `learnings`)
- [x] `NemoStore` class connects to DuckDB and applies schema
- [x] `NemoConfig` dataclass parses `nemo.toml` with sensible defaults (including hooks + verbosity)
- [x] `nemo init` creates `nemo.duckdb` + `nemo.toml` + `.nemo/` directory
- [x] `nemo doctor` runs health checks and reports status
- [x] All other CLI commands stubbed (raise `NotImplementedError` or print "coming soon")
- [x] `tests/test_store.py` — store initializes, inserts, and queries all system tables including `learnings`

---

## Sprint 2: Data Ingestion + Schema Intelligence

Load user data into DuckDB, generate schema profiles, discover join keys, and expose inspection commands. After this sprint, a user can `nemo add` files and understand their data before running exploration.

### 2.1 Dataset Ingestion

**File:** `nemo/ingest/add.py`

Load CSV, Parquet, or TPC-H data into DuckDB as persistent tables. Record metadata in the `datasets` system table.

```python
def add_file(store: NemoStore, path: Path, name: str, format: str = "auto") -> str:
    """
    Load a file into DuckDB as a named table.
    - CSV:     CREATE TABLE <name> AS SELECT * FROM read_csv_auto('<path>')
    - Parquet: CREATE TABLE <name> AS SELECT * FROM read_parquet('<path>')
    - Auto:    detect from extension
    Returns dataset_id.
    """

def add_tpch(store: NemoStore, scale: float = 1.0) -> list[str]:
    """
    Load TPC-H demo tables using DuckDB's built-in generator.
    - CALL dbgen(sf=<scale>)
    - Records each generated table (customer, orders, lineitem, etc.) as a dataset
    Returns list of dataset_ids.
    """

def add_glob(store: NemoStore, pattern: str, name: str, format: str = "csv") -> str:
    """
    Load multiple files matching a glob pattern as a single table.
    - CREATE TABLE <name> AS SELECT * FROM read_csv_auto('<pattern>')
    """
```

### 2.2 Schema Profiling

**File:** `nemo/ingest/profile.py`

Generate column-level statistics for a dataset table. This powers both the `nemo profile` command and the working memory that generators use.

```python
@dataclass
class ColumnProfile:
    name: str
    dtype: str
    nullable: bool
    null_count: int
    null_pct: float
    distinct_count: int
    cardinality_ratio: float      # distinct / total
    sample_values: list[Any]
    min_val: Any | None           # numeric / date
    max_val: Any | None
    mean: float | None
    stddev: float | None
    p25: float | None
    p50: float | None
    p75: float | None

@dataclass
class TableProfile:
    name: str
    row_count: int
    columns: list[ColumnProfile]

def profile_table(store: NemoStore, table_name: str) -> TableProfile:
    """
    Query DuckDB metadata + aggregations to build a full table profile.
    Uses SUMMARIZE, DESCRIBE, and targeted agg queries.
    """

def profile_all(store: NemoStore) -> list[TableProfile]:
    """Profile every user dataset table."""
```

### 2.3 Join Key Discovery

**File:** `nemo/ingest/joins.py`

Heuristic discovery of candidate join keys across tables. Conservative — records suggestions but doesn't auto-join.

```python
@dataclass
class JoinCandidate:
    table_a: str
    column_a: str
    table_b: str
    column_b: str
    confidence: float             # 0–1
    uniqueness_a: float           # distinct/total for col_a
    uniqueness_b: float
    overlap_ratio: float          # intersecting values / union
    rationale: str

def discover_joins(store: NemoStore, profiles: list[TableProfile]) -> list[JoinCandidate]:
    """
    For each pair of tables, find candidate join columns:
    1. Name matching: id, *_id, shared column names
    2. Uniqueness check: at least one side should be high-cardinality
    3. Overlap sampling: sample 1000 values from each and check intersection
    4. Type compatibility: both sides must be same or castable type
    5. Null rate: reject if either side > 50% null
    Returns sorted by confidence descending.
    """
```

### 2.4 Inspection CLI Commands

Implement the `nemo ls`, `nemo schema`, and `nemo profile` commands.

**`nemo ls`** — List all datasets with name, row count, column count, source, added date. Rich table output.

**`nemo schema <table>`** — Show columns, types, nullable flags for a table. Rich table output.

**`nemo profile <table>`** — Run `profile_table()` and render results as a Rich table with per-column stats (nulls, distinct, min/max, percentiles, sample values).

### 2.5 Wire `nemo add` Command

Update `cli.py` to implement the `add` command:

```python
@app.command()
def add(
    path: str = typer.Argument(None, help="Path to CSV/Parquet file or glob"),
    name: str = typer.Option(None, "--name", "-n", help="Table name"),
    format: str = typer.Option("auto", "--format", "-f", help="File format"),
    tpch: bool = typer.Option(False, "--tpch", help="Load TPC-H demo data"),
    scale: float = typer.Option(1.0, "--scale", help="TPC-H scale factor"),
):
    """Add a dataset to the project."""
```

### Sprint 2 Deliverables
- [x] `nemo add path/to/file.csv --name mytable` loads CSV into DuckDB and records metadata
- [x] `nemo add path/to/*.parquet --name events` loads Parquet (glob support)
- [x] `nemo add --tpch --scale 1` generates TPC-H tables via DuckDB's `dbgen`
- [x] `nemo ls` lists all loaded datasets as a formatted table
- [x] `nemo schema <table>` shows columns and types
- [x] `nemo profile <table>` shows full column-level stats (nulls, distribution, samples)
- [x] Join discovery runs on all table pairs and produces ranked `JoinCandidate` list
- [x] `tests/test_ingest.py` — add CSV, add TPC-H, verify table creation and profiles
- [x] `tests/test_joins.py` — join discovery on TPC-H returns expected candidates (e.g., `orders.o_custkey → customer.c_custkey`)

---

## Sprint 3: Planner — Generators, Scoring, Scheduling [✅ Completed]

Build the frontier system: generators propose experiments, deduplication filters noise, scoring ranks by utility, and the scheduler picks the next action. After this sprint the planner can produce a prioritized queue of actions from any dataset state.

### 3.1 Frontier Item Model [✅ Completed]

Use Pydantic models for frontier items:

```python
class FrontierItem(BaseModel):
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    run_id: str | None = None     # Set when persisted to a run
    action_type: str              # SCHEMA_PROFILE, METRIC_TREND_SCAN, etc.
    payload: dict                 # Action-specific parameters
    score: float = 0.0
    status: str = "queued"
    last_error: str | None = None
    thread_id: str | None = None
    depends_on_action_id: str | None = None
    dedupe_key: str               # Semantic dedup identifier
    rationale: str = ""           # Why this action was proposed
```

### 3.2 Frontier Generators [✅ Completed]

**File:** `nemo/planner/generators.py`

Each generator is a function that takes the current state (profiles, recent insights, frontier) and yields `FrontierItem`s. Start with 11 generators:

```python
GeneratorContext = namedtuple("GeneratorContext", [
    "store", "profiles", "recent_insights", "join_candidates", "config"
])

def gen_schema_profile(ctx: GeneratorContext) -> list[FrontierItem]:
    """SCHEMA_PROFILE — profile tables/columns not yet profiled."""

def gen_metric_trend(ctx: GeneratorContext) -> list[FrontierItem]:
    """METRIC_TREND_SCAN — time-series trend analysis on numeric columns with a time column."""

def gen_changepoint(ctx: GeneratorContext) -> list[FrontierItem]:
    """CHANGEPOINT_DETECT — detect sudden changes in key metrics over time windows."""

def gen_segment_compare(ctx: GeneratorContext) -> list[FrontierItem]:
    """SEGMENT_COMPARE — compare a metric across top values of a categorical dimension."""

def gen_top_groups(ctx: GeneratorContext) -> list[FrontierItem]:
    """TOP_GROUPS — find the highest/lowest groups for a metric by a dimension."""

def gen_outlier_groups(ctx: GeneratorContext) -> list[FrontierItem]:
    """OUTLIER_GROUPS — extreme values by group or row-level anomalies."""

def gen_correlation_scan(ctx: GeneratorContext) -> list[FrontierItem]:
    """CORRELATION_SCAN — pairwise correlations among numeric columns."""

def gen_data_quality(ctx: GeneratorContext) -> list[FrontierItem]:
    """DATA_QUALITY_CHECK — null spikes, duplicates, missing join keys."""

def gen_coverage_explorer(ctx: GeneratorContext) -> list[FrontierItem]:
    """COVERAGE_EXPLORER — target under-explored columns/tables."""

def gen_robustness_check(ctx: GeneratorContext) -> list[FrontierItem]:
    """ROBUSTNESS_CHECK — verify a prior claim with alternate definition or slice."""

def gen_contradiction_resolve(ctx: GeneratorContext) -> list[FrontierItem]:
    """CONTRADICTION_RESOLVE — target unresolved contradiction clusters and propose disambiguating tests."""

ALL_GENERATORS = [
    gen_schema_profile, gen_metric_trend, gen_changepoint,
    gen_segment_compare, gen_top_groups, gen_outlier_groups,
    gen_correlation_scan, gen_data_quality, gen_coverage_explorer,
    gen_robustness_check, gen_contradiction_resolve,
]

def run_generators(ctx: GeneratorContext) -> list[FrontierItem]:
    """Run all generators and return combined frontier items."""
```

Generators are deterministic given the same state — they propose actions based on schema structure, column types, and gaps in existing insights. No LLM calls in the generator phase.

### 3.3 Custom Generator Loading (Plugin System) [✅ Completed]

**File:** `nemo/planner/loader.py`

Inspired by Claude Code's skills/plugins system, users can extend Nemo with custom generators. Any `.py` file dropped in `.nemo/generators/` that exports a function matching the generator signature is auto-discovered and added to the generator pool.

```python
def load_custom_generators(generators_dir: Path) -> list[Callable]:
    """
    Scan .nemo/generators/ for Python files.
    Each file must export a function with signature:
        def generate(ctx: GeneratorContext) -> list[FrontierItem]

    Returns list of callables to append to ALL_GENERATORS.
    Invalid files are logged as warnings but don't crash the run.
    """

def get_all_generators(generators_dir: Path | None = None) -> list[Callable]:
    """Return built-in generators + any custom generators."""
```

Example custom generator (`.nemo/generators/weekly_cohort.py`):

```python
from nemo.planner.generators import GeneratorContext, FrontierItem

def generate(ctx: GeneratorContext) -> list[FrontierItem]:
    """Compare metrics across weekly cohorts for all date columns."""
    items = []
    for profile in ctx.profiles:
        date_cols = [c for c in profile.columns if "date" in c.dtype.lower()]
        for col in date_cols:
            for metric_name in ctx.config.key_metrics:
                items.append(FrontierItem(
                    action_type="WEEKLY_COHORT",
                    payload={"table": profile.name, "date_col": col.name, "metric": metric_name},
                    dedupe_key=f"weekly_cohort:{profile.name}.{col.name}:{metric_name}",
                    rationale=f"Compare {metric_name} across weekly cohorts by {col.name}",
                ))
    return items
```

### 3.4 Deduplication [✅ Completed]

**File:** `nemo/planner/dedupe.py`

Filter out duplicate or redundant proposed actions.

```python
def dedupe_frontier(
    new_items: list[FrontierItem],
    existing_keys: set[str],       # dedupe_keys from frontier table (done + queued)
    recent_insight_keys: set[str], # derived keys from recent insights
) -> list[FrontierItem]:
    """
    Remove items where:
    1. dedupe_key matches a recently completed or queued action
    2. dedupe_key matches a key derivable from a recent insight
    3. Exact payload match against existing items
    """
```

### 3.5 Scoring Function [✅ Completed]

**File:** `nemo/planner/scoring.py`

Score each frontier item by utility. Weights are configurable via `NemoConfig`. Incorporates cross-run learnings when available.

```python
def score_item(
    item: FrontierItem,
    ctx: GeneratorContext,
    learnings: list[dict] | None = None,
) -> float:
    """
    Score = w1 * info_gain_proxy
          + w2 * impact_proxy
          + w3 * novelty
          + w4 * feasibility
          + w5 * diversity_bonus
          + learning_adjustment

    - info_gain_proxy: higher for actions in areas with few insights
    - impact_proxy: higher for actions targeting known high-cardinality or high-variance columns
    - novelty: distance from the nearest existing insight (table + column coverage)
    - feasibility: penalize actions likely to be slow (large tables, complex joins)
    - diversity_bonus: boost actions in under-represented threads / action types
    - learning_adjustment: boost/penalize based on cross-run memory
      (e.g., boost if prior runs found this column interesting,
       penalize if prior runs found this generator noisy for this table)
    """

def score_frontier(items: list[FrontierItem], ctx: GeneratorContext) -> list[FrontierItem]:
    """Score all items, sort descending, and return."""
```

### 3.6 Scheduler + Saturation Detection [✅ Completed]

**File:** `nemo/planner/scheduler.py`

Pick the next action(s) subject to budget constraints. Includes **saturation detection** — inspired by the Ralph Wiggum pattern's completion criteria. Instead of only stopping at hard limits (step count, time), Nemo can also recognize when the frontier is exhausted: all remaining actions score below the saturation threshold, meaning further exploration is unlikely to yield novel insights.

```python
def select_next(
    store: NemoStore,
    config: NemoConfig,
) -> FrontierItem | None:
    """
    From the scored frontier queue:
    1. Filter by status='queued'
    2. Apply budget constraints:
       - max_actions_per_thread (don't run > N in one thread)
       - skip if estimated runtime > max_query_runtime_ms
    3. Check saturation: if top score < config.saturation_threshold, return None
       (signals the outer loop to stop — further exploration is low-value)
    4. Return highest-scoring eligible item, or None if frontier is empty

    Note: thread_id assignment is a v0 gap. In v0 most frontier items will have
    thread_id=None — the max_actions_per_thread budget only applies to items
    that have a thread_id (e.g. those spawned by contradiction clusters or
    thread card updates in Sprint 5). Full thread assignment is a v0.1 concern.
    """

def is_saturated(store: NemoStore, config: NemoConfig) -> bool:
    """
    Check if exploration has reached saturation:
    - All queued frontier items score below threshold
    - Or frontier is empty after generators run
    Used by the outer loop as a natural completion signal.
    """
```

### Sprint 3 Deliverables [✅ Completed]
- [x] `FrontierItem` Pydantic model with all fields and serialization
- [x] 11 generators implemented, each emitting typed `FrontierItem`s with dedupe keys
- [x] Generators cover: schema, trends, changepoints, segments, top groups, outliers, correlations, data quality, coverage, robustness, contradiction resolution
- [x] Custom generator loader discovers and validates `.nemo/generators/*.py` files
- [x] Dedupe removes duplicates against both queued and completed actions
- [x] Scoring function produces repeatable scores with configurable weights
- [x] Scoring incorporates cross-run learnings when available
- [x] Scheduler selects the top eligible action respecting budget constraints
- [x] Saturation detection recognizes when further exploration is low-value
- [x] Full pipeline: `run_generators → dedupe → score → select_next` works on TPC-H data
- [x] `tests/test_planner.py` — generators produce items, dedup reduces count, scoring is deterministic, scheduler respects budgets, saturation detects low-value states

---

## Sprint 4: Executor + Insight Writer + Evidence Graph + Outer Loop [✅ Completed]

The heart of Nemo. Compile frontier actions into SQL, execute against DuckDB, summarize results into insight nodes via LLM, link insights into the evidence graph, and wire it all into the `nemo run` outer loop.

### 4.1 Action → SQL Compiler [✅ Completed]

**File:** `nemo/executor/compile.py`

Each action type maps to a SQL template. The compiler fills in table names, columns, and parameters from the action payload.

```python
def compile_action(item: FrontierItem, profiles: list[TableProfile], join_candidates: list[JoinCandidate]) -> str:
    """
    Convert a FrontierItem into executable SQL.
    Dispatches to action-type-specific compilers:
    """

def compile_schema_profile(payload: dict) -> str: ...
def compile_metric_trend(payload: dict, profiles: list[TableProfile]) -> str: ...
def compile_changepoint(payload: dict, profiles: list[TableProfile]) -> str: ...
def compile_segment_compare(payload: dict) -> str: ...
def compile_top_groups(payload: dict) -> str: ...
def compile_outlier_groups(payload: dict) -> str: ...
def compile_correlation_scan(payload: dict) -> str: ...
def compile_data_quality(payload: dict) -> str: ...
def compile_coverage_explorer(payload: dict, profiles: list[TableProfile]) -> str: ...
def compile_robustness_check(payload: dict, prior_sql: str) -> str: ...
def compile_contradiction_resolve(payload: dict) -> str: ...
```

SQL rules:
- SELECT-only (safe mode enforced)
- Always include LIMIT (default from config)
- Include comments with action_id for traceability
- Use joins only when `join_confidence >= threshold`

### 4.2 Query Executor [✅ Completed]

**File:** `nemo/executor/run.py`

Execute compiled SQL against DuckDB with safety guardrails and result capture.

```python
@dataclass
class ExecutionResult:
    sql: str
    rows: list[dict]
    row_count: int
    column_names: list[str]
    truncated: bool
    cost_ms: int
    error: str | None = None

def execute_query(store: NemoStore, sql: str, config: NemoConfig) -> ExecutionResult:
    """
    Run a query against the user's data in DuckDB.
    - Validate: must be a single SELECT statement
    - Set timeout via PRAGMA
    - Capture wall-clock time
    - Return structured result with timing metadata
    """
```

### 4.3 LLM Summarizer [✅ Completed]

**File:** `nemo/summarize/summarize.py`

Send the execution result to an LLM to produce a human-readable insight.

```python
@dataclass
class InsightDraft:
    title: str
    question: str
    claim: str
    confidence: float
    effect_size: float | None
    tags: list[str]
    hypothesis_struct: dict       # canonical JSON
    claim_struct: dict            # canonical JSON
    result_summary: dict          # key stats from result
    result_sample: list[dict]     # first N rows

async def summarize_result(
    action: FrontierItem,
    result: ExecutionResult,
    profiles: list[TableProfile],
    recent_insights: list[dict],
    config: NemoConfig,
) -> InsightDraft:
    """
    Call LLM with:
    - The question/hypothesis from the action
    - The SQL that was run
    - The result data (truncated if large)
    - Schema context
    - Recent insight summaries (for contradiction/support awareness)

    Returns a structured InsightDraft ready to persist.
    """
```

### 4.4 Canonicalization [✅ Completed]

**File:** `nemo/summarize/canonicalize.py`

Produce structured JSON representations of hypotheses and claims for machine comparison.

```python
@dataclass
class CanonicalClaim:
    metric: str                    # e.g. "revenue", "count", "avg_discount"
    direction: str                 # "higher" | "lower" | "no_change" | "different"
    population: str                # e.g. "all orders", "orders in EUROPE"
    segment: str | None            # e.g. "by region"
    time_range: str | None         # e.g. "1995-01 to 1995-06"
    magnitude: float | None        # effect size
    comparison_base: str | None    # what it's compared to

def canonicalize_claim(claim_text: str, config: NemoConfig) -> dict:
    """LLM call to extract structured claim fields from natural language."""

def canonicalize_hypothesis(question: str, config: NemoConfig) -> dict:
    """LLM call to extract structured hypothesis fields."""
```

### 4.5 Evidence Graph Linker [✅ Completed]

**File:** `nemo/graph/link.py`

After creating an insight, determine how it relates to existing insights and create edges.

```python
def link_insight(
    store: NemoStore,
    new_insight: dict,
    config: NemoConfig,
) -> list[dict]:
    """
    Compare the new insight's canonical claim against recent insights.

    Heuristic rules (v0):
    1. Same metric + same population + narrower filter → REFINES
    2. Same metric + same population + opposite direction → CONTRADICTS
    3. Same metric + related population + same direction → SUPPORTS
    4. Shared source tables + related question → DEPENDS_ON
    5. Very similar dedupe key or claim struct → DUPLICATE_OF / SIMILAR_TO

    Returns list of edge dicts to persist.
    """
```

**File:** `nemo/graph/contradictions.py`

Detect clusters of contradicting insights for prioritized resolution.

```python
def find_contradiction_clusters(store: NemoStore) -> list[dict]:
    """
    Walk the edges table for type='contradicts'.
    Group connected contradicting insights into clusters.
    Return sorted by cluster size (largest unresolved first).
    Each cluster: { insights: [...], open_questions: [...] }
    """
```

### 4.6 Working Memory Loader [✅ Completed]

**File:** `nemo/engine.py` (module-level helper used by `NemoEngine`)

Load the context needed for each outer-loop iteration (MVP doc "Step 0"). Includes error patterns from the current run (self-correction) and cross-run learnings.

```python
def load_working_memory(store: NemoStore, config: NemoConfig, run_id: str) -> dict:
    """
    Build the context dict for one iteration:
    - Schema summary + metric definitions (from config + profiles)
    - Top N recent insights (configurable, default 20)
    - Active thread card (if working a thread)
    - Graph neighborhood around the most recent insight
    - Recent error patterns from this run (self-correction feedback)
    - Cross-run learnings (if config.use_learnings is True)
    """
```

The **error pattern tracking** is inspired by the Ralph Wiggum autonomous loop pattern: failures are data. When a query fails or produces an error, the error context is included in working memory so the LLM and generators can avoid repeating the same mistake. For example, if a `SEGMENT_COMPARE` on `comment` fails because it's free-text with too many unique values, that pattern is recorded and subsequent generators will deprioritize free-text columns.

### 4.7 Event Bus + Event Types [✅ Completed]

**File:** `nemo/events.py`

The engine communicates all state changes through a typed event bus. Every meaningful transition emits an event with a structured payload. Subscribers consume events — the Rich display, user-defined hooks, and (in the future) a WebSocket/SSE bridge to a frontend are all just subscribers.

This is the key extensibility point: to wire Nemo to a frontend, you add a subscriber that serializes events to SSE/WebSocket. To persist to Postgres, you add a subscriber that writes events to a table. The engine doesn't know or care who's listening.

```python
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, Callable


class EventType(str, Enum):
    # ── Run lifecycle ──────────────────────────────────────────
    RUN_STARTED       = "run:started"        # run begins
    RUN_COMPLETED     = "run:completed"       # run finished normally
    RUN_INTERRUPTED   = "run:interrupted"     # user hit Ctrl+C or SIGTERM
    RUN_ERROR         = "run:error"           # fatal error, run aborted

    # ── Frontier lifecycle ─────────────────────────────────────
    FRONTIER_REFRESHED = "frontier:refreshed" # generators + dedupe + score done
    FRONTIER_SATURATED = "frontier:saturated" # all scores below threshold

    # ── Step lifecycle (one per frontier action) ───────────────
    STEP_STARTED      = "step:started"        # action selected, step begins
    STEP_PHASE        = "step:phase"          # sub-phase within a step
    STEP_COMPLETED    = "step:completed"      # step finished (insight created)
    STEP_ERROR        = "step:error"          # step failed (query error, LLM error, etc.)
    STEP_SKIPPED      = "step:skipped"        # skipped by hook or budget

    # ── Artifact events (the things a frontend graph would render) ─
    INSIGHT_CREATED   = "insight:created"     # new insight node persisted
    EDGE_CREATED      = "edge:created"        # new edge persisted
    CONTRADICTION_DETECTED = "contradiction:detected"  # cluster found
    THREAD_UPDATED    = "thread:updated"      # thread card created/updated

    # ── Internal state ─────────────────────────────────────────
    MEMORY_LOADED     = "memory:loaded"       # working memory refreshed
    LEARNING_RECORDED = "learning:recorded"   # cross-run learning persisted


@dataclass
class NemoEvent:
    type: EventType
    run_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    step_num: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict. Every event is fully self-describing."""
        return {
            "type": self.type.value,
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "step_num": self.step_num,
            **self.payload,
        }


class EventSubscriber(Protocol):
    """Any object that can receive events. Subscribers are sync or async callables."""
    async def on_event(self, event: NemoEvent) -> None: ...


class EventBus:
    """
    Central event dispatcher. The engine emits here; subscribers consume.

    Subscribers can filter by event type:
        bus.subscribe(handler)                          # all events
        bus.subscribe(handler, types=[EventType.INSIGHT_CREATED])  # filtered
    """

    def __init__(self) -> None:
        self._subscribers: list[tuple[EventSubscriber | Callable, set[EventType] | None]] = []

    def subscribe(
        self,
        handler: EventSubscriber | Callable,
        types: list[EventType] | None = None,
    ) -> Callable:
        """Register a subscriber. Returns an unsubscribe function."""
        type_set = set(types) if types else None
        entry = (handler, type_set)
        self._subscribers.append(entry)
        return lambda: self._subscribers.remove(entry)

    async def emit(self, event: NemoEvent) -> None:
        """Dispatch an event to all matching subscribers."""
        for handler, type_filter in self._subscribers:
            if type_filter and event.type not in type_filter:
                continue
            if isinstance(handler, EventSubscriber):
                await handler.on_event(event)
            else:
                await handler(event)
```

**Event payload reference** — what each event carries:

| Event | Key Payload Fields |
|-------|-------------------|
| `run:started` | `config`, `datasets`, `frontier_size` |
| `run:completed` | `stats: { steps, insights_created, errors, duration_ms, frontier_remaining }` |
| `run:interrupted` | `reason`, `stats` (same as completed) |
| `run:error` | `error`, `traceback` |
| `frontier:refreshed` | `generated`, `after_dedupe`, `after_score`, `top_score` |
| `frontier:saturated` | `top_score`, `threshold` |
| `step:started` | `action: { action_id, action_type, table, payload, score }` |
| `step:phase` | `phase: "compiling" \| "executing" \| "summarizing" \| "linking"`, `detail` |
| `step:completed` | `insight_id`, `claim`, `confidence`, `duration_ms`, `edges_created` |
| `step:error` | `action_id`, `phase`, `error`, `will_retry` |
| `step:skipped` | `action_id`, `reason` (e.g. "hook blocked", "budget exceeded") |
| `insight:created` | **Full insight dict** — all fields from the insights table |
| `edge:created` | **Full edge dict** — from_insight_id, to_insight_id, type, weight, rationale |
| `contradiction:detected` | `cluster: { insight_ids, claims, open_questions }` |
| `thread:updated` | **Full thread_card dict** |
| `memory:loaded` | `tables`, `recent_insights_count`, `learnings_count`, `error_patterns` |
| `learning:recorded` | `learning: { category, subject, detail, confidence }` |

The `insight:created` and `edge:created` events carry **complete representations** — everything needed to render a node or edge in a frontend graph without additional API calls. This is intentional: a future SSE/WebSocket subscriber can forward these directly to a browser client that builds the graph incrementally.

### 4.8 User Hook Subscriber [✅ Completed]

**File:** `nemo/hooks.py`

One implementation of `EventSubscriber` — runs user-defined shell commands or Python scripts when specific events fire. Configured in `nemo.toml`.

```python
class UserHookSubscriber:
    """
    Executes user-defined commands on matching events.
    Inspired by Claude Code's PreToolUse/PostToolUse hooks.
    """

    def __init__(self, config: NemoConfig): ...

    async def on_event(self, event: NemoEvent) -> None:
        """
        Match event type to configured hooks and execute.
        Each hook receives the full event JSON on stdin.
        """

    async def _run_hook(self, command: str, event: NemoEvent) -> HookResult:
        """
        Execute a hook command as a subprocess.
        Passes event.to_dict() as JSON on stdin.

        Exit codes (matching Claude Code convention):
        - 0: success (stdout is optional feedback)
        - 1: warning (logged, execution continues)
        - 2: block/skip (only meaningful for pre-step events;
              causes the action to be marked 'skipped')

        For step:started events, exit code 2 blocks execution
        of that step — this is how a pre-execute hook works.
        """
```

Config in `nemo.toml`:

```toml
[hooks]
# Map event types (or event prefixes) to commands.
# Use "*" to subscribe to all events.
# Use "step:*" to subscribe to all step events, etc.

"step:started" = ["python .nemo/hooks/validate_sql.py"]
"insight:created" = [
    "python .nemo/hooks/notify_slack.py",
    "python .nemo/hooks/append_to_csv.py",
]
"run:completed" = ["python .nemo/hooks/send_brief_email.py"]

# Future: push all events to a frontend
# "*" = ["python .nemo/hooks/sse_bridge.py"]
```

Example hook script (`.nemo/hooks/notify_slack.py`):

```python
#!/usr/bin/env python3
"""Post high-confidence insights to Slack."""
import json, sys, os, httpx

event = json.load(sys.stdin)
if event.get("confidence", 0) < 0.8:
    sys.exit(0)  # ignore low-confidence

httpx.post(os.environ["SLACK_WEBHOOK"], json={
    "text": f"*Nemo found something:* {event['claim']} (confidence: {event['confidence']:.0%})"
})
```

Example hook script (`.nemo/hooks/sse_bridge.py`) — the future frontend pattern:

```python
#!/usr/bin/env python3
"""Forward all Nemo events to an SSE endpoint for a live frontend."""
import json, sys, httpx

event = json.load(sys.stdin)
# POST to a local server that fans out via SSE/WebSocket
httpx.post("http://localhost:3000/api/nemo/events", json=event)
```

### 4.9 Rich Display Subscriber [✅ Completed]

**File:** `nemo/display.py`

Another `EventSubscriber` — renders events as Rich terminal output. The display never calls the engine directly; it only reacts to events. This means the same engine can run headless (no display subscriber) when driven by a future web UI.

```python
class DisplaySubscriber:
    """Renders NemoEvents as Rich terminal output."""

    def __init__(self, verbose: bool = False, quiet: bool = False): ...

    async def on_event(self, event: NemoEvent) -> None:
        """Route events to the appropriate display method."""
        match event.type:
            case EventType.RUN_STARTED:     self._show_run_header(event)
            case EventType.STEP_STARTED:    self._show_phase(event)
            case EventType.STEP_PHASE:      self._update_spinner(event)
            case EventType.STEP_COMPLETED:  self._show_step_result(event)
            case EventType.STEP_ERROR:      self._show_step_error(event)
            case EventType.STEP_SKIPPED:    self._show_step_skipped(event)
            case EventType.INSIGHT_CREATED: self._show_insight(event)  # verbose only
            case EventType.EDGE_CREATED:    self._show_edge(event)     # verbose only
            case EventType.FRONTIER_REFRESHED: self._update_status_bar(event)
            case EventType.FRONTIER_SATURATED: self._show_saturation_warning(event)
            case EventType.CONTRADICTION_DETECTED: self._show_contradiction(event)
            case EventType.RUN_COMPLETED:   self._show_run_summary(event)
            case EventType.RUN_INTERRUPTED: self._show_interrupted(event)
            case EventType.RUN_ERROR:       self._show_fatal_error(event)

    def _show_step_result(self, event: NemoEvent):
        """
        Format varies by verbosity:
        - quiet:   nothing (accumulate for final summary)
        - normal:  [3/50] SEGMENT_COMPARE  "EUROPE has 23% higher revenue"  ●●●○○ (0.72)
        - verbose: above + full SQL + result table + edge linkage
        """

    def _show_run_summary(self, event: NemoEvent):
        """Final Rich table with run statistics."""
```

### 4.10 The Outer Loop — `nemo run` [✅ Completed]

**File:** `nemo/cli.py` (update) + orchestration in `nemo/engine.py`

Wire everything together into the main loop. The engine's only output interface is `bus.emit(event)` — it never prints, never writes to external systems, never knows who's listening. All side effects happen in subscribers.

**File:** `nemo/engine.py`

```python
class NemoEngine:
    def __init__(self, store: NemoStore, config: NemoConfig, bus: EventBus): ...

    async def run(
        self,
        max_steps: int | None = None,
        max_minutes: float | None = None,
        plan_only: bool = False,
    ) -> str:
        """
        The outer loop (every numbered step emits events):

        1.  Create/resume run record
            → emit(RUN_STARTED, { config, datasets, frontier_size })

        2.  Load working memory
            → emit(MEMORY_LOADED, { tables, recent_insights_count, ... })

        3.  Refresh frontier (generators → dedupe → score)
            → emit(FRONTIER_REFRESHED, { generated, after_dedupe, top_score })

        4.  If plan_only: return (display subscriber shows the plan)

        5.  Select next action (check saturation)
            → emit(FRONTIER_SATURATED) if all scores < threshold → break

        6.  → emit(STEP_STARTED, { action, step_num })
            Subscribers can block here (hook exit code 2 → skip)

        7.  Compile action → SQL
            → emit(STEP_PHASE, { phase: "compiling" })

        8.  Execute query
            → emit(STEP_PHASE, { phase: "executing", sql })

        9.  Summarize result → InsightDraft
            → emit(STEP_PHASE, { phase: "summarizing" })

        10. Persist insight node
            → emit(INSIGHT_CREATED, { full insight dict })

        11. Link to evidence graph
            → emit(STEP_PHASE, { phase: "linking" })
            → emit(EDGE_CREATED, { full edge }) for each edge
            → emit(CONTRADICTION_DETECTED, { cluster }) if found

        12. Record learnings
            → emit(LEARNING_RECORDED, { learning }) if any

        13. → emit(STEP_COMPLETED, { insight_id, claim, confidence, duration_ms })

        14. On error at any sub-step:
            → emit(STEP_ERROR, { action_id, phase, error, will_retry })

        15. Every config.reflect_every steps: refresh frontier (re-run generators
            → dedupe → score), detect contradictions, update thread cards
            → emit(FRONTIER_REFRESHED), emit(THREAD_UPDATED) as needed

        16. Repeat until: budget exhausted OR frontier empty OR saturated

        17. Finalize run record
            → emit(RUN_COMPLETED, { stats }) or emit(RUN_INTERRUPTED, { reason })

        Stop conditions (triple safety — inspired by Ralph):
        - Hard: step count >= max_steps
        - Hard: elapsed time >= max_minutes
        - Soft: saturation (all frontier scores < threshold)
        - External: SIGINT/SIGTERM (graceful shutdown → RUN_INTERRUPTED)

        Returns run_id.
        """
```

**CLI wiring** — the CLI assembles subscribers and plugs them into the bus:

```python
@app.command()
def run(
    steps: int = typer.Option(None, "--steps", "-s", help="Max exploration steps"),
    minutes: float = typer.Option(None, "--minutes", "-m", help="Max runtime in minutes"),
    safe: bool = typer.Option(True, "--safe/--unsafe", help="SQL read-only mode"),
    plan: bool = typer.Option(False, "--plan", help="Dry run: show what would be explored"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show full SQL and results"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Summary output only"),
):
    """Run Nemo exploration loop."""
    store = NemoStore(...)
    config = NemoConfig.load(...)
    bus = EventBus()

    # Subscriber 1: Rich terminal display
    bus.subscribe(DisplaySubscriber(verbose=verbose, quiet=quiet))

    # Subscriber 2: User-defined hooks from nemo.toml
    bus.subscribe(UserHookSubscriber(config))

    # Future subscriber examples (not built in v0):
    # bus.subscribe(SSEBridgeSubscriber("http://localhost:3000/api/nemo/events"))
    # bus.subscribe(PostgresEventLogSubscriber(pg_conn))
    # bus.subscribe(SupabaseRealtimeSubscriber(supabase_client))

    engine = NemoEngine(store, config, bus)
    asyncio.run(engine.run(max_steps=steps, max_minutes=minutes, plan_only=plan))
```

This architecture means **zero engine changes** are needed to add a frontend. You write a new subscriber class, register it on the bus, and every event the engine emits flows to your frontend in real time.

**`nemo run --plan`** (inspired by Claude Code's plan mode):
Generates the frontier, scores all items, and displays a ranked table of what Nemo *would* explore — without executing anything. Useful for previewing before a long overnight run.

```
$ nemo run --plan

  Nemo Plan — 47 actions scored (showing top 15)

  # │ Score │ Type             │ Table    │ Target                          │ Rationale
  ──┼───────┼──────────────────┼──────────┼─────────────────────────────────┼──────────────
  1 │  0.87 │ SEGMENT_COMPARE  │ lineitem │ revenue by r_name (via join)    │ High-variance metric × key dim
  2 │  0.82 │ METRIC_TREND     │ orders   │ order count by o_orderdate      │ Time column detected, no trends yet
  3 │  0.79 │ TOP_GROUPS       │ customer │ top nations by customer count   │ High-cardinality dimension
  ...

  Run `nemo run --minutes 20` to execute.
```

### 4.11 Run Persistence + Resume [✅ Completed]

The `runs` table tracks every run. Frontier items persist their status so a stopped run can be resumed without repeating work. Inspired by Claude Code's `/resume` which lets you pick up any prior session.

**`nemo resume`:**

```python
@app.command()
def resume(
    run_id: str = typer.Argument(None, help="Run ID to resume (omit to pick from list)"),
    steps: int = typer.Option(None, "--steps", "-s"),
    minutes: float = typer.Option(None, "--minutes", "-m"),
):
    """Resume an interrupted or completed run."""
```

When called without a run ID, shows a Rich table of recent runs to pick from:

```
$ nemo resume

  Recent Runs

  # │ Run ID   │ Status      │ Started          │ Steps │ Insights │ Frontier
  ──┼──────────┼─────────────┼──────────────────┼───────┼──────────┼─────────
  1 │ a3f7...  │ interrupted │ 2026-02-25 02:30 │ 23/50 │ 15       │ 28 queued
  2 │ 91cb...  │ completed   │ 2026-02-24 22:00 │ 50/50 │ 31       │ 12 queued
  3 │ d4e2...  │ error       │ 2026-02-24 18:15 │  7/50 │  4       │ 41 queued

  Select a run to resume [1]:
```

**`nemo status`:**

```python
@app.command()
def status():
    """Show current project status: datasets, runs, insights, frontier."""
```

Quick dashboard of the project state — datasets loaded, latest run info, total insights, frontier size, contradiction count.

### Sprint 4 Deliverables [✅ Completed]
- [x] `EventBus` with typed `NemoEvent`s and subscriber protocol
- [x] All 17 event types defined with documented payload schemas
- [x] `DisplaySubscriber` renders events as Rich terminal output (normal/verbose/quiet)
- [x] `UserHookSubscriber` routes events to shell commands per `nemo.toml` config
- [x] Hook exit code 2 on `step:started` blocks that step (pre-execute guard)
- [x] `insight:created` and `edge:created` events carry full entity representations
- [x] Engine emits events at every phase — never prints or writes to stdout directly
- [x] SQL compiler produces correct SELECT queries for all 11 action types
- [x] Executor runs SQL safely (SELECT-only enforcement, timeout, timing capture)
- [x] LLM summarizer converts execution results into structured `InsightDraft`s
- [x] Canonicalization extracts structured claim/hypothesis JSON
- [x] Graph linker creates supports/contradicts/refines edges with rationale
- [x] Contradiction cluster detection groups connected contradictions
- [x] Working memory loader builds iteration context (including error patterns + learnings)
- [x] `nemo run --steps 10` executes 10 steps of the outer loop end-to-end
- [x] `nemo run --minutes 5` respects time budget
- [x] `nemo run --plan` shows scored frontier without executing
- [x] `nemo run --verbose` shows full SQL + results; `--quiet` suppresses step output
- [x] Saturation detection stops the loop when frontier scores drop below threshold
- [x] Self-correction: query errors feed back into working memory
- [x] `nemo resume` lists recent runs and continues from persisted frontier
- [x] `nemo status` shows project dashboard
- [x] SIGINT/SIGTERM gracefully finishes current step, emits `run:interrupted`, persists state
- [x] `tests/test_events.py` — event bus dispatches, subscriber filtering, payload validation
- [x] `tests/test_executor.py` — SQL compilation and execution for each action type
- [x] `tests/test_summarize.py` — LLM summarizer produces valid InsightDrafts (mock LLM)
- [x] `tests/test_graph.py` — linker creates correct edge types, contradiction clusters detected

---

## Sprint 5: Reporting + Demo + Polish

Generate the morning brief, expose graph inspection commands, run the TPC-H end-to-end demo, add thread card stubs, and harden with tests.

### 5.1 Morning Brief Generator

**File:** `nemo/report/brief.py`

Produce a markdown report summarizing a run or time window.

```python
def generate_brief(
    store: NemoStore,
    config: NemoConfig,
    since: str | None = None,       # e.g. "12h", "2d", or ISO timestamp
    run_id: str | None = None,      # or scope to a specific run
) -> str:
    """
    Generate a markdown brief containing:

    ## Top Insights
    - Ranked by confidence × novelty
    - Each with: title, claim, confidence badge, SQL (collapsed), insight_id

    ## Contradictions
    - Unresolved contradiction clusters
    - Each with: conflicting claims, what was tried, open questions

    ## Coverage Summary
    - Tables/columns explored vs total
    - Heatmap-style coverage indicator

    ## Recommended Next Questions
    - Top queued frontier items (what Nemo would do next)

    ## Run Stats
    - Duration, steps, insights created, errors, frontier size

    Returns the full markdown string.
    """
```

### 5.2 Report CLI Commands

```python
@app.command()
def brief(
    output: Path = typer.Option(None, "--output", "-o", help="Write brief to file"),
    since: str = typer.Option("24h", "--since", help="Time window (e.g. 12h, 2d)"),
    run_id: str = typer.Option(None, "--run", help="Scope to a specific run"),
):
    """Generate a morning brief of Nemo's discoveries."""

@app.command()
def report(
    output: Path = typer.Option(None, "--output", "-o"),
    since: str = typer.Option("24h", "--since"),
):
    """Alias for brief with default output to reports/ directory."""
```

### 5.3 Graph Inspection Commands

```python
@graph_app.command()
def stats():
    """Show evidence graph statistics."""
    # Node count, edge count by type, insight count by status,
    # contradiction cluster count, avg confidence, coverage summary

@graph_app.command()
def contradictions(
    top: int = typer.Option(10, "--top", "-n", help="Number of clusters to show"),
):
    """Show top unresolved contradiction clusters."""
    # For each cluster: conflicting insights, claims, and recommended actions
```

### 5.4 Cross-Run Learnings

**File:** `nemo/graph/learnings.py`

Inspired by Claude Code's automatic memory system that records and recalls patterns across sessions. After each run, Nemo scans the results and records reusable patterns in the `learnings` table.

```python
def record_learnings(store: NemoStore, run_id: str) -> list[str]:
    """
    Scan the completed run and extract learnings:

    - join_quality: Which join candidates succeeded/failed in practice?
    - noisy_column: Which columns consistently produce low-confidence insights?
    - useful_metric: Which user-defined metrics yielded high-confidence insights?
    - error_pattern: Which action types + table combos consistently error?
    - generator_hit_rate: Which generators produce the most completed (non-error) insights?

    Merges with existing learnings:
    - If a learning already exists, increment times_confirmed and update confidence
    - If contradicted (e.g., a previously "noisy" column now yields good results),
      reduce confidence of the old learning

    Returns list of learning_ids created or updated.
    """

def recall_learnings(store: NemoStore, context: dict) -> list[dict]:
    """
    Retrieve relevant learnings for the current context.
    Filters by tables/columns in the current dataset.
    Used by the scoring function and generators.
    """
```

### 5.5 Thread Cards (v0 Stub)

**File:** `nemo/graph/threads.py`

Scaffold for v0.1 thread cards. In v0, a thread card is created whenever contradictions form a cluster or when a user-defined metric gets multiple related insights.

```python
def update_thread_cards(store: NemoStore, config: NemoConfig) -> list[str]:
    """
    Scan recent insights and contradiction clusters.
    Create or update thread_cards for:
    - Contradiction clusters (auto-thread)
    - User-defined metric groups
    Returns list of updated thread_ids.
    """
```

### 5.6 TPC-H End-to-End Demo

**File:** `examples/tpch_quickstart.md`

Step-by-step quickstart:

```markdown
# TPC-H Quickstart

## Setup
pip install pynemo
nemo init myproject && cd myproject

## Load demo data
nemo add --tpch --scale 1
nemo ls
nemo profile orders

## Run exploration
nemo run --minutes 10

## Review findings
nemo brief --output reports/morning_brief.md
nemo graph stats
nemo graph contradictions --top 5
```

### 5.7 Golden Test

**File:** `tests/test_golden.py`

Run Nemo on TPC-H scale=0.01 and assert baseline expectations.

```python
def test_tpch_golden():
    """
    End-to-end golden test:
    1. nemo init (temp dir)
    2. nemo add --tpch --scale 0.01
    3. nemo run --steps 15
    4. Assert: >= 10 insights created
    5. Assert: at least 1 edge of each type (supports, contradicts, refines)
    6. Assert: brief generates valid markdown with sections
    7. Assert: no write queries executed (safe mode)
    8. Assert: every insight has a non-empty SQL and claim
    9. Assert: every insight can be re-executed (reproducibility)
    """
```

### 5.8 Reproducibility Verification

```python
def test_insight_reproducibility():
    """
    For each insight in the store:
    - Re-run its stored SQL against the same data
    - Verify result matches stored result_summary (within tolerance)
    """
```

### 5.9 Error Handling + Polish

- **LLM failures:** Retry with exponential backoff (3 attempts). On persistent failure, mark frontier item as `error` with `last_error` and move on. Record the error pattern in working memory (self-correction).
- **Query timeouts:** Respect `max_query_runtime_ms`. On timeout, mark as `error`, log, continue. Record which table+action combo timed out as a learning.
- **Malformed SQL:** If the compiler or LLM produces invalid SQL, catch DuckDB exceptions, record error on the frontier item, and skip. Feed the error back so subsequent SQL generation avoids the same pattern.
- **Empty results:** If a query returns 0 rows, still record an insight ("No data found for this hypothesis") with low confidence.
- **Graceful shutdown:** Handle SIGINT/SIGTERM — finish current step, persist state, record learnings, print summary. (Moved to Sprint 4 as part of the outer loop.)
- **Hook failures:** If a hook script fails, log the error but don't block the loop. Pre-execute hooks can block individual actions (exit code 2) but not the entire run.

### Sprint 5 Deliverables
- [ ] `nemo brief` generates a markdown report with top insights, contradictions, coverage, and recommendations
- [ ] `nemo report` writes brief to `reports/` directory
- [ ] `nemo graph stats` shows node/edge counts, coverage, and avg confidence
- [ ] `nemo graph contradictions --top N` shows top unresolved contradiction clusters
- [ ] Cross-run learnings recorded after each run (join quality, noisy columns, generator hit rates)
- [ ] Learnings recalled and incorporated in subsequent runs
- [ ] Thread cards created for contradiction clusters (stub-level)
- [ ] TPC-H quickstart example documented
- [ ] Golden test: `nemo init → add --tpch → run --steps 15 → brief` passes with ≥10 insights
- [ ] Reproducibility test: every insight's SQL re-executes successfully
- [ ] Error handling: LLM retries, query timeouts, malformed SQL, empty results, hook failures
- [ ] `README.md` with installation, quickstart, configuration reference, and custom generator docs

---

## Data Model Summary

```
┌───────────────────────────────────────────────────────────────┐
│ datasets                                                       │
├───────────────────────────────────────────────────────────────┤
│ dataset_id    VARCHAR PRIMARY KEY                              │
│ name          VARCHAR NOT NULL                                 │
│ source_uri    VARCHAR NOT NULL                                 │
│ format        VARCHAR (csv/parquet/view/tpch)                  │
│ created_at    TIMESTAMP                                        │
│ notes         VARCHAR                                          │
│ schema_json   VARCHAR (JSON)                                   │
└───────────────────────────────────────────────────────────────┘
         │ referenced by source_tables_json
         ▼
┌───────────────────────────────────────────────────────────────┐
│ insights                                                       │
├───────────────────────────────────────────────────────────────┤
│ insight_id             VARCHAR PRIMARY KEY                     │
│ created_at             TIMESTAMP                               │
│ thread_id              VARCHAR (nullable)                      │
│ title                  VARCHAR                                 │
│ question               VARCHAR                                 │
│ hypothesis_struct_json VARCHAR (JSON)                           │
│ sql                    VARCHAR                                 │
│ result_summary_json    VARCHAR (JSON)                           │
│ result_sample_json     VARCHAR (JSON)                           │
│ claim                  VARCHAR                                 │
│ claim_struct_json      VARCHAR (JSON)                           │
│ confidence             DOUBLE                                  │
│ effect_size            DOUBLE (nullable)                       │
│ coverage               DOUBLE (nullable)                       │
│ cost_ms                INTEGER                                 │
│ source_tables_json     VARCHAR (JSON)                           │
│ tags_json              VARCHAR (JSON)                           │
│ status                 VARCHAR (ok/error)                       │
│ error_text             VARCHAR (nullable)                       │
└───────────────────────────────────────────────────────────────┘
         │
         │ N:N (via edges)
         ▼
┌───────────────────────────────────────────────────────────────┐
│ edges                                                          │
├───────────────────────────────────────────────────────────────┤
│ edge_id          VARCHAR PRIMARY KEY                           │
│ from_insight_id  VARCHAR → insights                            │
│ to_insight_id    VARCHAR → insights                            │
│ type             VARCHAR (supports/contradicts/refines/...)    │
│ weight           DOUBLE                                        │
│ rationale        VARCHAR                                       │
│ created_at       TIMESTAMP                                     │
└───────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────┐
│ frontier                                                       │
├───────────────────────────────────────────────────────────────┤
│ action_id            VARCHAR PRIMARY KEY                       │
│ action_type          VARCHAR                                   │
│ payload_json         VARCHAR (JSON)                             │
│ score                DOUBLE                                    │
│ status               VARCHAR (queued/running/done/skipped/err) │
│ dedupe_key           VARCHAR                                   │
│ thread_id            VARCHAR (nullable)                        │
│ depends_on_action_id VARCHAR (nullable)                        │
│ last_error           VARCHAR (nullable)                        │
│ created_at           TIMESTAMP                                 │
└───────────────────────────────────────────────────────────────┘

┌─────────────────────────────┐  ┌─────────────────────────────┐
│ runs                         │  │ thread_cards                 │
├─────────────────────────────┤  ├─────────────────────────────┤
│ run_id          VARCHAR PK   │  │ thread_id       VARCHAR PK   │
│ started_at      TIMESTAMP    │  │ updated_at      TIMESTAMP    │
│ ended_at        TIMESTAMP    │  │ title           VARCHAR      │
│ status          VARCHAR      │  │ summary_text    VARCHAR      │
│ config_json     VARCHAR      │  │ key_insight_ids VARCHAR(JSON)│
│ steps_completed INTEGER      │  │ open_questions  VARCHAR(JSON)│
│ insights_created INTEGER     │  │ contradictions  VARCHAR(JSON)│
│ errors          INTEGER      │  └─────────────────────────────┘
│ frontier_size   INTEGER      │
│ notes           VARCHAR      │  ┌─────────────────────────────┐
└─────────────────────────────┘  │ learnings (cross-run memory) │
                                  ├─────────────────────────────┤
                                  │ learning_id     VARCHAR PK   │
                                  │ created_at      TIMESTAMP    │
                                  │ run_id          VARCHAR      │
                                  │ category        VARCHAR      │
                                  │ subject         VARCHAR      │
                                  │ detail          VARCHAR      │
                                  │ confidence      DOUBLE       │
                                  │ times_confirmed INTEGER      │
                                  └─────────────────────────────┘
```

---

## Testing Strategy

### Unit Tests (per module)
| Test File | Covers | Key Assertions |
|-----------|--------|----------------|
| `test_store.py` | Store init, CRUD, all 7 tables | Tables created, insert/query round-trips, learnings CRUD |
| `test_ingest.py` | File loading, TPC-H, profiling | Tables exist, profiles have correct columns |
| `test_joins.py` | Join discovery | Expected candidates found for TPC-H |
| `test_planner.py` | Generators, custom loader, dedupe, scoring, scheduler, saturation | Items emitted, customs loaded, dupes removed, scores stable, budget respected, saturation detected |
| `test_executor.py` | SQL compilation, query execution | Valid SQL for each action type, results structured correctly |
| `test_summarize.py` | LLM summarizer (mocked) | InsightDraft fields populated, confidence in range |
| `test_graph.py` | Linker, contradictions | Correct edge types, clusters detected |
| `test_events.py` | EventBus, dispatching, filtering | Events emitted, subscribers receive correct types, payloads valid |
| `test_hooks.py` | UserHookSubscriber, shell execution | Hooks run on matching events, exit codes respected (0=proceed, 2=skip) |
| `test_report.py` | Brief generation | Valid markdown, required sections present |
| `test_learnings.py` | Cross-run memory | Learnings recorded, recalled, merged across runs |

### Golden Test
| Test | Scope | Assertions |
|------|-------|------------|
| `test_golden.py` | Full E2E on TPC-H 0.01 | ≥10 insights, edges exist, brief valid, safe mode, reproducible, learnings recorded |

### Reproducibility
Every insight stores its SQL + referenced tables. The reproducibility test re-runs each query and confirms the result matches within tolerance.

---

## Dependencies

| Package | Purpose | Sprint |
|---------|---------|--------|
| `typer[all]` | CLI framework with Rich integration | 1 |
| `duckdb` | Storage + analytics engine | 1 |
| `rich` | Terminal formatting, tables, progress bars, Live display | 1 |
| `tomllib` (stdlib) | TOML config reading (built into Python 3.11+) | 1 |
| `tomli-w` | TOML config writing (for `nemo init`) | 1 |
| `pydantic` | Data models and validation | 1 |
| `openai` | LLM API client | 4 |
| `pytest` | Testing framework | 1 |
| `pytest-asyncio` | Async test support | 4 |

---

## Environment Variables

```env
# LLM
OPENAI_API_KEY=sk-...
NEMO_MODEL=gpt-4.1-mini

# Budgets (override nemo.toml)
NEMO_MAX_STEPS=100
NEMO_MAX_RUNTIME_MINUTES=30
NEMO_MAX_QUERY_RUNTIME_MS=15000
```

---

## File Checklist

| File | Sprint | Purpose |
|------|--------|---------|
| `pyproject.toml` | 1 | Package config + dependencies |
| `README.md` | 5 | Installation + quickstart + custom generator docs |
| `nemo/__init__.py` | 1 | Package root |
| `nemo/cli.py` | 1 | Typer CLI entrypoint (init, doctor, add, run, resume, status, plan, brief, graph) |
| `nemo/config.py` | 1 | NemoConfig dataclass + TOML parsing (hooks, verbosity, saturation) |
| `nemo/events.py` | 4 | EventBus, NemoEvent, EventType enum, subscriber protocol |
| `nemo/hooks.py` | 4 | UserHookSubscriber — routes events to shell commands per config |
| `nemo/display.py` | 4 | DisplaySubscriber — Rich live terminal output driven by events |
| `nemo/engine.py` | 4 | Outer loop orchestrator + working memory loader (emits events, never prints directly) |
| `nemo/store/__init__.py` | 1 | Store package |
| `nemo/store/db.py` | 1 | NemoStore — DuckDB connection + CRUD |
| `nemo/store/schema.sql` | 1 | System table DDL (7 tables including learnings) |
| `nemo/ingest/__init__.py` | 2 | Ingest package |
| `nemo/ingest/add.py` | 2 | Dataset loading (file, glob, TPC-H) |
| `nemo/ingest/profile.py` | 2 | Schema + column profiling |
| `nemo/ingest/joins.py` | 2 | Join key discovery heuristics |
| `nemo/planner/__init__.py` | 3 | Planner package |
| `nemo/planner/generators.py` | 3 | 10 built-in frontier generators |
| `nemo/planner/loader.py` | 3 | Custom generator discovery (.nemo/generators/) |
| `nemo/planner/scoring.py` | 3 | Utility scoring function (with learnings integration) |
| `nemo/planner/dedupe.py` | 3 | Deduplication logic |
| `nemo/planner/scheduler.py` | 3 | Next-action selection with budgets + saturation detection |
| `nemo/executor/__init__.py` | 4 | Executor package |
| `nemo/executor/compile.py` | 4 | Action → SQL compiler |
| `nemo/executor/run.py` | 4 | DuckDB query execution + timing |
| `nemo/summarize/__init__.py` | 4 | Summarizer package |
| `nemo/summarize/summarize.py` | 4 | LLM result → InsightDraft |
| `nemo/summarize/canonicalize.py` | 4 | Structured claim/hypothesis extraction |
| `nemo/graph/__init__.py` | 4 | Graph package |
| `nemo/graph/link.py` | 4 | Evidence graph edge creation |
| `nemo/graph/contradictions.py` | 4 | Contradiction cluster detection |
| `nemo/graph/threads.py` | 5 | Thread cards (v0 stub) |
| `nemo/graph/learnings.py` | 5 | Cross-run memory (record + recall) |
| `nemo/report/__init__.py` | 5 | Report package |
| `nemo/report/brief.py` | 5 | Morning brief markdown generator |
| `nemo/report/render.py` | 5 | Optional HTML renderer (stub) |
| `.nemo/generators/` | 3 | User-defined generator Python files (auto-discovered) |
| `.nemo/hooks/` | 4 | User-defined hook scripts |
| `examples/tpch_quickstart.md` | 5 | Demo walkthrough |
| `examples/custom_generator.py` | 5 | Example custom generator |
| `tests/conftest.py` | 1 | Shared test fixtures |
| `tests/test_store.py` | 1 | Store init + CRUD tests (including learnings table) |
| `tests/test_ingest.py` | 2 | Ingestion + profiling tests |
| `tests/test_joins.py` | 2 | Join discovery tests |
| `tests/test_planner.py` | 3 | Generator + scoring + scheduler + saturation tests |
| `tests/test_executor.py` | 4 | SQL compilation + execution tests |
| `tests/test_summarize.py` | 4 | Summarizer tests (mocked LLM) |
| `tests/test_graph.py` | 4 | Linker + contradiction tests |
| `tests/test_events.py` | 4 | EventBus dispatch, subscriber filtering, payload validation |
| `tests/test_hooks.py` | 4 | UserHookSubscriber execution, exit codes, event routing |
| `tests/test_report.py` | 5 | Brief generation tests |
| `tests/test_learnings.py` | 5 | Cross-run memory tests |
| `tests/test_golden.py` | 5 | E2E TPC-H golden test |

---

## Definition of Done

The MVP is complete when:

1. A user can run:
   ```
   nemo init
   nemo doctor
   nemo add --tpch --scale 1
   nemo run --plan              # preview what would be explored
   nemo run --minutes 20
   nemo status
   nemo brief
   nemo graph stats
   ```

2. The brief contains:
   - At least 10 insights
   - Each with an `insight_id`, a query, and a readable claim
   - Some refinements and at least one contradiction link

3. The system can be stopped and resumed:
   - `Ctrl+C` gracefully persists state
   - `nemo resume` picks up from where it left off
   - Frontier persists — it doesn't "forget" what it already tried

4. Every insight is reproducible:
   - Stored SQL re-executes against the same data
   - Result matches within tolerance

5. The system is observable:
   - Rich live display shows progress during `nemo run`
   - `nemo run --verbose` shows full SQL and results
   - `nemo run --plan` previews the frontier without executing

6. The system is extensible:
   - Custom generators in `.nemo/generators/` are auto-discovered
   - Hooks in `nemo.toml` are executed at pre_execute, post_insight, and post_run

7. The system gets smarter over time:
   - Cross-run learnings persist in the `learnings` table
   - Subsequent runs incorporate learnings into scoring
