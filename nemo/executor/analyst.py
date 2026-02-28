"""Statistical analyst ReAct agent and tooling."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Awaitable, Callable

import pandas as pd
from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from nemo.config import NemoConfig
from nemo.executor.run import ExecutionResult
from nemo.executor.sandbox import PythonSession
from nemo.ingest.profile import TableProfile
from nemo.store import NemoStore

ANALYST_TOOLS = [
    {
        "type": "function",
        "name": "describe_table",
        "description": "Return column names, types, distinct counts, and sample values for a table.",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table name"},
            },
            "required": ["table"],
        },
    },
    {
        "type": "function",
        "name": "extract_dataframe",
        "description": (
            "Execute a SQL SELECT query and load results into a pandas DataFrame. "
            "The DataFrame is stored as `df` in the Python session (or a custom variable name). "
            "Returns shape, dtypes, and head(5) preview. Keep queries narrow: "
            "select only columns needed for analysis, use WHERE/LIMIT to control size. "
            "Max rows: configurable (default 50,000)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "DuckDB-compatible SELECT query"},
                "variable_name": {
                    "type": "string",
                    "description": "Variable name for the DataFrame (default: 'df')",
                    "default": "df",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "type": "function",
        "name": "run_python",
        "description": (
            "Execute Python code in a sandboxed session with pandas, numpy, scipy.stats, "
            "and statsmodels available. Previously extracted DataFrames persist between calls. "
            "Return values: assign results to `_result` (dict) for structured output. "
            "Print statements are captured as output text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute. Use `_result = {...}` for structured output.",
                },
            },
            "required": ["code"],
        },
    },
]

ANALYST_INSTRUCTIONS = """\
You are a statistical analyst working inside an automated data exploration system.
Your job is to answer a specific analytical question using rigorous statistical methods.

APPROACH:
1. Understand the schema with describe_table. Identify the columns you need.
2. Extract a targeted DataFrame using extract_dataframe. CRITICAL memory rules:
   - Select ONLY the columns your analysis needs (not SELECT *)
   - Apply WHERE filters to focus on the relevant subset
   - Use LIMIT or TABLESAMPLE for very large tables
   - If you need > 50,000 rows, justify why sampling won't work
3. Run your statistical analysis with run_python:
   - Use scipy.stats for hypothesis tests (ttest_ind, chi2_contingency, pearsonr, etc.)
   - Use statsmodels for regression (OLS, logit, with summary())
   - Always report: test statistic, p-value, effect size, confidence intervals
   - Check assumptions before running tests (normality, homoscedasticity, sample size)
   - If assumptions are violated, use non-parametric alternatives
4. Interpret results:
   - p < 0.05 is suggestive, p < 0.01 is strong evidence
   - Always pair p-values with effect sizes - statistical significance != practical significance
   - Report confidence intervals, not just point estimates
   - Flag any data quality issues (missing values, outliers, small samples)

AVOID:
- SELECT * - always specify columns
- Pulling more data than needed - think about what the test actually requires
- Running tests without checking assumptions
- Reporting only p-values without effect sizes
- Treating correlation as causation (unless explicitly doing causal inference)

