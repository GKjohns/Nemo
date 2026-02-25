# Nemo (Python) — Local-First, Long-Running Insight Agent

## High-level vision

Nemo is a **long-running AI discovery agent** that continuously explores hypotheses in one or more related datasets, runs experiments (mostly SQL), and writes its findings into a durable **Insight Graph**.

Instead of a linear “chat log” that grows until it must be compacted, Nemo operates like an automated research lab:

* It maintains a **frontier** of “next experiments to run”
* It schedules experiments using a **utility score**
* It executes experiments against local/accessible data (SQL-first)
* It records each learning as an **insight node**
* It links insights into an **evidence graph** (supports / contradicts / refines)
* It periodically generates **briefs** (morning report, contradiction clusters, open questions)

**Local-first** is a core product feature:

* Users can run Nemo on their machine with their data
* It can use local compute, local files, and local tools
* It avoids the SaaS “upload your database” problem
* It’s naturally aligned with agentic tooling ecosystems

---

## Core concepts

### Insight Node (unit of knowledge)

An Insight is a compact, inspectable artifact:

* **Question / Hypothesis**
* **Method** (SQL query or plan)
* **Result summary** (stats, sample, counts, effect size, etc.)
* **Answer / Claim** (plain language + structured form)
* **Metadata** (confidence, novelty, cost, coverage, runtime, provenance)

Nodes should be:

* reproducible (store query + dataset refs)
* inspectable (store key result fields + a sample)
* linkable (supports/refines/contradicts)

### Evidence Graph

Edges express relationships between nodes:

* `supports`
* `contradicts`
* `refines` (same claim but narrower cohort/time/segment)
* `depends_on`
* `duplicate_of` / `similar_to`

The graph becomes a “second dataset”: it can be searched, summarized, and mined for higher-level patterns.

### Frontier (candidate actions)

A **Frontier Item** is “what Nemo might do next,” e.g.:

* test a hypothesis
* segment an insight by cohort/platform/region
* run robustness checks / alternate metrics
* resolve a contradiction cluster
* explore uncharted schema areas (coverage)
* sanity checks (data quality signals)

---

## Product goals

### What Nemo should feel like (v0)

* You point Nemo at data
* You run it overnight
* In the morning you get a **brief** of:

  * top new insights (with confidence + reproducible queries)
  * major contradictions and what Nemo tried to resolve them
  * recommended next questions

### What Nemo becomes (v1+)

* Multiple datasets, join-aware exploration
* Threaded investigations with “thread cards” instead of giant context
* Parallel execution with budget constraints
* An interactive UI (optional) that browses the graph and lets users steer

---

## MVP scope (what to build first)

### MVP principles

1. **SQL-first, read-only by default**
2. **Durable storage of everything** (no “context accumulation”)
3. **Reproducibility over cleverness**
4. **Small set of strong exploration generators**
5. **Join support exists, but used conservatively**

### MVP deliverables

* Python package + CLI
* Local database file that stores:

  * datasets (or references)
  * insights
  * edges
  * frontier queue
  * run logs
* “Morning brief” markdown report

---

## Tech stack (recommended)

### Data engine

**DuckDB** as the primary analytic engine:

* fast local OLAP
* supports CSV/Parquet, views, joins, window functions
* can store Nemo’s own tables too
* single file persistence (`nemo.duckdb`)

Optional:

* **Polars** (or Pandas) for non-SQL transforms, profiling, sampling, etc.

### Storage approach

Simplest: store *everything* in DuckDB:

* Nemo system tables: `insights`, `edges`, `frontier`, `runs`, `artifacts`
* User data loaded as tables or external views

---

## Repo / package layout

```
nemo/
  __init__.py
  cli.py                  # Typer CLI entrypoint
  config.py               # NemoConfig, profiles, budgets
  store/
    db.py                 # DuckDB connection + migrations
    schema.sql            # system tables
    migrations/           # optional versioned migrations
  ingest/
    add.py                # add dataset(s), create tables/views
    profile.py            # schema + stats profiling
    joins.py              # join key discovery + join graph suggestions
  planner/
    generators.py         # frontier generators
    scoring.py            # scoring function (utility)
    dedupe.py             # avoid duplicates
    scheduler.py          # pick next action(s)
  executor/
    compile.py            # action -> SQL plan
    run.py                # execute query, capture timings
  summarize/
    summarize.py          # results -> insight node
    canonicalize.py       # structured hypothesis/claim format
  graph/
    link.py               # supports/contradicts/refines edges
    contradictions.py     # detect contradiction clusters
    threads.py            # thread cards (v1 but scaffold now)
  report/
    brief.py              # morning brief markdown generator
    render.py             # optional HTML later
examples/
  tpch_quickstart.md
pyproject.toml
README.md
```

