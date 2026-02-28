"""LLM-driven exploration strategist.

Replaces deterministic generators with a reasoning loop:
plan → execute → interpret → update notebook → plan next.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

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

def build_schema_context(profiles: list[TableProfile]) -> str:
    """Compact schema representation for LLM prompts."""
    parts: list[str] = []
    for p in sorted(profiles, key=lambda p: p.name):
        cols: list[str] = []
        for c in p.columns:
            extras: list[str] = []
            if c.distinct_count:
                extras.append(f"{c.distinct_count} distinct")
            if c.null_pct > 0.01:
                extras.append(f"{c.null_pct:.0%} null")
            if c.mean is not None:
                extras.append(f"mean={c.mean:.1f}")
            if c.min_val is not None and c.max_val is not None:
                extras.append(f"range={c.min_val}..{c.max_val}")
            if c.sample_values:
                samples = ", ".join(repr(v) for v in c.sample_values[:8])
                extras.append(f"e.g. {samples}")
            extra_str = f" ({', '.join(extras)})" if extras else ""
            cols.append(f"    {c.name} {c.dtype}{extra_str}")
        parts.append(f"  {p.name} ({p.row_count:,} rows):\n" + "\n".join(cols))
    return "\n".join(parts)


def format_notebook(notebook: Notebook) -> str:
    """Format notebook entries for inclusion in LLM prompts."""
    if not notebook.entries:
        return "(empty — this is the start of your investigation)"
    parts: list[str] = []
    for entry in notebook.entries:
        findings = "\n".join(f"    - {f}" for f in entry.key_findings) if entry.key_findings else "    (none yet)"
        questions = "\n".join(f"    - {q}" for q in entry.open_questions) if entry.open_questions else "    (none)"
        parts.append(
            f"  [{entry.theme}] ({entry.step_count} steps, tables: {', '.join(entry.tables_touched)})\n"
            f"    Summary: {entry.summary}\n"
            f"    Key findings:\n{findings}\n"
            f"    Open questions:\n{questions}"
        )
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
{{"question": "The specific question to answer", "reasoning": "Why this is the best next step (reference prior findings if any)", "sql": "DuckDB SQL query", "table": "Primary table being queried"}}"""

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
    schema_context: str,
    config: NemoConfig,
    client: OpenAI,
    error_context: dict[str, str] | None = None,
    coverage_context: str | None = None,
    frontier_hints: str | None = None,
    planning_feedback: str | None = None,
) -> Hypothesis:
    """Ask the LLM to propose the next investigation step."""
    goal_section = _format_goal_section(config.goal)
    if error_context:
        user_content = STRATEGIST_RETRY_USER.format(
            error=error_context["error"],
            failed_sql=error_context["sql"],
        )
        messages = [
            {"role": "user", "content": STRATEGIST_USER.format(
                schema=schema_context,
                notebook=format_notebook(notebook),
                coverage=coverage_context or "(not provided)",
                frontier_hints=frontier_hints or "(none)",
                goal_section=goal_section,
            )},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [
            {"role": "user", "content": STRATEGIST_USER.format(
                schema=schema_context,
                notebook=format_notebook(notebook),
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
    schema_context: str,
    config: NemoConfig,
    client: OpenAI,
) -> InterpretationResult:
    """Ask the LLM to interpret a query result and update the notebook."""
    if result.row_count == 0:
        user_content = INTERPRETER_EMPTY_USER.format(
            schema=schema_context,
            notebook=format_notebook(notebook),
            question=hypothesis.question,
            reasoning=hypothesis.reasoning,
            sql=hypothesis.sql,
        )
    else:
        rows_preview = _format_rows(result.rows[:20], result.column_names)
        user_content = INTERPRETER_USER.format(
            schema=schema_context,
            notebook=format_notebook(notebook),
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


def _format_rows(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """Format result rows as a compact text table for LLM context."""
    if not rows:
        return "(no rows)"
    lines: list[str] = []
    for row in rows[:20]:
        parts = [f"{col}={_fmt_val(row.get(col))}" for col in columns]
        lines.append("  " + ", ".join(parts))
    if len(rows) > 20:
        lines.append(f"  ... ({len(rows)} rows total)")
    return "\n".join(lines)


def _fmt_val(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


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