OUTPUT (raw JSON, no fences):
{
  "title": "Short finding title (<=10 words)",
  "claim": "1-2 sentence finding with specific statistics (coefficient, p-value, CI, effect size)",
  "confidence": 0.0-1.0,
  "effect_size": <number or null>,
  "statistical_tests": [
    {"test": "test name", "statistic": 0.0, "p_value": 0.0, "effect_size": 0.0, "interpretation": "..."}
  ],
  "methodology": "Brief description of statistical approach and assumptions checked",
  "tags": ["tag1", "tag2"],
  "extraction_sql": "The SQL used to extract the analysis dataset",
  "python_code": "The key Python analysis code",
  "sample_size": 0,
  "data_quality_notes": "Any issues with missing data, outliers, etc."
}
"""


@dataclass
class AnalystResult:
    title: str
    claim: str
    confidence: float
    effect_size: float | None
    statistical_tests: list[dict[str, Any]]
    methodology: str
    tags: list[str]
    extraction_sql: str
    python_code: str
    sample_size: int
    data_quality_notes: str
    all_sql: list[str]

    def to_execution_result(self) -> ExecutionResult:
        """Adapter so the interpreter can consume analyst output."""
        summary_rows: list[dict[str, Any]] = []
        for test in self.statistical_tests:
            summary_rows.append({
                "test": test.get("test", ""),
                "statistic": test.get("statistic"),
                "p_value": test.get("p_value"),
                "effect_size": test.get("effect_size"),
                "interpretation": test.get("interpretation", ""),
            })
        return ExecutionResult(
            sql=self.extraction_sql,
            rows=summary_rows,
            row_count=len(summary_rows),
            column_names=["test", "statistic", "p_value", "effect_size", "interpretation"],
            truncated=False,
            cost_ms=0,
            error=None,
        )


async def run_statistical_analysis(
    *,
    question: str,
    extraction_sql: str,
    table: str,
    profiles: list[TableProfile],
    store: NemoStore,
    config: NemoConfig,
    client: OpenAI,
    max_iterations: int | None = None,
    on_iteration: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
) -> AnalystResult | None:
    """Run a ReAct statistical analysis loop and return structured output."""
    effective_max_rows = max(1, int(getattr(config, "max_analysis_rows", 50_000)))
    effective_max_memory_mb = max(1, int(getattr(config, "max_analysis_memory_mb", 256)))
    effective_timeout = max(1, int(getattr(config, "analysis_timeout_seconds", 30)))
    iteration_budget = max_iterations or int(getattr(config, "analyst_max_iterations", 8))
    warning_messages: list[str] = []

    session = PythonSession(timeout_seconds=effective_timeout)
    all_sql: list[str] = []
    conversation: list[dict[str, Any]] = [{
        "role": "user",
        "content": (
            f"Question: {question}\n"
            f"Primary table: {table}\n"
            f"Suggested extraction SQL:\n{extraction_sql}\n\n"
            "Use that SQL if appropriate, but refine as needed."
        ),
    }]

    for iteration_num in range(1, iteration_budget + 1):
        response = None
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    client.responses.create,
                    model=config.model or "gpt-5-mini",
                    instructions=ANALYST_INSTRUCTIONS,
                    input=conversation,
                    tools=ANALYST_TOOLS,
                )
                break
            except (APIConnectionError, APITimeoutError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
            except RateLimitError:
                if attempt == 2:
                    raise
                await asyncio.sleep(5 * (attempt + 1))

        if response is None:
            return None

        tool_calls = [item for item in response.output if item.type == "function_call"]
        if not tool_calls:
            await _notify_iteration(
                on_iteration,
                {
                    "iteration": iteration_num,
                    "analyst_max_iterations": iteration_budget,
                    "analyst_stage": "finalizing",
                    "analyst_tool_calls": [],
                },
            )
            final_text = _extract_text(response)
            return _parse_analyst_result(final_text, all_sql, extraction_sql)

        await _notify_iteration(
            on_iteration,
            {
                "iteration": iteration_num,
                "analyst_max_iterations": iteration_budget,
                "analyst_stage": "tooling",
                "analyst_tool_calls": [str(tc.name) for tc in tool_calls],
            },
        )
        for tc in tool_calls:
            warning_count_before = len(warning_messages)
            result_str = _dispatch_tool(
                tc.name,
                json.loads(tc.arguments) if tc.arguments else {},
                store=store,
                profiles=profiles,
                session=session,
                all_sql=all_sql,
                max_rows=effective_max_rows,
                max_memory_mb=effective_max_memory_mb,
                warnings=warning_messages,
            )
            if len(warning_messages) > warning_count_before:
                for warning in warning_messages[warning_count_before:]:
                    await _notify_iteration(
                        on_iteration,
                        {
                            "iteration": iteration_num,
                            "analyst_max_iterations": iteration_budget,
                            "analyst_stage": "memory_warning",
                            "warning": warning,
                        },
                    )
            conversation.append(
                {"type": "function_call", "call_id": tc.call_id, "name": tc.name, "arguments": tc.arguments}
            )
            conversation.append({"type": "function_call_output", "call_id": tc.call_id, "output": result_str})

    await _notify_iteration(
        on_iteration,
        {
            "iteration": iteration_budget,
            "analyst_max_iterations": iteration_budget,
            "analyst_stage": "max_iterations_reached",
            "warning": "Analyst reached max iterations before producing a final structured result.",
        },
    )
    return None


def _dispatch_tool(
    name: str,
    args: dict[str, Any],
    *,
    store: NemoStore,
    profiles: list[TableProfile],
    session: PythonSession,
    all_sql: list[str],
    max_rows: int,
    max_memory_mb: int = 256,
    memory_warn_fraction: float = 0.8,
    warnings: list[str] | None = None,
) -> str:
    if name == "describe_table":
        table_name = str(args.get("table", ""))
        described = _describe_table(table_name, profiles, store)
        return _safe_json_dumps(described)

    if name == "extract_dataframe":
        sql = str(args.get("sql", ""))
        variable_name = str(args.get("variable_name", "df") or "df")
        all_sql.append(sql)
        try:
            df, meta = _extract_dataframe(sql, store, max_rows=max_rows)
        except Exception as exc:  # noqa: BLE001
            return _safe_json_dumps({
                "error": str(exc),
                "failed_sql": sql,
            })
        session.session_vars[variable_name] = df
        payload: dict[str, Any] = {
            "variable_name": variable_name,
            **meta,
        }
        if max_memory_mb > 0:
            threshold_mb = max_memory_mb * memory_warn_fraction
            memory_mb = float(meta.get("memory_mb") or 0.0)
            if memory_mb >= threshold_mb:
                warning = (
                    f"Extracted DataFrame uses {memory_mb:.2f}MB, which exceeds "
                    f"{memory_warn_fraction:.0%} of max_analysis_memory_mb ({max_memory_mb}MB)."
                )
                payload["warning"] = warning
                if warnings is not None:
                    warnings.append(warning)
        return _safe_json_dumps(payload)

    if name == "run_python":
        code = str(args.get("code", ""))
        result = session.execute(code)
        return _safe_json_dumps({
            "result": result.result,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": result.error,
            "duration_ms": result.duration_ms,
            "memory_mb": result.memory_mb,
        })

    return _safe_json_dumps({"error": f"unknown tool: {name}"})


def _describe_table(table_name: str, profiles: list[TableProfile], store: NemoStore) -> dict[str, Any]:
    for profile in profiles:
        if profile.name == table_name:
            return {
                "table": table_name,
                "row_count": profile.row_count,
                "columns": [
                    {
                        "name": col.name,
                        "type": col.dtype,
                        "distinct": col.distinct_count,
                        "nulls": col.null_count,
                        "sample": col.sample_values[:3] if col.sample_values else [],
                    }
                    for col in profile.columns
                ],
            }

    if not table_name:
        return {"error": "table is required"}
    if not store.table_exists(table_name):
        return {"error": f"table '{table_name}' not found"}

    safe_table = _quote_ident(table_name)
    row_count = int(store.execute(f"SELECT COUNT(*) FROM {safe_table}").fetchone()[0])
    columns = []
    for row in store.execute(f"PRAGMA table_info({safe_table})").fetchall():
        col_name = str(row[1])
        col_type = str(row[2])
        safe_col = _quote_ident(col_name)
        distinct = int(
            store.execute(
                f"SELECT COUNT(DISTINCT {safe_col}) FROM {safe_table} WHERE {safe_col} IS NOT NULL"
            ).fetchone()[0]
        )
        sample_rows = store.execute(
            f"SELECT DISTINCT {safe_col} FROM {safe_table} WHERE {safe_col} IS NOT NULL LIMIT 3"
        ).fetchall()
        columns.append({
            "name": col_name,
            "type": col_type,
            "distinct": distinct,
            "sample": [r[0] for r in sample_rows],
        })
    return {"table": table_name, "row_count": row_count, "columns": columns}


def _extract_dataframe(sql: str, store: NemoStore, max_rows: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Execute SQL and return a pandas DataFrame with metadata."""
    _validate_select_only(sql)
    bounded_sql = _apply_row_cap(sql, max_rows)
    df = store.execute(bounded_sql).fetchdf()
    if len(df) > max_rows:
        df = df.iloc[:max_rows].copy()
    meta = {
        "shape": list(df.shape),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "memory_mb": round(float(df.memory_usage(deep=True).sum()) / 1_048_576, 4),
        "head": df.head(5).to_dict(orient="records"),
        "executed_sql": bounded_sql,
        "hit_row_cap": bool(len(df) >= max_rows),
    }
    return df, meta