---

## CLI design

### Commands

**Initialize**

* `nemo init`
  Creates `.nemo/` (or `nemo/`), initializes `nemo.duckdb`, writes default config.

**Add datasets**

* `nemo add path/to/file.parquet --name orders`
* `nemo add path/to/*.csv --name raw_events --format csv`
* `nemo add s3://...` (future)
* `nemo add --tpch --scale 1` (demo mode)

**Inspect**

* `nemo ls` (list tables/datasets)
* `nemo schema orders`
* `nemo profile orders`

**Run**

* `nemo run --minutes 30`
* `nemo run --steps 50`
* `nemo run --parallel 4` (future)
* `nemo run --safe` (default; SQL read-only)

**Report**

* `nemo report --since 12h`
* `nemo brief --output reports/brief.md`

**Graph**

* `nemo graph stats` (node/edge counts, clusters)
* `nemo graph contradictions --top 10`

### CLI output expectations (v0)

* Clear stdout logs
* Reports written to disk
* Each insight references a stable `insight_id` and stores its SQL

---

## Data model (DuckDB tables)

### 1) `datasets`

Tracks loaded tables/views and metadata.

Suggested fields:

* `dataset_id` (uuid)
* `name` (string)
* `source_uri` (string) — file path, glob, etc.
* `format` (csv/parquet/view)
* `created_at`
* `notes`
* `schema_json` (optional)

### 2) `insights`

Core unit. Keep it audit-friendly.

* `insight_id` (uuid)
* `created_at`
* `thread_id` (nullable)
* `title` (short)
* `question` (text)
* `hypothesis_struct_json` (json) — canonical form
* `sql` (text)
* `result_summary_json` (json) — counts, aggregates, etc.
* `result_sample_json` (json) — small sample rows
* `claim` (text)
* `claim_struct_json` (json) — canonical claim
* `confidence` (0–1)
* `effect_size` (nullable numeric)
* `coverage` (nullable numeric) — percent rows scanned / represented
* `cost_ms` (runtime)
* `source_tables_json` (json) — tables referenced
* `tags_json` (json)
* `status` (ok/error)
* `error_text` (nullable)

### 3) `edges`

* `edge_id` (uuid)
* `created_at`
* `from_insight_id`
* `to_insight_id`
* `type` (supports/contradicts/refines/depends_on/duplicate_of)
* `weight` (0–1)
* `rationale` (text)

### 4) `frontier`

Queue of proposed actions.

* `action_id` (uuid)
* `created_at`
* `thread_id` (nullable)
* `action_type` (enum-ish string)
* `payload_json` (json) — parameters (metric, segment, etc.)
* `score` (float)
* `status` (queued/running/done/skipped/error)
* `last_error` (nullable)
* `depends_on_action_id` (nullable)
* `dedupe_key` (string) — important for avoiding repeats

### 5) `runs`

* `run_id` (uuid)
* `started_at`
* `ended_at`
* `config_json`
* `steps_completed`
* `insights_created`
* `errors`
* `notes`

### 6) `thread_cards` (optional v0 stub)

A compact summary of an investigation cluster.

* `thread_id`
* `updated_at`
* `title`
* `summary_text`
* `key_insight_ids_json`
* `open_questions_json`
* `contradictions_json`

---

## Execution modes and safety

### Default: `--safe` (read-only SQL)

In v0, Nemo should be “harmless”:

* only runs SELECT queries
* does not write to user tables
* only writes to Nemo’s system tables

Later modes:

* `--python` allows dataframe transforms
* `--tools` allows calling local tools (very gated)

---

## The outer loop: detailed v0 design

### Step 0: Load “working memory”

Instead of chat context, Nemo loads:

* schema summary + metric definitions
* top recent insights
* thread card (if working a thread)
* relevant neighborhood around the current node(s)

### Step 1: Frontier refresh

Run generators to propose new actions:

**Generators (start with ~8–12)**

1. **Schema scout**: list tables/columns, row counts, nulls, cardinalities
2. **Top movers (time-series)**: detect sudden changes in key metrics
3. **Segment deltas**: compare metric across top categorical dimensions
4. **Outliers**: extreme values by group or row-level anomalies
5. **Correlations**: simple correlations among numeric metrics
6. **Funnel-ish checks** (if applicable): step dropoffs (requires config)
7. **Data quality signals**: null spikes, duplicates, missing join keys
8. **Contradiction resolver**: target contradiction clusters and propose tests
9. **Robustness check**: verify a claim with alternate definition or slice
10. **Coverage explorer**: pick under-explored columns/tables

Each generator emits `FrontierItem`s with a `dedupe_key`.

### Step 2: Dedupe

Drop actions that are:

* duplicates of recently run actions
* semantically identical based on `dedupe_key`
* redundant given already strong evidence

