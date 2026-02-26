"""Lightweight ReAct SQL agent for goal-directed data exploration.

Instead of deterministic SQL templates, this module lets an LLM decide what
queries to run, reason about results, and iteratively explore until it finds
something meaningful.  No LangChain dependency — uses the OpenAI Responses API
tool-calling loop directly.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from openai import OpenAI

from nemo.config import NemoConfig
from nemo.executor.run import ExecutionResult, execute_query
from nemo.ingest.profile import TableProfile
from nemo.planner.models import FrontierItem
from nemo.store import NemoStore

AGENT_TOOLS = [
    {
        "type": "function",
        "name": "list_tables",
        "description": "List every user table in the database with row counts.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "describe_table",
        "description": (
            "Return column names, data types, distinct counts, null counts, "
            "and sample values for one table."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table name to describe"},
            },
            "required": ["table"],
        },
    },
    {
        "type": "function",
        "name": "run_sql",
        "description": (
            "Execute a read-only SQL SELECT query and return up to 30 result rows. "
            "Prefer concise aggregations over SELECT *."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A DuckDB-compatible SELECT query"},
            },
            "required": ["sql"],
        },
    },
]

AGENT_INSTRUCTIONS = """\
You are a data exploration agent working inside an automated insight-discovery system.
Your job is to find ONE meaningful, business-relevant insight by querying the database.

APPROACH:
1. Start by understanding the schema (use describe_table). Focus on columns that
   represent real business metrics (revenue, costs, quantities, prices, balances,
   dates, statuses) — NOT surrogate keys or IDs.
2. Form a hypothesis about an interesting pattern (segment differences, outliers,
   trends, correlations between business metrics, data quality issues).
3. Run targeted SQL to test the hypothesis.  Use DuckDB SQL syntax.  Use
   aggregations, GROUP BY, HAVING, window functions — whatever best answers the
   question.
4. ERROR RECOVERY: if a query fails, READ THE ERROR MESSAGE carefully, fix your
   SQL, and retry.  Common fixes: double-quote identifiers that conflict with
   reserved words, verify column names with describe_table, use DuckDB-compatible
   functions (not MySQL/Postgres-only syntax).
5. QUALITY GATE: if a query succeeds but the result is uninteresting or trivial,
   do NOT report it.  Ask a DIFFERENT, deeper question instead.
6. Before giving your final answer, sanity-check: would a data analyst actually
   care about this finding?  If not, dig deeper or try a different angle.

AVOID:
- Statistics on primary keys, surrogate keys, or sequential IDs — these are meaningless.
- Trivial findings ("the table has N rows", "the average ID is X").
- Restating the schema without analysis.
- Reporting a finding you aren't confident about — keep exploring instead.