def _apply_row_cap(sql: str, max_rows: int) -> str:
    if _has_limit(sql):
        return f"SELECT * FROM ({sql}) AS _sub LIMIT {int(max_rows)}"
    return f"SELECT * FROM ({sql}) AS _sub LIMIT {int(max_rows)}"


def _has_limit(sql: str) -> bool:
    return re.search(r"\blimit\s+\d+\b", sql, flags=re.IGNORECASE) is not None


def _validate_select_only(sql: str) -> None:
    cleaned = sql.strip()
    if not cleaned:
        raise ValueError("empty SQL")
    lowered = cleaned.lower()
    forbidden = ("insert ", "update ", "delete ", "drop ", "create ", "alter ", "truncate ", "attach ", "copy ")
    if any(token in lowered for token in forbidden):
        raise ValueError("only SELECT queries are allowed in analyst extraction")
    statements = [part.strip() for part in cleaned.split(";") if part.strip()]
    if len(statements) != 1:
        raise ValueError("query must contain exactly one statement")
    first = _strip_leading_comments(statements[0]).lstrip().lower()
    if not (first.startswith("select") or first.startswith("with")):
        raise ValueError("query must start with SELECT or WITH")


def _strip_leading_comments(statement: str) -> str:
    lines = statement.splitlines()
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("--"):
            idx += 1
            continue
        break
    return "\n".join(lines[idx:])


