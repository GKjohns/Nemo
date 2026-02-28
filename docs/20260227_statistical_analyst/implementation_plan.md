# Statistical Analyst: Python-Powered Analysis for Nemo

## Problem Statement

Nemo currently explores data exclusively through SQL. SQL excels at aggregation, filtering, and grouping — but it cannot do real statistics. There's no way to compute p-values, run linear regressions, test for causal relationships, calculate proper effect sizes, or perform any of the inferential statistics that separate "interesting pattern" from "statistically significant finding."

When the strategist says "providers who see more patients tend to bill higher amounts," that's an observation from a GROUP BY. It's not a regression coefficient with a confidence interval. When it says "there's a difference between specialties," that's not an ANOVA. The confidence scores the LLM assigns are vibes, not statistics.

A real data scientist working with a warehouse would:
1. Use SQL to extract a targeted slice (specific columns, filtered/sampled rows) into Python memory
2. Run proper statistical tests with scipy/statsmodels/pandas
3. Interpret the statistical output in context

Nemo should do the same.

## Design Decision: Where Does This Fit?

### Options Considered

**Option A: Separate agent that runs independently**
A standalone "Statistical Analyst" agent with its own loop, separate from the strategist. The engine would alternate between the SQL strategist and this agent.

*Rejected.* This fragments the investigation context. The notebook, themes, evidence graph, and hypothesis backlog are shared state that both SQL and Python analysis contribute to. Running them as separate agents means duplicating context or introducing complex synchronization. It also means the arbiter needs to decide between three things (explore/exploit/analyze) instead of letting the existing phases naturally incorporate statistical analysis.

**Option B: Replace the strategist with a multi-tool agent**
Make the strategist itself an agentic loop (like `executor/agent.py`) that can call both SQL and Python tools.

*Rejected.* The strategist is deliberately NOT agentic — it plans one step at a time, and the engine controls the outer loop. Making it agentic would blur the clean separation between planning, execution, and interpretation. The current architecture's strength is that each step is auditable: one question, one query, one interpretation, one notebook update. An agentic strategist could run 8 internal iterations and produce a single opaque finding.

**Option C (Chosen): Analysis phase within the existing step lifecycle**
The strategist can propose a step that requires statistical analysis instead of (or in addition to) SQL. When it does, the engine routes execution to a **Statistical Analyst** — a ReAct agent with both SQL extraction and Python execution tools. The analyst's output flows back into the same interpretation → notebook → evidence pipeline.

This is the right fit because:
- The strategist stays in control of *what* to investigate (single question per step)
- The analyst handles *how* — the multi-step SQL→Python→result workflow
- The notebook, hypotheses, and evidence graph stay unified
- The arbiter doesn't need to change — statistical analysis can happen in both explore and exploit phases
- It mirrors the existing `executor/agent.py` pattern, which already handles multi-step exploration for coverage/quality/correlation actions

### Analogy to Current Architecture

Today:
```
Strategist plans → engine executes SQL → interpreter reads results → notebook updated
```

With this change:
```
Strategist plans → engine checks analysis_type:
  ├─ "sql"         → execute_query (unchanged)
  └─ "statistical" → run_analyst_agent (SQL extract → Python analysis → structured results)
→ interpreter reads results → notebook updated
```

The analyst agent is **not** a new outer loop. It's a new execution backend, like how `executor/agent.py` is already an alternative execution path for `COVERAGE_EXPLORER` and friends.

## Detailed Design

### Conventions (must follow)

This implementation must follow the repo's established standards:

- **LLM calls**: Follow `docs/llm_guide.md`:
  - Use the **Responses API** (`client.responses.create` / `client.responses.parse`) only.
  - Prefer **Pydantic structured outputs** via `responses.parse()` wherever the output is structured.
  - Use `response.output_text` when you need plain text; do **not** assume text is at `output[0]...`.
  - **Retry** on `APIConnectionError`, `APITimeoutError`, and `RateLimitError` using the same backoff patterns already used in Nemo.
  - Reuse the **single OpenAI client per run** created by the engine (`make_client(config)`).

- **Testing**: Follow `docs/testing.md`:
  - Smoke tests should run against the same venv + invocation style: `.venv/bin/python -m pytest ...`
  - Default smoke tests should run the **fast suite** (slow/e2e tests are opt-in and explicitly invoked).

### 1. Strategist Changes

The `Hypothesis` model gains one field:

```python
class Hypothesis(BaseModel):
    question: str
    reasoning: str
    sql: str
    table: str = ""
    analysis_type: Literal["sql", "statistical"] = "sql"
```

The strategist system prompt is updated to teach it when to choose `"statistical"`:

> You have two execution modes:
> - **sql**: Standard DuckDB query. Use for aggregations, counts, distributions, GROUP BY comparisons, ranking, filtering. This is the default and preferred mode for most questions.
> - **statistical**: Python-based statistical analysis. Use ONLY when the question genuinely requires inferential statistics that SQL cannot perform:
>   - Hypothesis testing (t-tests, chi-squared, ANOVA, Mann-Whitney)
>   - Regression analysis (linear, logistic, with coefficients and p-values)
>   - Correlation with significance testing
>   - Effect size calculations (Cohen's d, odds ratios, risk ratios)
>   - Causal inference techniques (propensity scoring, difference-in-differences)
>   - Distribution fitting and goodness-of-fit tests
>   - Confidence intervals that require bootstrapping
>
> When choosing "statistical", your SQL field should contain the extraction query — the SELECT that pulls the specific columns needed for analysis. Keep it narrow: only the columns the test requires, with appropriate WHERE filters or sampling. The Python analyst will handle the rest.
>
> Rule of thumb: if SQL can answer it with a GROUP BY and you'd just be eyeballing the numbers, use "sql". If you need a p-value, a regression coefficient, or a formal test, use "statistical".

The strategist prompt already has access to schema context with row counts, so it can make informed decisions about what to extract. The SQL field in a `"statistical"` hypothesis serves as the data extraction query, not the final analysis.

### 2. The Statistical Analyst Agent (`executor/analyst.py`)

A new ReAct agent, following the established pattern in `executor/agent.py`.

#### Tools

```python
ANALYST_TOOLS = [
    {
        "type": "function",
        "name": "describe_table",
        "description": "Return column names, types, distinct counts, and sample values for a table.",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table name"}
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
```

#### Agent Instructions

```
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
   - Always pair p-values with effect sizes — statistical significance ≠ practical significance
   - Report confidence intervals, not just point estimates
   - Flag any data quality issues (missing values, outliers, small samples)

AVOID:
- SELECT * — always specify columns
- Pulling more data than needed — think about what the test actually requires
- Running tests without checking assumptions
- Reporting only p-values without effect sizes
- Treating correlation as causation (unless explicitly doing causal inference)

OUTPUT (raw JSON, no fences):
{
  "title": "Short finding title (≤10 words)",
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
```

#### Python Sandbox

The Python execution environment uses a restricted `exec()` with a controlled namespace:

```python
import io
import sys
import signal
from contextlib import redirect_stdout, redirect_stderr

ALLOWED_IMPORTS = {
    "pandas", "numpy", "scipy", "scipy.stats",
    "statsmodels", "statsmodels.api", "statsmodels.formula.api",
    "statsmodels.stats.proportion", "statsmodels.stats.weightstats",
    "statsmodels.stats.diagnostic", "statsmodels.stats.outliers_influence",
    "math", "statistics", "collections", "itertools", "functools",
    "datetime", "decimal", "json", "re",
}

def execute_python(
    code: str,
    session_vars: dict,
    timeout_seconds: int = 30,
    max_output_chars: int = 50_000,
) -> PythonResult:
    """Execute Python code in a controlled namespace with timeout."""
    namespace = {
        "__builtins__": _safe_builtins(),
        **session_vars,
    }

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    try:
        with _timeout(timeout_seconds):
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                exec(compile(code, "<analyst>", "exec"), namespace)
    except TimeoutError:
        return PythonResult(error="Execution timed out", ...)
    except Exception as exc:
        return PythonResult(error=str(exc), ...)

    # Extract _result if set, update session_vars with new DataFrames
    result_val = namespace.get("_result")
    # ... return structured result
```

Key safety measures:
- **No file I/O**: builtins restricted to exclude `open`, `exec`, `eval`, `__import__` (we provide the imports)
- **No network**: no `requests`, `urllib`, `socket` in allowed imports
- **Timeout**: signal-based timeout (configurable, default 30s)
- **Memory**: DataFrame extraction capped at `max_analysis_rows` (default 50,000)
- **Output cap**: stdout/stderr truncated at 50KB

The session is **stateful** across tool calls within one analyst invocation. DataFrames extracted in step 1 persist for step 2's Python code. This lets the agent iteratively refine its analysis.

### 3. Engine Integration

The change in `engine.py` is minimal. In `_execute_strategist_step`, after the strategist returns a hypothesis:

```python
async def _execute_strategist_step(self, *, run_id, step_num, hypothesis, notebook, schema_ctx):
    if hypothesis.analysis_type == "statistical":
        return await self._execute_statistical_analysis(
            run_id=run_id, step_num=step_num, hypothesis=hypothesis,
            notebook=notebook, schema_ctx=schema_ctx,
        )

    # ... existing SQL execution path (unchanged) ...
```

The new method:

```python
async def _execute_statistical_analysis(self, *, run_id, step_num, hypothesis, notebook, schema_ctx):
    await self._emit_step_phase(run_id, step_num, "analyzing", sql=hypothesis.sql)

    analyst_result = await run_statistical_analysis(
        question=hypothesis.question,
        extraction_sql=hypothesis.sql,
        table=hypothesis.table,
        profiles=self._profiles,
        store=self.store,
        config=self.config,
        client=self._llm_client,
    )

    if analyst_result is None:
        # Fall back to plain SQL execution
        await self._emit_step_phase(run_id, step_num, "executing", sql=hypothesis.sql)
        result = execute_query(self.store, hypothesis.sql, self.config)
        if result.error:
            self.store.insert_learning(
                run_id=run_id,
                category="error_pattern",
                subject=f"STRATEGIST:{hypothesis.table}",
                detail=result.error,
                confidence=0.7,
            )
            await self._emit_step_error(run_id, step_num, "executing", result.error, will_retry=False)
            return hypothesis, result, 1, True
        return hypothesis, result, 0, False

    # Convert AnalystResult → ExecutionResult-like object for the interpreter
    result = analyst_result.to_execution_result()
    return hypothesis, result, 0, False
```

#### AnalystResult

```python
@dataclass
class AnalystResult:
    title: str
    claim: str
    confidence: float
    effect_size: float | None
    statistical_tests: list[dict]
    methodology: str
    tags: list[str]
    extraction_sql: str
    python_code: str
    sample_size: int
    data_quality_notes: str
    all_sql: list[str]

    def to_execution_result(self) -> ExecutionResult:
        """Adapter so the interpreter can consume analyst output."""
        summary_rows = []
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
```

This adapter means the interpreter receives statistical test results as structured rows, and the existing `interpret_and_update` flow works without changes. The interpreter already handles arbitrary result shapes — it will see rows like `test=OLS regression, statistic=0.342, p_value=0.001` and interpret them naturally.

### 4. Interpreter Enhancement

The interpreter prompt gets a small addition to handle statistical results:

> If the query results contain statistical test output (p-values, test statistics, effect sizes, confidence intervals), interpret them rigorously:
> - Report the specific test used and its assumptions
> - Distinguish statistical significance (p < 0.05) from practical significance (effect size)
> - If multiple tests were run, note any multiple-comparison concerns
> - Translate statistical findings into business language

No structural changes to `InterpretationResult` — the existing fields (`claim`, `confidence`, `effect_size`, `reasoning`) already accommodate statistical findings. The `effect_size` field was always there but rarely populated meaningfully; now it will be.

### 5. Validator Enhancement

The exploit phase's validator (`planner/validator.py`) currently generates SQL-only validation steps. Statistical analysis is a natural fit for several validation stages:

- **REPRODUCE**: Re-run with a proper statistical test instead of just re-aggregating
- **CONFOUND**: Use regression with control variables, not just stratified GROUP BY
- **QUANTIFY**: Calculate proper effect sizes, confidence intervals, power analysis

The validator prompt gets updated to allow `analysis_type: "statistical"` in its output, with the same routing logic as the explore phase. The `STEP_GUIDANCE` dict gets statistical suggestions:

```python
STEP_GUIDANCE = {
    "reproduce": (
        "Re-test the core signal with a formal statistical test (t-test, chi-squared, "
        "regression). Confirm whether the signal is statistically significant, not just "
        "visually apparent in aggregations. Use analysis_type='statistical' if appropriate."
    ),
    # ... etc
}
```

### 6. Memory Management Strategy

This is the critical design constraint. The agent must think like a data scientist pulling from a warehouse:

#### Extraction Budget

```python
@dataclass
class AnalysisBudget:
    max_rows: int = 50_000          # Hard cap on extracted rows
    max_columns: int = 20           # Soft guidance (agent prompt, not enforced)
    max_memory_mb: int = 256        # Estimated memory cap
    timeout_seconds: int = 30       # Per-execution timeout
```

The `extract_dataframe` tool enforces `max_rows` at the DuckDB level (appends LIMIT if not present). The agent's prompt teaches column discipline.

#### Smart Extraction Patterns

The agent prompt includes guidance on common extraction patterns:

> **Memory-efficient extraction patterns:**
>
> 1. **Narrow & deep**: Few columns, many rows. Good for correlation/regression on 2-3 variables.
>    `SELECT col_a, col_b FROM big_table WHERE condition LIMIT 50000`
>
> 2. **Pre-aggregated**: Aggregate in SQL, analyze in Python. Good for ANOVA, chi-squared.
>    `SELECT category, AVG(metric) as mean_metric, COUNT(*) as n, STDDEV(metric) as sd FROM table GROUP BY category`
>
> 3. **Sampled**: Random sample for distribution analysis.
>    `SELECT cols FROM table USING SAMPLE 10000`
>
> 4. **Two-stage**: SQL does the heavy filtering, Python does the stats.
>    `SELECT a, b, c FROM table WHERE important_filter AND date > '2023-01-01'`
>
> **When to NOT pull into Python:**
> - Single aggregates (MIN, MAX, COUNT, AVG) — just use SQL
> - Simple GROUP BY comparisons — SQL is faster and cheaper
> - Existence checks, ranking, top-N — SQL
>
> **When Python is worth it:**
> - You need a p-value or confidence interval
> - You need to control for confounders (regression)
> - You need to fit a distribution or test normality
> - You need bootstrapped estimates
> - You need causal inference techniques

#### DuckDB ↔ Pandas Bridge

DuckDB has native pandas integration. The extraction tool uses this:

```python
def _extract_dataframe(sql: str, store: NemoStore, max_rows: int) -> tuple[pd.DataFrame, dict]:
    """Execute SQL and return a pandas DataFrame with metadata."""
    if not _has_limit(sql):
        sql = f"SELECT * FROM ({sql}) AS _sub LIMIT {max_rows}"

    df = store.execute(sql).fetchdf()  # Native DuckDB -> pandas via NemoStore API

    meta = {
        "shape": list(df.shape),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "memory_mb": round(df.memory_usage(deep=True).sum() / 1_048_576, 2),
        "head": df.head(5).to_dict(orient="records"),
    }
    return df, meta
```

DuckDB's `fetchdf()` is zero-copy where possible, making the SQL→pandas bridge efficient.

### 7. Configuration

New fields in `NemoConfig`:

```python
# Statistical analysis
enable_statistical_analysis: bool = True
max_analysis_rows: int = 50_000
max_analysis_memory_mb: int = 256
analysis_timeout_seconds: int = 30
analyst_max_iterations: int = 8
```

New section in `nemo.toml`:

```toml
[analysis]
enable_statistical_analysis = true
max_analysis_rows = 50_000
max_analysis_memory_mb = 256
analysis_timeout_seconds = 30
analyst_max_iterations = 8
```

### 8. New Dependencies

```toml
dependencies = [
    # ... existing ...
    "pandas",
    "numpy",
    "scipy",
    "statsmodels",
]
```

These are standard data science packages. `pandas` and `numpy` are essentially universal. `scipy` provides the core statistical tests. `statsmodels` provides regression, time series, and more formal econometric methods.

### 9. Event System

No new `EventType` entries are required. Reuse existing step lifecycle events:

- Emit `STEP_PHASE` with `phase="analyzing"` when statistical execution begins.
- Emit the normal `STEP_COMPLETED` payload (already includes `claim`, `confidence`, `effect_size`, and preview rows).
- Keep event semantics consistent across SQL and statistical execution so subscribers stay simple.

## File Plan

### New Files

| File | Purpose |
|------|---------|
| `nemo/executor/analyst.py` | Statistical Analyst ReAct agent (main module) |
| `nemo/executor/sandbox.py` | Python execution sandbox (exec, timeout, safety) |

### Modified Files

| File | Change |
|------|--------|
| `nemo/planner/strategist.py` | Add `analysis_type` to `Hypothesis`, update system prompt |
| `nemo/planner/validator.py` | Allow `analysis_type` in validation steps, update guidance |
| `nemo/engine.py` | Route `"statistical"` hypotheses to analyst agent |
| `nemo/config.py` | Add `[analysis]` config fields |
| `nemo/display.py` and `nemo/tui/app.py` | Surface `phase="analyzing"` cleanly in CLI/TUI messaging |
| `nemo/report/brief.py` | Render statistical metadata (tests/effect sizes) when present |
| `pyproject.toml` | Add pandas, numpy, scipy, statsmodels dependencies |

### Unchanged

The store, graph, summarize, and scoring modules need **no changes**. The analyst's output flows through the existing `ExecutionResult` adapter, so insights, edges, and thread cards work as before.

## Implementation Phases

All commands below assume you have a local venv set up per `docs/testing.md`. If not:

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -e . pytest
```

### Phase 1: Sandbox & Execution Foundation [✅ Completed]

**Status:** Completed on 2026-02-27 (unit tests + smoke test passing)

**Work:**
- `executor/sandbox.py`: Python execution with timeout, safety, session management
- `PythonResult` dataclass for structured output
- Session state management (DataFrames persist across calls)
- Import allow-list enforcement
- Timeout via `signal.alarm` (Unix) or threading fallback

**Tests** (`tests/test_sandbox.py`):
- Basic execution: run `1 + 1`, verify `_result` capture
- Session persistence: set `df = ...` in call 1, read it in call 2
- Timeout enforcement: `while True: pass` raises within configured seconds
- Import restrictions: `import os; os.system("rm -rf /")` blocked
- Allowed imports: `import pandas as pd; import scipy.stats` succeeds
- Output capture: `print("hello")` appears in stdout
- Error handling: syntax errors and runtime exceptions return clean `PythonResult.error`
- Memory estimation: extracting a DataFrame reports approximate memory usage

**Smoke test:**
```bash
# Always run the fast suite first (docs/testing.md default path)
.venv/bin/python -m pytest -q

# Run the sandbox unit tests
.venv/bin/python -m pytest -q tests/test_sandbox.py

# Quick manual sanity: start a Python REPL and exercise the sandbox directly
.venv/bin/python -c "
from nemo.executor.sandbox import PythonSession
s = PythonSession()
r = s.execute('import pandas as pd; df = pd.DataFrame({\"a\": [1,2,3], \"b\": [4,5,6]}); _result = df.describe().to_dict()')
assert r.error is None, f'Sandbox failed: {r.error}'
assert '_result' in r.__dict__ or r.result is not None, 'No result captured'
print('Phase 1 smoke test PASSED')
"
```

---

### Phase 2: Analyst Agent

**Status:** Completed on 2026-02-27 (unit tests + smoke test passing)

**Work:**
- `executor/analyst.py`: ReAct agent with `describe_table`, `extract_dataframe`, `run_python` tools
- Agent instructions and prompt engineering
- `AnalystResult` dataclass and `to_execution_result()` adapter
- `extract_dataframe` bridges DuckDB → pandas via `fetchdf()` with row cap enforcement
- Tool dispatch function following the `executor/agent.py` pattern

**Tests** (`tests/test_analyst.py`):
- `extract_dataframe` tool: creates table in DuckDB, extracts to pandas, verifies shape and dtypes
- `extract_dataframe` row cap: table with 100K rows, cap at 50K, verify truncation
- `extract_dataframe` without LIMIT: auto-appends LIMIT to query
- `run_python` tool: executes code in sandbox, returns structured output
- `run_python` session continuity: extract DataFrame in one call, run stats in next
- `AnalystResult.to_execution_result()`: verify adapter produces valid `ExecutionResult`
- `_parse_analyst_result`: parse agent JSON output, verify field mapping
- Tool dispatch returns clean error JSON for unknown tools

**Smoke test:**
```bash
# Always run the fast suite first (docs/testing.md default path)
.venv/bin/python -m pytest -q

# Run analyst unit tests
.venv/bin/python -m pytest -q tests/test_analyst.py

# Integration check: verify the DuckDB → pandas bridge works end-to-end
.venv/bin/python -c "
from nemo.store import NemoStore
from nemo.executor.analyst import _extract_dataframe
from pathlib import Path
import tempfile, os

with tempfile.TemporaryDirectory() as td:
    store = NemoStore(Path(td) / 'test.duckdb')
    store.initialize()
    store.execute('CREATE TABLE demo (x DOUBLE, y DOUBLE, label VARCHAR)')
    store.execute(\"INSERT INTO demo SELECT random(), random(), CASE WHEN random() > 0.5 THEN 'A' ELSE 'B' END FROM range(1000)\")
    df, meta = _extract_dataframe('SELECT x, y, label FROM demo', store, max_rows=500)
    assert df.shape[0] <= 500, f'Row cap violated: {df.shape[0]}'
    assert list(df.columns) == ['x', 'y', 'label'], f'Wrong columns: {list(df.columns)}'
    assert meta['memory_mb'] < 1.0, f'Unexpectedly large: {meta[\"memory_mb\"]}MB'
    store.close()
print('Phase 2 smoke test PASSED')
"
```

---

### Phase 3: Strategist Integration [✅ Completed]

**Status:** Completed on 2026-02-28 (unit tests + smoke tests passing)

**Work:**
- Add `analysis_type` field to `Hypothesis` model (default: `"sql"`)
- Update strategist system/user prompts with SQL vs. statistical guidance
- Update engine routing: `_execute_strategist_step` checks `analysis_type`
- New `_execute_statistical_analysis` method on `NemoEngine`
- Config additions: `[analysis]` section in `NemoConfig` and `nemo.toml`
- `pyproject.toml`: add pandas, numpy, scipy, statsmodels to dependencies

**Tests:**
- `test_planner.py` additions: `Hypothesis` model validates `analysis_type` field, default is `"sql"`
- `test_executor.py` additions: engine routes `analysis_type="statistical"` to analyst, `"sql"` unchanged
- `test_config.py`: new `[analysis]` fields parse from TOML correctly, defaults work
- `test_events.py`: no enum additions needed; existing `STEP_PHASE` handling remains valid

**Smoke test:**
```bash
# Run all existing tests to verify no regressions
.venv/bin/python -m pytest -q

# Focused check: strategist produces valid Hypothesis with analysis_type
.venv/bin/python -c "
from nemo.planner.strategist import Hypothesis
h_sql = Hypothesis(question='test', reasoning='r', sql='SELECT 1', analysis_type='sql')
h_stat = Hypothesis(question='test', reasoning='r', sql='SELECT x FROM t', analysis_type='statistical')
assert h_sql.analysis_type == 'sql'
assert h_stat.analysis_type == 'statistical'
# Default should be sql
h_default = Hypothesis(question='test', reasoning='r', sql='SELECT 1')
assert h_default.analysis_type == 'sql'
print('Phase 3 smoke test PASSED')
"

# Run a 3-step exploration with TPC-H to confirm SQL path still works
# (statistical path won't fire in 3 steps but ensures no regressions)
.venv/bin/python -c "
import asyncio, tempfile, os
from pathlib import Path
from nemo.store import NemoStore
from nemo.config import NemoConfig
from nemo.engine import NemoEngine
from nemo.events import EventBus
from nemo.ingest.add import add_tpch

if not os.getenv('OPENAI_API_KEY'):
    print('SKIP: no API key')
    exit(0)

with tempfile.TemporaryDirectory() as td:
    store = NemoStore(Path(td) / 'nemo.duckdb')
    store.initialize()
    add_tpch(store, scale=0.01)
    config = NemoConfig(max_steps=3, max_runtime_minutes=5, openai_api_key=os.getenv('OPENAI_API_KEY'))
    engine = NemoEngine(store, config, EventBus())
    run_id = asyncio.run(engine.run(max_steps=3))
    rows = store.execute('SELECT COUNT(*) FROM insights').fetchone()
    assert rows[0] >= 1, f'Expected insights, got {rows[0]}'
    store.close()
print('Phase 3 e2e smoke test PASSED')
"
```

---

### Phase 4: Validator Integration

**Work:**
- Update `Hypothesis` output from `plan_validation_step` to support `analysis_type`
- Update `STEP_GUIDANCE` with statistical suggestions for REPRODUCE, CONFOUND, QUANTIFY
- Update validator system prompt to teach when statistical validation is appropriate
- Engine's exploit path routes through the same `_execute_statistical_analysis` when `analysis_type == "statistical"`

**Tests:**
- `test_planner.py` additions: validator output parses `analysis_type` field correctly
- Integration test: mock a hypothesis with a testable claim, run validator, verify it can produce a `"statistical"` step

**Smoke test:**
```bash
# Always run the fast suite first (docs/testing.md default path)
.venv/bin/python -m pytest -q

# Then run focused validator + analyst-related tests
.venv/bin/python -m pytest -q tests/test_planner.py tests/test_analyst.py tests/test_sandbox.py tests/test_executor.py

# Run a longer exploration (5 steps) that's more likely to trigger exploit phase
# and confirm the full explore → hypothesize → validate pipeline works
.venv/bin/python -c "
import asyncio, tempfile, os
from pathlib import Path
from nemo.store import NemoStore
from nemo.config import NemoConfig
from nemo.engine import NemoEngine
from nemo.events import EventBus
from nemo.ingest.add import add_tpch

if not os.getenv('OPENAI_API_KEY'):
    print('SKIP: no API key')
    exit(0)

with tempfile.TemporaryDirectory() as td:
    store = NemoStore(Path(td) / 'nemo.duckdb')
    store.initialize()
    add_tpch(store, scale=0.01)
    config = NemoConfig(
        max_steps=5, max_runtime_minutes=5,
        arbiter_interval=2,  # consult arbiter more often
        openai_api_key=os.getenv('OPENAI_API_KEY'),
        enable_statistical_analysis=True,
    )
    engine = NemoEngine(store, config, EventBus())
    run_id = asyncio.run(engine.run(max_steps=5))
    insights = store.execute('SELECT COUNT(*) FROM insights').fetchone()[0]
    hypotheses = store.execute('SELECT COUNT(*) FROM hypotheses').fetchone()[0]
    assert insights >= 2, f'Expected >=2 insights, got {insights}'
    print(f'Run complete: {insights} insights, {hypotheses} hypotheses')
    store.close()
print('Phase 4 e2e smoke test PASSED')
"
```

---

### Phase 5: Polish

**Work:**
- TUI output for statistical analysis steps (show test name, p-value, effect size in console)
- Brief generation: include statistical findings with proper formatting in markdown report
- Memory usage monitoring: warn if extraction exceeds 80% of `max_analysis_memory_mb`
- Error recovery: if Python analysis fails, fall back to running the extraction SQL as a plain query
- Logging: analyst agent iterations visible in verbose mode

**Tests:**
- TUI rendering: mock a `STEP_PHASE` event with `phase="analyzing"` and verify status messaging
- Brief generation: insert an insight with statistical test metadata, generate brief, verify stats appear
- Fallback: mock analyst agent failure, verify engine falls back to SQL execution gracefully
- Memory warning: extract a DataFrame near the limit, verify warning is emitted

**Smoke test:**
```bash
# Fast suite should pass (default in CI/dev loops)
.venv/bin/python -m pytest -q

# Optional: full suite including slow tests (see docs/testing.md)
# .venv/bin/python -m pytest -o addopts=""

# End-to-end with the Medicare dataset if available, otherwise TPC-H
# This is the real validation: does the system produce statistical findings?
.venv/bin/python -c "
import asyncio, tempfile, os
from pathlib import Path
from nemo.store import NemoStore
from nemo.config import NemoConfig
from nemo.engine import NemoEngine
from nemo.events import EventBus, EventType, NemoEvent
from nemo.ingest.add import add_tpch

if not os.getenv('OPENAI_API_KEY'):
    print('SKIP: no API key')
    exit(0)

analysis_events = []
def track_analysis(event: NemoEvent):
    if event.type == EventType.STEP_PHASE and event.payload.get('phase') == 'analyzing':
        analysis_events.append(event)

with tempfile.TemporaryDirectory() as td:
    store = NemoStore(Path(td) / 'nemo.duckdb')
    store.initialize()
    add_tpch(store, scale=0.01)
    config = NemoConfig(
        max_steps=8, max_runtime_minutes=8,
        openai_api_key=os.getenv('OPENAI_API_KEY'),
        enable_statistical_analysis=True,
    )
    bus = EventBus()
    bus.subscribe(track_analysis, types=[EventType.STEP_PHASE])
    engine = NemoEngine(store, config, bus)
    run_id = asyncio.run(engine.run(max_steps=8))

    insights = store.execute('SELECT COUNT(*) FROM insights').fetchone()[0]
    print(f'Run complete: {insights} insights, {len(analysis_events)} analysis events')

    # Generate brief and verify it renders
    from nemo.report import generate_brief_markdown
    brief = generate_brief_markdown(store, top_n=10)
    assert '## Top Insights' in brief
    print(f'Brief generated: {len(brief)} chars')
    store.close()
print('Phase 5 final smoke test PASSED')
"
```

## Example Walkthrough

Given a Medicare physician dataset, here's how a statistical analysis step would flow:

**Step 7** (explore phase):
1. **Strategist** sees notebook finding: "Cardiologists bill 40% more than family medicine on average"
2. **Strategist** proposes:
   ```json
   {
     "question": "Is the billing difference between cardiologists and family medicine statistically significant after controlling for patient volume and geographic region?",
     "reasoning": "Prior step showed a large billing gap between specialties, but this could be confounded by practice volume and regional cost differences. Need regression analysis to isolate the specialty effect.",
     "sql": "SELECT \"Total Medicare Payment Amount\", \"Number of Services\", \"Provider Type\", \"NPPES Provider State\" FROM medicare_data WHERE \"Provider Type\" IN ('Cardiology', 'Family Practice') AND \"Total Medicare Payment Amount\" IS NOT NULL",
     "table": "medicare_data",
     "analysis_type": "statistical"
   }
   ```
3. **Engine** routes to analyst agent
4. **Analyst agent** runs:
   - `extract_dataframe(sql=<above>, variable_name="df")` → gets DataFrame, ~30K rows × 4 columns
   - `run_python`:
     ```python
     import statsmodels.formula.api as smf
     import numpy as np

     df['log_payment'] = np.log1p(df['Total Medicare Payment Amount'])
     df['is_cardiology'] = (df['Provider Type'] == 'Cardiology').astype(int)

     model = smf.ols(
         'log_payment ~ is_cardiology + Q("Number of Services") + C(Q("NPPES Provider State"))',
         data=df
     ).fit()

     coef = model.params['is_cardiology']
     pval = model.pvalues['is_cardiology']
     ci = model.conf_int().loc['is_cardiology']
     pct_effect = (np.exp(coef) - 1) * 100

     _result = {
         "coefficient": round(coef, 4),
         "pct_effect": round(pct_effect, 1),
         "p_value": round(pval, 6),
         "ci_lower": round((np.exp(ci[0]) - 1) * 100, 1),
         "ci_upper": round((np.exp(ci[1]) - 1) * 100, 1),
         "r_squared": round(model.rsquared, 4),
         "n_obs": int(model.nobs),
     }
     ```
5. **Analyst returns**:
   ```json
   {
     "title": "Cardiology billing premium persists after controls",
     "claim": "Cardiologists bill 28.3% more than family medicine (p < 0.001, 95% CI: [24.1%, 32.8%]) after controlling for service volume and state, based on OLS regression (n=31,247, R²=0.41).",
     "confidence": 0.85,
     "effect_size": 0.283,
     "statistical_tests": [{
       "test": "OLS regression (log-transformed payment)",
       "statistic": 0.249,
       "p_value": 0.000001,
       "effect_size": 0.283,
       "interpretation": "Specialty coefficient significant; 28.3% premium is both statistically and practically significant"
     }],
     "methodology": "Log-linear OLS with state fixed effects and service volume control. Assumptions checked: residuals approximately normal (Shapiro-Wilk on sample p=0.12), no severe multicollinearity (VIF < 3).",
     "sample_size": 31247
   }
   ```
6. **Interpreter** receives this as structured rows, integrates into notebook

Compare this to the SQL-only version, which would produce: "Cardiologists average $X, family medicine averages $Y, difference is Z%." No controls. No significance test. No confidence interval. No effect size.

## Why Not a Completely Separate Agent?

The user asked whether this should be "the same agent or a different one." Here's why it's integrated rather than standalone:

1. **Shared investigation context**: The notebook tracks what we've learned. A separate agent would need its own notebook or constant context-passing. The strategist already knows "we found specialty differences in step 3" — it should be able to say "now let's test that statistically" without re-discovering the finding.

2. **Unified evidence graph**: A statistical finding that "the difference is significant (p < 0.001)" is evidence that supports or contradicts a hypothesis, just like a SQL finding. It should link into the same graph with the same edge classification.

3. **Arbiter coherence**: The arbiter decides explore vs. exploit based on hypothesis backlog and coverage. Statistical analysis is a *tool* used within both phases, not a third phase. During exploration, you might use stats to validate a surprising pattern before moving on. During exploitation, you use stats to formally test a hypothesis.

4. **Step budget**: Every analysis step should count against the same budget. If stats ran in a separate agent, you'd need separate budgeting and coordination.

The analyst agent is analogous to how a data science team works: the lead analyst (strategist) decides what to investigate, and sometimes hands a sub-task to a specialist (the statistical analyst) who uses different tools but reports findings back to the same investigation log.