OUTPUT (respond with raw JSON, no code fences):
{
  "title": "Short finding title (≤10 words)",
  "claim": "1-2 sentence description of the finding with specific numbers.",
  "confidence": 0.0-1.0,
  "effect_size": <number or null>,
  "tags": ["tag1", "tag2"],
  "sql": "The most important SQL query you ran",
  "key_rows": [{"col": "val", ...}]
}
"""

# Action types that benefit from agent-driven exploration.
AGENT_ACTION_TYPES = frozenset({
    "COVERAGE_EXPLORER",
    "DATA_QUALITY_CHECK",
    "CORRELATION_SCAN",
})


def goal_for_action(action: FrontierItem, profiles: list[TableProfile]) -> str:
    """Derive a natural-language exploration goal from a frontier action."""
    payload = action.payload
    table = payload.get("table", "")
    action_type = action.action_type

    table_info = ""
    for p in profiles:
        if p.name == table:
            col_summaries = ", ".join(
                f"{c.name} ({c.dtype}, {c.distinct_count} distinct)"
                for c in p.columns
            )
            table_info = (
                f"Table '{table}' has {p.row_count:,} rows.  "
                f"Columns: {col_summaries}"
            )
            break

    if action_type == "COVERAGE_EXPLORER":
        return (
            f"Explore the '{table}' table and find the single most interesting "
            f"business-relevant pattern.  Look for segment differences, outliers, "
            f"or surprising distributions in real metrics (NOT keys/IDs).\n\n{table_info}"
        )
    if action_type == "DATA_QUALITY_CHECK":
        key_cols = payload.get("key_columns", [])
        return (
            f"Audit data quality in '{table}'.  Check for: NULL counts in important "
            f"columns, duplicate keys ({', '.join(key_cols) or 'auto-detect'}), "
            f"impossible values (negative prices, future dates, etc.), and "
            f"referential-integrity issues.\n\n{table_info}"
        )
    if action_type == "CORRELATION_SCAN":
        return (
            f"Find meaningful correlations between business metrics in '{table}'.  "
            f"Ignore ID/key columns.  Report the strongest correlation with its "
            f"coefficient and a brief interpretation.\n\n{table_info}"
        )
    metric = payload.get("metric_col", "")
    dim = payload.get("dimension_col") or payload.get("group_col", "")
    if metric and dim:
        return (
            f"Analyse how '{metric}' varies across '{dim}' in '{table}'.  "
            f"Find the most notable differences or outliers.\n\n{table_info}"
        )
    return f"Find the most interesting pattern in '{table}'.\n\n{table_info}"


async def run_agent_exploration(
    action: FrontierItem,
    profiles: list[TableProfile],
    store: NemoStore,
    config: NemoConfig,
    client: OpenAI,
    max_iterations: int = 8,
) -> AgentResult | None:
    """Run a multi-step ReAct agent and return structured findings."""
    goal = goal_for_action(action, profiles)
    conversation: list[dict[str, Any]] = [{"role": "user", "content": goal}]
    all_sql: list[str] = []
    retried_trivial = False

    for _ in range(max_iterations):
        response = await asyncio.to_thread(
            client.responses.create,
            model=config.model or "gpt-5-mini",
            instructions=AGENT_INSTRUCTIONS,
            input=conversation,
            tools=AGENT_TOOLS,
        )

        tool_calls = [item for item in response.output if item.type == "function_call"]

        if not tool_calls:
            text_parts = [
                item for item in response.output
                if getattr(item, "type", None) == "message"
            ]
            if not text_parts:
                return None

            final_text = text_parts[0].content[0].text
            result = _parse_agent_result(final_text, all_sql)

            if result is not None and _looks_trivial(result) and not retried_trivial:
                retried_trivial = True
                conversation.append({"role": "assistant", "content": final_text})
                conversation.append({
                    "role": "user",
                    "content": (
                        "That finding is trivial (statistics on IDs/keys, or just "
                        "restating the schema).  Please dig deeper — look for a "
                        "genuinely surprising business pattern in the data.  "
                        "Use run_sql with a more analytical query."
                    ),
                })
                continue

            return result

        for tc in tool_calls:
            result_str = _dispatch_tool(
                tc.name,
                json.loads(tc.arguments) if tc.arguments else {},
                store,
                config,
                profiles,
                all_sql,
            )
            conversation.append(
                {"type": "function_call", "call_id": tc.call_id,
                 "name": tc.name, "arguments": tc.arguments}
            )
            conversation.append(
                {"type": "function_call_output", "call_id": tc.call_id,
                 "output": result_str}
            )

    return None


class AgentResult:
    """Structured result from the agent exploration loop."""

    __slots__ = ("title", "claim", "confidence", "effect_size", "tags", "sql",
                 "key_rows", "all_sql")

    def __init__(
        self,
        title: str,
        claim: str,
        confidence: float,
        effect_size: float | None,
        tags: list[str],
        sql: str,
        key_rows: list[dict[str, Any]],
        all_sql: list[str],
    ):
        self.title = title
        self.claim = claim
        self.confidence = min(1.0, max(0.0, confidence))
        self.effect_size = effect_size
        self.tags = tags
        self.sql = sql
        self.key_rows = key_rows
        self.all_sql = all_sql


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dispatch_tool(
    name: str,
    args: dict[str, Any],
    store: NemoStore,
    config: NemoConfig,
    profiles: list[TableProfile],
    all_sql: list[str],
) -> str:
    if name == "list_tables":
        datasets = store.get_datasets()
        info = []
        for d in datasets:
            row_count = 0
            for p in profiles:
                if p.name == d["name"]:
                    row_count = p.row_count
                    break
            info.append({"table": d["name"], "rows": row_count})
        return _safe_json_dumps(info)

    if name == "describe_table":
        table_name = args.get("table", "")
        for p in profiles:
            if p.name == table_name:
                cols = [
                    {
                        "name": c.name,
                        "type": c.dtype,
                        "distinct": c.distinct_count,
                        "nulls": c.null_count,
                        "sample": c.sample_values[:3] if c.sample_values else [],
                    }
                    for c in p.columns
                ]
                return _safe_json_dumps({"row_count": p.row_count, "columns": cols})
        return _safe_json_dumps({"error": f"table '{table_name}' not found"})

    if name == "run_sql":
        sql = args.get("sql", "")
        all_sql.append(sql)
        result = execute_query(store, sql, config)
        if result.error:
            return _safe_json_dumps({
                "error": result.error,
                "failed_sql": sql,
                "hint": "Check column names with describe_table. "
                        "Use double-quotes for identifiers. "
                        "DuckDB syntax may differ from Postgres/MySQL.",
            })
        return _safe_json_dumps({
            "columns": result.column_names,
            "rows": result.rows[:30],
            "row_count": result.row_count,
            "truncated": result.truncated,
        })

    return _safe_json_dumps({"error": f"unknown tool: {name}"})


def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


_TRIVIAL_SIGNALS = re.compile(
    r"(average\s+(key|id)\b|avg.*_key\b|avg.*_id\b|avg.*_sk\b|avg.*_pk\b"
    r"|trivial|schema\s+(has|contains|shows)|table\s+has\s+\d+\s+(rows|columns))",
    re.IGNORECASE,
)


def _looks_trivial(result: AgentResult) -> bool:
    """Heuristic check: does the agent result look like a meaningless finding?"""
    if result.confidence <= 0.25:
        return True
    text = f"{result.title} {result.claim} {result.sql}"
    if _TRIVIAL_SIGNALS.search(text):
        return True
    return False


def _parse_agent_result(text: str, all_sql: list[str]) -> AgentResult | None:
    """Extract a structured AgentResult from the agent's final text response."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                obj = json.loads(match.group())
            except json.JSONDecodeError:
                return None
        else:
            return None

    return AgentResult(
        title=str(obj.get("title", "Agent finding")),
        claim=str(obj.get("claim", cleaned[:500])),
        confidence=float(obj.get("confidence", 0.5)),
        effect_size=obj.get("effect_size"),
        tags=[str(t) for t in (obj.get("tags") or [])],
        sql=str(obj.get("sql", all_sql[-1] if all_sql else "")),
        key_rows=obj.get("key_rows") or [],
        all_sql=all_sql,
    )