### Step 3: Score

Score(action) = weighted sum:

* info gain proxy (uncertainty reduction)
* estimated impact proxy (effect size potential)
* novelty (distance from prior insights)
* feasibility (runtime estimate)
* diversity bonus (don’t get stuck in one thread)

### Step 4: Select

Pick top action subject to budgets:

* max runtime per step
* max scan size (optional)
* max actions per thread (diversification)

### Step 5: Execute

Compile action → SQL

* Run in DuckDB
* Capture timing, row counts
* Get summary + sample

### Step 6: Summarize into an Insight

Produce:

* human-readable claim
* structured claim/hypothesis (canonical JSON)
* confidence (based on rules + model narrative)

### Step 7: Link

Heuristics (v0):

* If claim_struct matches prior claim with narrower filters → `refines`
* If same metric/population but opposite direction → `contradicts`
* If query result seems to provide evidence for an existing open question → `supports`

### Step 8: Report / publish

Every N insights or at end of run:

* write brief markdown
* update thread card(s)

Repeat.

---

## Join support (v0 → v1)

### v0 join behavior

* Nemo discovers likely join keys and records a “join suggestion graph”
* Nemo uses joins only when:

  * explicitly required by an action
  * join confidence is above threshold

### Join discovery heuristics

For each pair of tables:

* candidate key columns: `id`, `*_id`, common names
* uniqueness ratio for key candidate (should be high on dimension side)
* overlap sampling: do values intersect?
* type compatibility and null rate

Nemo emits:

* `join_candidate(table_a.col_x -> table_b.col_y, confidence)`
* can later be accepted/overridden by user config

---

## Demo dataset plan (recommended)

### Primary demo: TPC-H via DuckDB

Reason: instant joins + businessy insights with no external downloads.

Demo script:

* `nemo init`
* `nemo add --tpch --scale 1`
* `nemo run --minutes 10`
* `nemo brief`

Example insights Nemo should find:

* revenue by region (join `customer` + `orders` + `lineitem`)
* supplier performance vs ship date (join supplier/nation/lineitem)
* segments with high discount behavior
* anomalies in late shipments by category

(You can later add “real” datasets, but TPC-H is the fastest path to a reliable wow.)

---

## Configuration (what users can control)

`nemo.toml` (or YAML) should allow:

* dataset aliases / table names
* time column(s)
* key metrics + definitions (SQL expressions)
* “important” dimensions (country, platform, segment, etc.)
* budgets:

  * max runtime per query
  * max steps per run
  * allowed tables
* scoring weights:

  * novelty vs impact vs coverage
* join overrides:

  * accepted joins, rejected joins
* safety modes:

  * safe/sql-only default

---

## Testing strategy (important for trust)

### Unit tests

* dedupe key stability
* scoring determinism (given fixed state)
* SQL compilation correctness for each action type
* join discovery heuristics

### Golden tests

* Run Nemo on TPCH scale=0.1 and assert:

  * creates at least N insights
  * produces a brief
  * no write queries in safe mode

### Reproducibility

* every insight should be re-runnable:

  * store SQL
  * store referenced tables
  * store run config

---

## Roadmap

### v0 (ship)

* DuckDB storage + system tables
* CLI: init/add/run/brief
* 8–12 strong generators
* scoring + scheduler
* insight writer + basic graph linking
* TPCH demo

### v0.1

* thread cards (compact memory)
* contradiction cluster detection + prioritization
* improved canonicalization + duplicate detection

### v1

* parallel actions with budgets
* better join planning (join graph as a first-class object)
* plugin system for custom generators and metric packs
* optional lightweight UI (browse graph + approve actions)

### v2

* tool mode (python execution, notebooks)
* local desktop integrations (gated, explicit)
* remote runners / distributed execution

---

## Definition of “done” for MVP

You can consider v0 complete when:

1. A user can run:

   * `nemo init`
   * `nemo add --tpch --scale 1`
   * `nemo run --minutes 20`
   * `nemo brief`

2. The brief contains:

   * at least 10 insights
   * each with an `insight_id`, a query, and a readable claim
   * some refinements and at least one contradiction link

3. The system can be stopped and resumed:

   * frontier persists
   * it doesn’t “forget” what it already tried

---

## Appendix: Suggested action types (v0)

* `SCHEMA_PROFILE`
* `METRIC_TREND_SCAN`
* `CHANGEPOINT_DETECT` (simple)
* `SEGMENT_COMPARE`
* `TOP_GROUPS`
* `OUTLIER_GROUPS`
* `CORRELATION_SCAN`
* `DATA_QUALITY_CHECK`
* `JOIN_CANDIDATE_DISCOVERY`
* `ROBUSTNESS_CHECK`
* `CONTRADICTION_RESOLVE`