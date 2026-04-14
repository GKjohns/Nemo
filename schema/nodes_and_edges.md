# Nemo Evidence Graph Schema

## Overview

Nemo's evidence graph is stored in DuckDB. Insights are nodes, edges capture relationships between them. The graph is the core analytical artifact — it's what makes the analysis auditable.

## Insights (nodes)

Each insight is a finding from one exploration step.

| Field | Type | Description |
|-------|------|-------------|
| `insight_id` | VARCHAR (PK) | Unique identifier |
| `run_id` | VARCHAR | Which run produced this insight |
| `thread_id` | VARCHAR | Thematic thread grouping |
| `title` | VARCHAR | Short title |
| `question` | VARCHAR | What was asked |
| `sql` | VARCHAR | The SQL query that was executed |
| `result_summary_json` | VARCHAR | Structured summary of query results |
| `result_sample_json` | VARCHAR | Sample rows from query output |
| `claim` | VARCHAR | The model's interpretation as a testable claim |
| `confidence` | DOUBLE | 0.0 to 1.0 |
| `effect_size` | DOUBLE | Magnitude of the finding |
| `coverage` | DOUBLE | What fraction of data this covers |
| `source_tables_json` | VARCHAR | Which tables were queried |
| `tags_json` | VARCHAR | Categorical tags |
| `status` | VARCHAR | `ok`, `error`, `skipped` |
| `error_text` | VARCHAR | Error details if status != ok |
| `reasoning` | VARCHAR | Model's reasoning chain |

## Edges

Edges capture logical relationships between insights.

| Field | Type | Description |
|-------|------|-------------|
| `edge_id` | VARCHAR (PK) | Unique identifier |
| `from_insight_id` | VARCHAR (FK) | Source insight |
| `to_insight_id` | VARCHAR (FK) | Target insight |
| `type` | VARCHAR | Relationship type (see below) |
| `weight` | DOUBLE | Edge strength, 0.0 to 1.0 |
| `rationale` | VARCHAR | Why this edge exists |

### Edge types

| Type | Meaning |
|------|---------|
| `supports` | Target provides evidence FOR source's claim |
| `contradicts` | Target provides evidence AGAINST source's claim |
| `refines` | Target narrows or qualifies source's claim |
| `depends_on` | Source is only valid if target holds |

Contradiction edges are used to identify unresolved tensions. The `graph contradictions` command clusters them for review.

## Hypotheses

Testable claims extracted during exploration.

| Field | Type | Description |
|-------|------|-------------|
| `hypothesis_id` | VARCHAR (PK) | Unique identifier |
| `run_id` | VARCHAR | Which run proposed this |
| `claim` | VARCHAR | The hypothesis statement |
| `source_insight_id` | VARCHAR | Insight that inspired this hypothesis |
| `initial_confidence` | DOUBLE | Starting confidence |
| `status` | VARCHAR | `proposed`, `testing`, `confirmed`, `refuted`, `inconclusive` |
| `priority` | DOUBLE | Exploration priority |
| `evidence_chain` | VARCHAR | JSON list of supporting/conflicting insight IDs |
| `verdict` | VARCHAR | Final determination |
| `verdict_confidence` | DOUBLE | Confidence in the verdict |
| `validation_step` | INTEGER | How many validation steps completed |

## Supporting tables

| Table | Purpose |
|-------|---------|
| `datasets` | Registered data sources (name, path, format, schema) |
| `frontier` | Queued exploration actions with scores |
| `runs` | Run metadata, step counts, debrief text |
| `thread_cards` | Thematic groupings with summaries and open questions |
| `notebooks` | Per-run strategist notebook (JSON) |
| `learnings` | Cross-run patterns (category, subject, confidence) |
