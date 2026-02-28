# Context Efficiency: Leaner LLM Prompt Representations

**Date:** 2026-02-27  
**Status:** Draft  

## Problem

Every LLM call in Nemo assembles context from several sources — schema, notebook, hypotheses, query results, frontier hints, evidence chains — and injects them into the prompt. The current formatting is functional but has several inefficiencies that compound during long runs:

1. **Notebook grows without bound.** `format_notebook()` dumps every theme, every finding, every open question into every prompt. A 50-step run with 8 themes can produce a notebook section that dwarfs the actual task instructions. Nothing is ever pruned or condensed.

2. **Full schema in every prompt.** `build_schema_context()` includes all tables with all columns, all stats, and up to 8 sample values per column — even when the current step only touches one table. For a 15-table database, this can be 2-3K tokens of unchanging reference material repeated on every call.

3. **Redundant context in the arbiter.** `_build_arbiter_context()` includes both a `_build_knowledge_summary()` *and* the full `format_notebook()`. The knowledge summary already extracts the last 3 findings and last 2 questions per theme — the full notebook duplicates most of this. The arbiter doesn't write SQL, so it doesn't need the raw detail.

4. **Hypothesis backlog has no filtering.** `_format_hypotheses()` lists every hypothesis regardless of status. Resolved hypotheses (validated/invalidated) are historical context at best, but they occupy the same visual weight as active ones. A long run could accumulate 15+ hypotheses, all serialized in full.

5. **Row format is verbose.** `_format_rows()` uses `col=val` pairs for up to 20 rows. When a table has 15 columns, each row becomes a long line. Column names are repeated on every row, which is redundant when the model has already seen the column list.

6. **No token awareness.** There are no guardrails that estimate or cap the total context size before calling the LLM. If multiple sections bloat simultaneously, the prompt silently degrades (model attention diluted, cost inflated, latency increased).

## Principles

- **Don't hide information the model needs.** The goal is compression, not omission. The strategist still needs schema detail to write correct SQL; the interpreter still needs raw rows to form claims.
- **Different consumers need different fidelity.** The strategist needs full schema for the tables it's querying. The arbiter just needs a strategic summary. The interpreter needs raw data. Tailor the representation to the consumer.
- **Prefer structural limits over LLM-based summarization.** Summarization calls add latency and cost. Most gains come from simple truncation, filtering, and format changes.
- **Make it measurable.** Add lightweight token estimation so we can log context sizes and set alerts.

## Current State

### What each LLM consumer sees

| Consumer | Schema | Notebook | Hypotheses | Data Rows | Other |
|---|---|---|---|---|---|
| Strategist (plan) | Full | Full | — | — | Coverage, frontier hints (top 8) |
| Interpreter | Full | Full | — | 20 rows (`col=val`) | Question, SQL, reasoning |
| Arbiter | — | Full + knowledge summary | All (unfiltered) | — | Coverage, budget, recent phases |
| Validator | Full | Full | Target + evidence (last 8) | — | — |
| Reranker | — | Full | — | — | Candidate list |
| Edge linker | — | — | — | — | Insight pairs (minimal dicts) |
| Debrief | — | Full | All | — | Stats, contradictions |

### Formatting functions (current)

- `build_schema_context(profiles)` — All tables, all columns, 8 sample vals each
- `format_notebook(notebook)` — All entries, all findings, all questions
- `_format_hypotheses(hypotheses)` — All hypotheses, full claims, all metadata
- `_format_rows(rows, columns)` — Up to 20 rows, `col=val` format
- `_build_knowledge_summary(notebook, hypotheses)` — Last 3 findings/theme, last 2 questions/theme, top 5 untested hypotheses
- `_format_evidence(hypothesis)` — Last 8 evidence links

---

## Sprint 1: Notebook Compression and Consumer-Specific Views [✅ Completed]

**Goal:** Stop the notebook from growing unboundedly in prompts. Give each consumer the level of detail it actually needs.

### 1.1 — Tiered notebook formatting [✅ Completed]

Replace the single `format_notebook()` with a function that accepts a detail level:

```python
def format_notebook(
    notebook: Notebook,
    detail: Literal["full", "summary", "headlines"] = "full",
    max_findings_per_theme: int = 5,
    max_questions_per_theme: int = 3,
) -> str:
```

