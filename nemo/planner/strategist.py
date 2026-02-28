"""LLM-driven exploration strategist.

Replaces deterministic generators with a reasoning loop:
plan → execute → interpret → update notebook → plan next.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, Field

from nemo.config import NemoConfig
from nemo.executor.run import ExecutionResult
from nemo.ingest.profile import TableProfile


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Hypothesis(BaseModel):
    """A single investigation step proposed by the strategist."""

    question: str
    reasoning: str
    sql: str
    table: str = ""
    analysis_type: Literal["sql", "statistical"] = "sql"


class NotebookEntry(BaseModel):
    """One investigation thread in the analyst's notebook."""

    theme: str
    summary: str
    key_findings: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    tables_touched: list[str] = Field(default_factory=list)
    step_count: int = 0


class Notebook(BaseModel):
    """The analyst's running notebook of findings."""

    entries: list[NotebookEntry] = Field(default_factory=list)
    total_steps: int = 0


class InterpretationResult(BaseModel):
    """Structured output from the interpret + notebook update step."""

    title: str
    claim: str
    confidence: float
    effect_size: float | None = None
    tags: list[str] = Field(default_factory=list)
    reasoning: str

    theme: str
    summary_update: str
    new_finding: str
    new_open_questions: list[str] = Field(default_factory=list)
    resolved_questions: list[str] = Field(default_factory=list)
    proposed_hypothesis: str | None = None
    hypothesis_confidence: float | None = None


# ---------------------------------------------------------------------------
# Schema context builder
# ---------------------------------------------------------------------------

def build_schema_context(
    profiles: list[TableProfile],
    focus_tables: set[str] | None = None,
    max_samples: int = 5,
) -> str:
    """Compact schema representation for LLM prompts."""
    focus_norm = {table.strip().lower() for table in (focus_tables or set()) if table and table.strip()}
    sample_limit = max(0, int(max_samples))
    parts: list[str] = []
    for profile in sorted(profiles, key=lambda p: p.name):
        if focus_tables is None or profile.name.lower() in focus_norm:
            parts.append(_render_full_schema_table(profile, max_samples=sample_limit))
            continue
        parts.append(_render_compact_schema_table(profile))
    return "\n".join(parts)


def _render_full_schema_table(profile: TableProfile, *, max_samples: int) -> str:
    cols: list[str] = []
    for column in profile.columns:
        extras: list[str] = []
        if column.distinct_count:
            extras.append(f"{column.distinct_count} distinct")
        if column.null_pct > 0.01:
            extras.append(f"{column.null_pct:.0%} null")
        if column.mean is not None:
            extras.append(f"mean={column.mean:.1f}")
        if column.min_val is not None and column.max_val is not None:
            extras.append(f"range={column.min_val}..{column.max_val}")
        if column.sample_values and max_samples > 0:
            samples = ", ".join(repr(v) for v in column.sample_values[:max_samples])
            extras.append(f"e.g. {samples}")
        extra_str = f" ({', '.join(extras)})" if extras else ""
        cols.append(f"    {column.name} {column.dtype}{extra_str}")
    return f"  {profile.name} ({profile.row_count:,} rows):\n" + "\n".join(cols)


def _render_compact_schema_table(profile: TableProfile) -> str:
    column_names = ", ".join(column.name for column in profile.columns) or "(no columns)"
    return f"  {profile.name} ({profile.row_count:,} rows, {len(profile.columns)} columns): {column_names}"