def _parse_analyst_result(text: str, all_sql: list[str], default_sql: str) -> AnalystResult | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return None
        try:
            obj = json.loads(match.group())
        except json.JSONDecodeError:
            return None

    return AnalystResult(
        title=str(obj.get("title", "Statistical finding")),
        claim=str(obj.get("claim", cleaned[:500])),
        confidence=max(0.0, min(1.0, float(obj.get("confidence", 0.5)))),
        effect_size=_to_float_or_none(obj.get("effect_size")),
        statistical_tests=[dict(item) for item in (obj.get("statistical_tests") or []) if isinstance(item, dict)],
        methodology=str(obj.get("methodology", "")),
        tags=[str(tag) for tag in (obj.get("tags") or [])],
        extraction_sql=str(obj.get("extraction_sql") or (all_sql[-1] if all_sql else default_sql)),
        python_code=str(obj.get("python_code", "")),
        sample_size=int(obj.get("sample_size", 0) or 0),
        data_quality_notes=str(obj.get("data_quality_notes", "")),
        all_sql=all_sql.copy(),
    )


def _extract_text(response: Any) -> str:
    if getattr(response, "output_text", None):
        return str(response.output_text)
    chunks: list[str] = []
    for item in getattr(response, "output", []):
        if getattr(item, "type", None) != "message":
            continue
        for block in getattr(item, "content", []):
            text = getattr(block, "text", None)
            if text:
                chunks.append(str(text))
    return "\n".join(chunks).strip()


def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            pass
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _notify_iteration(
    callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    maybe_awaitable = callback(payload)
    if asyncio.iscoroutine(maybe_awaitable):
        await maybe_awaitable


def _quote_ident(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'