**`full`** — Current behavior, but with per-theme caps on findings and questions. Oldest findings beyond `max_findings_per_theme` are dropped. This is what the strategist and interpreter see.

**`summary`** — Each theme gets its summary sentence, last 2 findings, last 1 question. Suitable for the arbiter, reranker, and validator.

**`headlines`** — One line per theme: `[Theme Name] (N steps, N findings) — latest: "...most recent finding..."`. Suitable for edge linking or any consumer that just needs thematic awareness.

### 1.2 — Remove notebook duplication in the arbiter [✅ Completed]

The arbiter currently receives both `_build_knowledge_summary()` and the full `format_notebook()`. Change `_build_arbiter_context()` to use `format_notebook(notebook, detail="summary")` and drop the separate knowledge summary call. The summary-level notebook already captures findings and questions at comparable density.

### 1.3 — Cap notebook entries themselves [✅ Completed]

Add a `max_themes` parameter to `format_notebook()`. When the notebook exceeds this count (default 10), only show the N most recently active themes in full, and collapse the rest into a single "Earlier themes" line listing names and step counts.

### Changes

| File | Function | Change |
|---|---|---|
| `strategist.py` | `format_notebook()` | Add `detail`, `max_findings_per_theme`, `max_questions_per_theme`, `max_themes` params |
| `strategist.py` | `plan_next_step()` | Pass `detail="full"` (explicit) |
| `strategist.py` | `interpret_and_update()` | Pass `detail="full"` (explicit) |
| `arbiter.py` | `_build_arbiter_context()` | Use `format_notebook(detail="summary")`, remove `_build_knowledge_summary()` from the body (or fold its hypothesis section in separately) |
| `scoring.py` | `_rerank_top_candidates()` | Use `format_notebook(detail="summary")` |
| `validator.py` | `plan_validation_step()` | Use `format_notebook(detail="summary")` |
| `brief.py` | `generate_run_debrief()` | Keep `detail="full"` (end-of-run, not latency-sensitive) |

---

## Sprint 2: Schema Scoping [✅ Completed]

**Goal:** Reduce schema context size by showing full detail only for tables relevant to the current step, and compact summaries for the rest.

### 2.1 — Scoped schema builder [✅ Completed]

Add a `focus_tables` parameter to `build_schema_context()`:

```python
def build_schema_context(
    profiles: list[TableProfile],
    focus_tables: set[str] | None = None,
    max_samples: int = 5,
) -> str:
```

When `focus_tables` is provided:
- **Focus tables** get full detail (column stats, sample values up to `max_samples`).
- **Other tables** get a compact one-liner: `table_name (N rows, M columns): col1, col2, col3, ...` — just the column names, no stats or samples.

When `focus_tables` is `None`, all tables get full detail (preserving current behavior for the first step when no context exists yet).

### 2.2 — Infer focus tables from context [✅ Completed]

In `plan_next_step()`: derive `focus_tables` from the current notebook entry's `tables_touched` plus any tables mentioned in recent frontier hints. This is a simple heuristic — no LLM call needed.

In `interpret_and_update()`: the table is known from the hypothesis/query, so pass it directly.

In `plan_validation_step()`: use `hypothesis.tables_involved`.

### 2.3 — Reduce sample values [✅ Completed]

Drop the default sample count from 8 to 5. For non-focus tables in compact view, show 0 samples. Sample values are helpful for writing correct SQL (data format awareness), but 5 gives enough signal.

### Changes

| File | Function | Change |
|---|---|---|
| `strategist.py` | `build_schema_context()` | Add `focus_tables` and `max_samples` params, implement two-tier rendering |
| `strategist.py` | `plan_next_step()` | Compute `focus_tables` from notebook + frontier, pass to `build_schema_context()` |
| `strategist.py` | `interpret_and_update()` | Pass queried table as `focus_tables` |
| `validator.py` | `plan_validation_step()` | Pass `hypothesis.tables_involved` as `focus_tables` |

---

## Sprint 3: Hypothesis Filtering and Row Format [✅ Completed]

**Goal:** Reduce hypothesis bloat and make row data more token-efficient.

### 3.1 — Filter hypotheses by relevance [✅ Completed]

Change `_format_hypotheses()` to group and filter:

```python
def _format_hypotheses(
    hypotheses: list[HypothesisRecord],
    include_resolved: bool = False,
    max_active: int = 10,
    max_resolved: int = 3,
) -> str:
```

- **Active** (proposed, testing): Show up to `max_active`, sorted by priority descending. These are what the arbiter actually decides on.
- **Resolved** (validated, invalidated, narrowed, inconclusive): Show only a count line by default (`"5 resolved hypotheses (3 validated, 2 invalidated)"`). When `include_resolved=True`, show the last `max_resolved` with their verdicts.
- **Claim truncation**: Truncate hypothesis claims to 150 chars in the formatted output. The full claim is in the database if needed.

### 3.2 — Tabular row format [✅ Completed]

Replace the `col=val` row format with a compact table (pipe-delimited markdown):

```
| col1 | col2 | col3 |
|------|------|------|
| val  | val  | val  |
| val  | val  | val  |
```

This eliminates column name repetition on every row. For a 15-column, 20-row result, this saves ~40% tokens compared to `col=val` format. The column header appears once instead of 20 times.

For wide tables (>10 columns), only include the first 10 columns in the table and append a note: `(+ 5 more columns: col11, col12, ...)`. The model can request a wider view in its next step if needed.

### 3.3 — Adaptive row count [✅ Completed]

Add a `max_display_rows` config option (default: 15) and use it in `_format_rows()`. The current hardcoded 20 is slightly generous — 15 rows of a compact table provides ample signal. For statistical-mode results, drop to 5 rows (the statistical analyst already has access to the full data).

### Changes

| File | Function | Change |
|---|---|---|
| `arbiter.py` | `_format_hypotheses()` | Add filtering, grouping, truncation |
| `arbiter.py` | `_build_arbiter_context()` | Pass `include_resolved=False` |
| `brief.py` | `_hypothesis_summary()` | Pass `include_resolved=True` for debrief |
| `strategist.py` | `_format_rows()` | Switch to pipe-delimited table, add column cap |
| `strategist.py` | `interpret_and_update()` | Use `max_display_rows` from config |
| `config.py` | `NemoConfig` | Add `max_display_rows: int = 15` |

---

## Estimated Token Impact

Rough estimates for a mid-length run (30 steps, 6 themes, 10 hypotheses, 12-column table):

| Section | Before (est. tokens) | After (est. tokens) | Savings |
|---|---|---|---|
| Notebook (per prompt) | ~800-1200 | ~400-600 (summary), ~600-800 (full, capped) | 30-50% |
| Schema (per prompt) | ~600-1500 | ~300-500 (scoped) | 50-70% |
| Hypotheses (arbiter) | ~400-800 | ~200-350 | 50-55% |
| Result rows (interpreter) | ~500-900 | ~300-550 | 35-40% |
| **Total per prompt** | **~2300-4400** | **~1200-2000** | **~45-55%** |

Over a 30-step run with ~60 LLM calls (strategist + interpreter + arbiter + validator), this adds up to a meaningful reduction in both cost and latency.

## Testing

- **Unit tests**: Add tests for each formatting function at all detail levels. Verify output contains expected sections, respects caps, and truncates correctly.
- **Regression test**: Run the existing `test_planner.py` suite to ensure prompt changes don't break parsing of LLM responses (the response format is unchanged — only the input context shrinks).
- **Token counting**: Add a lightweight `estimate_tokens(text: str) -> int` utility (simple `len(text) // 4` heuristic or `tiktoken` if available) and log context sizes per call. Use this in integration tests to assert that prompts stay under a configurable ceiling.
- **Manual review**: Run a full analysis on the Medicare dataset and compare the prompt contents before/after at step 1, step 15, and step 30 to verify no critical information is lost.

## Risks

- **Schema scoping too aggressive.** If the strategist wants to join across tables and only sees compact info for the secondary table, it might write incorrect SQL. Mitigation: the compact view still lists all column names; the model can infer types from names. If the retry rate increases, widen the focus heuristic.
- **Notebook caps lose important early findings.** An early finding might be foundational but fall off the per-theme cap. Mitigation: keep the summary sentence (which should capture the theme's essence) and only cap the findings list. The model can always look at the summary.
- **Tabular row format harder to parse for some models.** Pipe-delimited tables are a well-known format for LLMs, but some models handle `col=val` better for sparse data. Mitigation: benchmark both formats on a few representative results before committing.