def format_notebook(
    notebook: Notebook,
    detail: Literal["full", "summary", "headlines"] = "full",
    max_findings_per_theme: int = 5,
    max_questions_per_theme: int = 3,
    max_themes: int = 10,
) -> str:
    """Format notebook entries for inclusion in LLM prompts."""
    if not notebook.entries:
        return "(empty — this is the start of your investigation)"
    max_findings = max(0, int(max_findings_per_theme))
    max_questions = max(0, int(max_questions_per_theme))
    max_theme_count = max(1, int(max_themes))

    sorted_entries = sorted(
        notebook.entries,
        key=lambda entry: (int(entry.step_count), len(entry.key_findings)),
        reverse=True,
    )
    displayed_entries = sorted_entries[:max_theme_count]
    collapsed_entries = sorted_entries[max_theme_count:]

    parts: list[str] = []
    for entry in displayed_entries:
        tables = ", ".join(entry.tables_touched) if entry.tables_touched else "(none)"
        findings = entry.key_findings
        questions = entry.open_questions
        if detail == "full":
            findings = findings[-max_findings:] if max_findings else []
            questions = questions[-max_questions:] if max_questions else []
            findings_text = "\n".join(f"    - {f}" for f in findings) if findings else "    (none yet)"
            questions_text = "\n".join(f"    - {q}" for q in questions) if questions else "    (none)"
            parts.append(
                f"  [{entry.theme}] ({entry.step_count} steps, tables: {tables})\n"
                f"    Summary: {entry.summary}\n"
                f"    Key findings:\n{findings_text}\n"
                f"    Open questions:\n{questions_text}"
            )
        elif detail == "summary":
            summary_findings = findings[-2:]
            summary_questions = questions[-1:]
            findings_text = "\n".join(f"    - {f}" for f in summary_findings) if summary_findings else "    (none yet)"
            questions_text = "\n".join(f"    - {q}" for q in summary_questions) if summary_questions else "    (none)"
            parts.append(
                f"  [{entry.theme}] ({entry.step_count} steps, tables: {tables})\n"
                f"    Summary: {entry.summary}\n"
                f"    Recent findings:\n{findings_text}\n"
                f"    Top open question:\n{questions_text}"
            )
        else:
            latest_finding = findings[-1] if findings else "(no findings yet)"
            parts.append(
                f"  [{entry.theme}] ({entry.step_count} steps, {len(entry.key_findings)} findings) "
                f"- latest: {latest_finding}"
            )

    if collapsed_entries:
        collapsed_text = ", ".join(f"{entry.theme} ({entry.step_count} steps)" for entry in collapsed_entries)
        parts.append(f"  Earlier themes: {collapsed_text}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

STRATEGIST_SYSTEM = """\
You are a senior data analyst performing exploratory data analysis on a DuckDB database. \
You work methodically: form hypotheses, test them with SQL, and build on what you learn.

You write DuckDB-compatible SQL. Use double-quoted identifiers for column/table names. \
Prefer concise aggregations (GROUP BY, window functions, CTEs) over SELECT *. \
Always add LIMIT (max 200 rows) to prevent huge result sets.

You have two execution modes:
- sql: Standard DuckDB query. Use for aggregations, counts, distributions, GROUP BY comparisons, ranking, filtering.
- statistical: Python-based inferential analysis. Use only when the question requires formal statistical methods
  (for example p-values, regression coefficients with significance, confidence intervals from bootstrapping,
  or formal hypothesis tests such as t-tests/ANOVA/chi-squared/correlation tests).

When using statistical mode, your `sql` must be a narrow extraction query that fetches only the columns
required by the statistical method. The SQL is data extraction input for the analyst, not final business output.

Rule of thumb: if SQL GROUP BY can answer it directly, use `sql`. If you need inferential statistics, use `statistical`.

Avoid exploration loops:
- Do not ask nearly the same question as a recent step.
- If one theme has already gone deep, pivot to a different table/theme.
- Treat sharp drops at the edge of a date range as potential data-boundary artifacts, not immediate business events."""

STRATEGIST_USER = """\
## Available Tables
{schema}

## Your Investigation Notebook
{notebook}

## Coverage Guidance
{coverage}

## Frontier Suggestions (deterministic)
{frontier_hints}
{goal_section}
## Task
Decide what to investigate next. Think like a real analyst:
- If the notebook is empty, start by understanding the most interesting table — look for \
the dimension with the most business-relevant variation.
- If you have prior findings, identify the most promising thread to pull on. What's the \
most important open question? What would a domain expert want to understand deeper?
- Connect your reasoning to specific prior findings when they exist.
- Favor breadth early: touch under-explored tables before continuing a deep thread.
- Write a single DuckDB SQL query to answer your question.

IMPORTANT: Look carefully at the sample values in the schema above. Use them to write \
correct SQL — column values may be strings that look numeric (e.g. '$123,456.00'), need \
casting, or have specific format patterns. Always double-check your WHERE/GROUP BY values \
against the samples.

Return a JSON object (no markdown fences):
{{"question": "The specific question to answer", "reasoning": "Why this is the best next step (reference prior findings if any)", "sql": "DuckDB SQL query", "table": "Primary table being queried", "analysis_type": "sql|statistical"}}"""

STRATEGIST_RETRY_USER = """\
Your previous query failed with this error:
{error}

The failed SQL was:
{failed_sql}

Fix the SQL and try again. Common issues: use double-quoted identifiers, verify column \
names match the schema above, use DuckDB syntax (not MySQL/Postgres-specific).

Return the same JSON format with corrected SQL."""

INTERPRETER_SYSTEM = """\
You are a senior data analyst interpreting query results from an automated exploration system. \
Your job is to extract meaningful, actionable insights and maintain a running investigation notebook."""

INTERPRETER_USER = """\
## Available Tables
{schema}

## Your Investigation Notebook
{notebook}

## Current Investigation Step
Question: {question}
Reasoning: {reasoning}
SQL: {sql}

## Query Results
Columns: {columns}
Row count: {row_count}
{rows_preview}

## Task
1. **Interpret**: What does this result tell us? Does it answer the question? What's surprising \
or actionable? Be specific with numbers.
2. **Sanity check**: If the numbers look nonsensical (e.g. negative salaries, impossibly high \
values, all NULLs), flag a likely data-type or casting issue — check the sample values in the \
schema above for the correct format. Note any data quality issues in your reasoning.
3. **Assess quality**: If the result is trivial (stats on IDs, just restating row counts, or \
tautological), set confidence ≤ 0.3. High confidence (0.7+) requires a non-obvious pattern \
with clear business meaning.
4. **Update notebook**: Either extend an existing theme or create a new one. Add the key finding \
and any new questions this raises. Mark any questions that were answered.
5. **Propose hypothesis**: If this finding suggests a specific, testable hypothesis worth validating \
later, include it. Otherwise leave it null.

Return a JSON object (no markdown fences):
{{"title": "Short finding title (≤10 words)", "claim": "1-2 sentence finding with specific numbers", "confidence": 0.0-1.0, "effect_size": null, "tags": ["tag1", "tag2"], "reasoning": "How this finding connects to prior discoveries and what it means for the investigation (2-3 sentences)", "theme": "Theme name to update or create", "summary_update": "Updated 2-3 sentence summary for this theme", "new_finding": "One-line key finding to add to the theme", "new_open_questions": ["New question raised by this finding"], "resolved_questions": ["Question from notebook that this answered"], "proposed_hypothesis": null, "hypothesis_confidence": null}}"""

INTERPRETER_EMPTY_USER = """\
## Available Tables
{schema}

## Your Investigation Notebook
{notebook}

## Current Investigation Step
Question: {question}
Reasoning: {reasoning}
SQL: {sql}

## Query Results
The query returned 0 rows.

## Task
Interpret this empty result. Does the absence of data tell us something? Check the schema \
sample values above — the empty result may indicate a casting or filter-value mismatch. \
Update the notebook accordingly. Set confidence ≤ 0.3 for empty results.

Return the same JSON format as usual."""


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def _format_goal_section(goal: str) -> str:
    if not goal.strip():
        return ""
    return f"\n## Investigation Goal\n{goal}\nKeep this goal in mind — prioritize lines of inquiry that serve it.\n"


async def plan_next_step(
    notebook: Notebook,
    schema_context: str | None,
    config: NemoConfig,
    client: OpenAI,
    error_context: dict[str, str] | None = None,
    coverage_context: str | None = None,
    frontier_hints: str | None = None,
    planning_feedback: str | None = None,
    profiles: list[TableProfile] | None = None,
) -> Hypothesis:
    """Ask the LLM to propose the next investigation step."""
    scoped_schema_context = schema_context or ""
    if profiles is not None:
        focus_tables = _infer_focus_tables_for_planning(
            notebook=notebook,
            frontier_hints=frontier_hints,
            profiles=profiles,
        )
        scoped_schema_context = build_schema_context(
            profiles,
            focus_tables=focus_tables,
            max_samples=5,
        )

    goal_section = _format_goal_section(config.goal)
    if error_context:
        user_content = STRATEGIST_RETRY_USER.format(
            error=error_context["error"],
            failed_sql=error_context["sql"],
        )
        messages = [
            {"role": "user", "content": STRATEGIST_USER.format(
                schema=scoped_schema_context,
                notebook=format_notebook(notebook, detail="full"),
                coverage=coverage_context or "(not provided)",
                frontier_hints=frontier_hints or "(none)",
                goal_section=goal_section,
            )},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [
            {"role": "user", "content": STRATEGIST_USER.format(
                schema=scoped_schema_context,
                notebook=format_notebook(notebook, detail="full"),
                coverage=coverage_context or "(not provided)",
                frontier_hints=frontier_hints or "(none)",
                goal_section=goal_section,
            )},
        ]
    if planning_feedback:
        messages.append({"role": "user", "content": planning_feedback})

    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                client.responses.parse,
                model=config.plan_model or config.model or "gpt-5-mini",
                instructions=STRATEGIST_SYSTEM,
                input=messages,
                text_format=Hypothesis,
            )
            if response.output_parsed is not None:
                return response.output_parsed
            raw = _extract_text(response)
            return _parse_json_as(raw, Hypothesis)
        except (APIConnectionError, APITimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
        except RateLimitError:
            if attempt == 2:
                raise
            await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError("plan_next_step: exhausted retries")


async def interpret_and_update(
    hypothesis: Hypothesis,
    result: ExecutionResult,
    notebook: Notebook,
    schema_context: str | None,
    config: NemoConfig,
    client: OpenAI,
    profiles: list[TableProfile] | None = None,
) -> InterpretationResult:
    """Ask the LLM to interpret a query result and update the notebook."""
    scoped_schema_context = schema_context or ""
    if profiles is not None:
        focus_tables = {hypothesis.table.strip()} if hypothesis.table.strip() else None
        scoped_schema_context = build_schema_context(
            profiles,
            focus_tables=focus_tables,
            max_samples=5,
        )

    if result.row_count == 0:
        user_content = INTERPRETER_EMPTY_USER.format(
            schema=scoped_schema_context,
            notebook=format_notebook(notebook, detail="full"),
            question=hypothesis.question,
            reasoning=hypothesis.reasoning,
            sql=hypothesis.sql,
        )
    else:
        max_display_rows = max(1, int(getattr(config, "max_display_rows", 15)))
        if hypothesis.analysis_type == "statistical":
            max_display_rows = min(max_display_rows, 5)
        rows_preview = _format_rows(
            result.rows,
            result.column_names,
            max_rows=max_display_rows,
            max_columns=10,
        )
        user_content = INTERPRETER_USER.format(
            schema=scoped_schema_context,
            notebook=format_notebook(notebook, detail="full"),
            question=hypothesis.question,
            reasoning=hypothesis.reasoning,
            sql=hypothesis.sql,
            columns=", ".join(result.column_names),
            row_count=result.row_count,
            rows_preview=rows_preview,
        )

    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                client.responses.parse,
                model=config.model or "gpt-5-mini",
                instructions=INTERPRETER_SYSTEM,
                input=[{"role": "user", "content": user_content}],
                text_format=InterpretationResult,
            )
            if response.output_parsed is not None:
                parsed = response.output_parsed
                parsed.confidence = max(0.0, min(1.0, parsed.confidence))
                if parsed.hypothesis_confidence is not None:
                    parsed.hypothesis_confidence = max(0.0, min(1.0, parsed.hypothesis_confidence))
                return parsed
            raw = _extract_text(response)
            parsed = _parse_json_as(raw, InterpretationResult)
            parsed.confidence = max(0.0, min(1.0, parsed.confidence))
            if parsed.hypothesis_confidence is not None:
                parsed.hypothesis_confidence = max(0.0, min(1.0, parsed.hypothesis_confidence))
            return parsed
        except (APIConnectionError, APITimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
        except RateLimitError:
            if attempt == 2:
                raise
            await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError("interpret_and_update: exhausted retries")


def apply_notebook_update(notebook: Notebook, interpretation: InterpretationResult) -> Notebook:
    """Apply an InterpretationResult's notebook updates to produce a new Notebook."""
    entries = [e.model_copy() for e in notebook.entries]
    target_theme = interpretation.theme.strip()
    existing_idx: int | None = None
    for i, entry in enumerate(entries):
        if entry.theme.lower() == target_theme.lower():
            existing_idx = i
            break

    if existing_idx is not None:
        entry = entries[existing_idx]
        entry.summary = interpretation.summary_update
        if interpretation.new_finding and interpretation.new_finding not in entry.key_findings:
            entry.key_findings.append(interpretation.new_finding)
        for q in interpretation.new_open_questions:
            if q not in entry.open_questions:
                entry.open_questions.append(q)
        entry.open_questions = _fuzzy_resolve_questions(
            entry.open_questions, interpretation.resolved_questions
        )
        entry.step_count += 1
        tables = set(entry.tables_touched)
        if interpretation.tags:
            for tag in interpretation.tags:
                if "." not in tag and tag.isidentifier():
                    tables.add(tag)
        entry.tables_touched = sorted(tables)
    else:
        tables_touched: list[str] = []
        for tag in (interpretation.tags or []):
            if "." not in tag and tag.isidentifier():
                tables_touched.append(tag)
        entries.append(NotebookEntry(
            theme=target_theme,
            summary=interpretation.summary_update,
            key_findings=[interpretation.new_finding] if interpretation.new_finding else [],
            open_questions=list(interpretation.new_open_questions),
            tables_touched=sorted(set(tables_touched)),
            step_count=1,
        ))

    return Notebook(entries=entries, total_steps=notebook.total_steps + 1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fuzzy_resolve_questions(
    open_questions: list[str], resolved: list[str], threshold: float = 0.55
) -> list[str]:
    """Remove open questions that fuzzy-match any resolved question."""
    if not resolved:
        return open_questions

    import re

    def _tokens(text: str) -> set[str]:
        stopwords = {
            "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "by",
            "with", "is", "are", "was", "were", "be", "this", "that", "how", "what",
            "which", "do", "does", "did", "from",
        }
        return {tok for tok in re.findall(r"[a-z0-9_]+", text.lower()) if tok not in stopwords}

    def _similarity(a: str, b: str) -> float:
        ta, tb = _tokens(a), _tokens(b)
        if not ta or not tb:
            return 0.0
        intersection = len(ta & tb)
        return max(
            intersection / len(ta | tb),
            intersection / min(len(ta), len(tb)),
        )

    resolved_texts = [r.strip() for r in resolved if r.strip()]
    remaining: list[str] = []
    for q in open_questions:
        if any(_similarity(q, r) >= threshold for r in resolved_texts):
            continue
        remaining.append(q)
    return remaining


def _format_rows(
    rows: list[dict[str, Any]],
    columns: list[str],
    *,
    max_rows: int = 20,
    max_columns: int = 10,
) -> str:
    """Format result rows as a compact markdown table for LLM context."""
    if not rows:
        return "(no rows)"
    row_limit = max(1, int(max_rows))
    col_limit = max(1, int(max_columns))
    visible_columns = columns[:col_limit]
    hidden_columns = columns[col_limit:]

    header = "| " + " | ".join(visible_columns) + " |"
    separator = "| " + " | ".join(["---"] * len(visible_columns)) + " |"
    lines: list[str] = [header, separator]
    for row in rows[:row_limit]:
        values = [_fmt_table_cell(_fmt_val(row.get(col))) for col in visible_columns]
        lines.append("| " + " | ".join(values) + " |")

    notes: list[str] = []
    if hidden_columns:
        notes.append(
            f"(+ {len(hidden_columns)} more columns: {', '.join(hidden_columns)})"
        )
    if len(rows) > row_limit:
        notes.append(f"... ({len(rows)} rows total)")
    if notes:
        return "\n".join(lines + [""] + notes)
    return "\n".join(lines)


def _infer_focus_tables_for_planning(
    *,
    notebook: Notebook,
    frontier_hints: str | None,
    profiles: list[TableProfile],
) -> set[str] | None:
    focus_tables: set[str] = set()
    if notebook.entries:
        current_entry = max(notebook.entries, key=lambda entry: int(entry.step_count))
        focus_tables.update(table for table in current_entry.tables_touched if table)

    hints_text = (frontier_hints or "").lower()
    if hints_text.strip():
        for profile in profiles:
            pattern = rf"\b{re.escape(profile.name.lower())}\b"
            if re.search(pattern, hints_text):
                focus_tables.add(profile.name)

    if not focus_tables:
        return None
    return focus_tables


def _fmt_val(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _fmt_table_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ")


def _is_temporal_dtype(dtype: str) -> bool:
    kind = dtype.lower()
    return "date" in kind or "time" in kind


def _extract_text(response: Any) -> str:
    """Pull raw text content from an OpenAI response."""
    for item in response.output:
        if getattr(item, "type", None) == "message":
            for block in item.content:
                if hasattr(block, "text"):
                    return block.text
    return ""


def _parse_json_as(text: str, model_cls: type[BaseModel]) -> Any:
    """Best-effort JSON extraction and Pydantic parse."""
    import re

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return model_cls.model_validate_json(cleaned)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            return model_cls.model_validate_json(match.group())
        raise
