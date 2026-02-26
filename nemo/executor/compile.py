"""Compile frontier actions into executable SELECT SQL."""

from __future__ import annotations

from nemo.ingest.joins import JoinCandidate
from nemo.ingest.profile import TableProfile
from nemo.planner.models import FrontierItem


def compile_action(
    item: FrontierItem,
    profiles: list[TableProfile],
    join_candidates: list[JoinCandidate],
    default_limit: int = 200,
) -> str:
    """Convert one frontier action into SQL."""
    payload = dict(item.payload)
    action_type = item.action_type
    if action_type == "SCHEMA_PROFILE":
        sql = compile_schema_profile(payload)
    elif action_type == "METRIC_TREND_SCAN":
        sql = compile_metric_trend(payload)
    elif action_type == "CHANGEPOINT_DETECT":
        sql = compile_changepoint(payload)
    elif action_type == "SEGMENT_COMPARE":
        sql = compile_segment_compare(payload)
    elif action_type == "TOP_GROUPS":
        sql = compile_top_groups(payload)
    elif action_type == "OUTLIER_GROUPS":
        sql = compile_outlier_groups(payload)
    elif action_type == "CORRELATION_SCAN":
        sql = compile_correlation_scan(payload)
    elif action_type == "DATA_QUALITY_CHECK":
        sql = compile_data_quality(payload)
    elif action_type == "COVERAGE_EXPLORER":
        sql = compile_coverage_explorer(payload, profiles)
    elif action_type == "ROBUSTNESS_CHECK":
        sql = compile_robustness_check(payload, str(payload.get("prior_sql") or "SELECT 1 AS ok"))
    elif action_type == "CONTRADICTION_RESOLVE":
        sql = compile_contradiction_resolve(payload)
    else:
        sql = "SELECT 'unsupported action type' AS message"
    with_limit = _ensure_limit(sql, int(payload.get("limit", default_limit)))
    return f"-- action_id: {item.action_id}\n{with_limit}"


def compile_schema_profile(payload: dict) -> str:
    table = _ident(payload.get("table", ""))
    return (
        "SELECT column_name, data_type\n"
        "FROM information_schema.columns\n"
        f"WHERE table_name = '{table.strip(chr(34))}'\n"
        "ORDER BY ordinal_position"
    )


def compile_metric_trend(payload: dict) -> str:
    table = _ident(payload.get("table", ""))
    time_col = _ident(payload.get("time_col", ""))
    metric_col = _ident(payload.get("metric_col", ""))
    return (
        f"SELECT {time_col} AS period,\n"
        f"       AVG({metric_col}) AS metric_avg,\n"
        "       COUNT(*) AS row_count\n"
        f"FROM {table}\n"
        f"WHERE {time_col} IS NOT NULL AND {metric_col} IS NOT NULL\n"
        f"GROUP BY {time_col}\n"
        f"ORDER BY {time_col}"
    )


def compile_changepoint(payload: dict) -> str:
    table = _ident(payload.get("table", ""))
    time_col = _ident(payload.get("time_col", ""))
    metric_col = _ident(payload.get("metric_col", ""))
    return (
        "WITH trend AS (\n"
        f"  SELECT {time_col} AS period,\n"
        f"         AVG({metric_col}) AS metric_avg\n"
        f"  FROM {table}\n"
        f"  WHERE {time_col} IS NOT NULL AND {metric_col} IS NOT NULL\n"
        f"  GROUP BY {time_col}\n"
        ")\n"
        "SELECT period,\n"
        "       metric_avg,\n"
        "       metric_avg - LAG(metric_avg) OVER (ORDER BY period) AS delta\n"
        "FROM trend\n"
        "ORDER BY ABS(delta) DESC NULLS LAST"
    )


def compile_segment_compare(payload: dict) -> str:
    table = _ident(payload.get("table", ""))
    dim = _ident(payload.get("dimension_col", ""))
    metric = _ident(payload.get("metric_col", ""))
    return (
        f"SELECT {dim} AS segment,\n"
        f"       AVG({metric}) AS metric_avg,\n"
        "       COUNT(*) AS row_count\n"
        f"FROM {table}\n"
        f"WHERE {dim} IS NOT NULL AND {metric} IS NOT NULL\n"
        f"GROUP BY {dim}\n"
        "ORDER BY metric_avg DESC"
    )


def compile_top_groups(payload: dict) -> str:
    table = _ident(payload.get("table", ""))
    group_col = _ident(payload.get("group_col", ""))
    metric = _ident(payload.get("metric_col", ""))
    return (
        f"SELECT {group_col} AS grp,\n"
        f"       SUM({metric}) AS metric_total,\n"
        "       COUNT(*) AS row_count\n"
        f"FROM {table}\n"
        f"WHERE {group_col} IS NOT NULL AND {metric} IS NOT NULL\n"
        f"GROUP BY {group_col}\n"
        "ORDER BY metric_total DESC"
    )


def compile_outlier_groups(payload: dict) -> str:
    table = _ident(payload.get("table", ""))
    group_col = _ident(payload.get("group_col", ""))
    metric = _ident(payload.get("metric_col", ""))
    return (
        "WITH grouped AS (\n"
        f"  SELECT {group_col} AS grp,\n"
        f"         AVG({metric}) AS metric_avg\n"
        f"  FROM {table}\n"
        f"  WHERE {group_col} IS NOT NULL AND {metric} IS NOT NULL\n"
        f"  GROUP BY {group_col}\n"
        ")\n"
        "SELECT grp,\n"
        "       metric_avg,\n"
        "       (metric_avg - AVG(metric_avg) OVER ()) / NULLIF(STDDEV_SAMP(metric_avg) OVER (), 0) AS z_score\n"
        "FROM grouped\n"
        "ORDER BY ABS(z_score) DESC NULLS LAST"
    )


def compile_correlation_scan(payload: dict) -> str:
    table = _ident(payload.get("table", ""))
    columns = [c for c in (payload.get("columns") or []) if not _is_likely_key(c)]
    if len(columns) < 2:
        return f"SELECT COUNT(*) AS row_count FROM {table}"
    pairs: list[str] = []
    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            a, b = _ident(columns[i]), _ident(columns[j])
            label = f"corr_{columns[i]}__{columns[j]}"
            pairs.append(f"ROUND(corr({a}, {b}), 4) AS \"{label}\"")
    if len(pairs) > 10:
        pairs = pairs[:10]
    not_null = " AND ".join(f"{_ident(c)} IS NOT NULL" for c in columns)
    select = ",\n       ".join(pairs)
    return (
        f"SELECT {select},\n"
        f"       COUNT(*) AS row_count\n"
        f"FROM {table}\n"
        f"WHERE {not_null}"
    )


def compile_data_quality(payload: dict) -> str:
    table = _ident(payload.get("table", ""))
    key_columns: list[str] = payload.get("key_columns") or []

    parts = ["COUNT(*) AS total_rows"]
    for col_name in key_columns[:5]:
        c = _ident(col_name)
        safe = col_name.replace('"', "")
        parts.append(f"SUM(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END) AS {safe}_null_count")
        parts.append(f"COUNT(DISTINCT {c}) AS {safe}_distinct_count")

    if key_columns:
        pk = _ident(key_columns[0])
        safe_pk = key_columns[0].replace('"', "")
        parts.append(f"COUNT(*) - COUNT(DISTINCT {pk}) AS {safe_pk}_duplicate_count")

    select = ",\n       ".join(parts)
    return f"SELECT {select}\nFROM {table}"


def compile_coverage_explorer(payload: dict, profiles: list[TableProfile]) -> str:
    table_name = str(payload.get("table", ""))
    table = _ident(table_name)
    metric_cols: list[str] = []
    categorical_cols: list[str] = []
    for profile in profiles:
        if profile.name == table_name:
            for col in profile.columns:
                if _is_numeric(col.dtype) and not _is_likely_key(col.name):
                    metric_cols.append(col.name)
                elif not _is_numeric(col.dtype) and col.distinct_count <= 50:
                    categorical_cols.append(col.name)
            break

    parts = ["COUNT(*) AS row_count"]
    for col_name in metric_cols[:3]:
        c = _ident(col_name)
        parts.append(f"AVG({c}) AS avg_{col_name}")
        parts.append(f"STDDEV_SAMP({c}) AS stddev_{col_name}")
        parts.append(f"MIN({c}) AS min_{col_name}")
        parts.append(f"MAX({c}) AS max_{col_name}")
    for col_name in categorical_cols[:2]:
        c = _ident(col_name)
        parts.append(f"COUNT(DISTINCT {c}) AS distinct_{col_name}")

    select = ",\n       ".join(parts)
    return f"SELECT {select}\nFROM {table}"


def compile_robustness_check(payload: dict, prior_sql: str) -> str:
    _ = payload
    cleaned = prior_sql.strip().rstrip(";")
    return f"SELECT * FROM ({cleaned}) AS prior_result"


def compile_contradiction_resolve(payload: dict) -> str:
    thread_id = str(payload.get("thread_id") or "")
    if thread_id:
        return (
            "SELECT insight_id, claim, confidence\n"
            "FROM insights\n"
            f"WHERE thread_id = '{thread_id.replace(chr(39), chr(39) * 2)}'\n"
            "ORDER BY created_at DESC"
        )
    return "SELECT insight_id, claim, confidence FROM insights ORDER BY created_at DESC"


def _ensure_limit(sql: str, limit: int) -> str:
    lowered = sql.lower()
    if " limit " in lowered or lowered.rstrip().endswith(" limit"):
        return sql
    return f"{sql}\nLIMIT {max(1, int(limit))}"


def _ident(value: str) -> str:
    return f'"{str(value).replace(chr(34), chr(34) * 2)}"'


def _is_numeric(dtype: str) -> bool:
    lowered = dtype.lower()
    return any(token in lowered for token in ("int", "decimal", "numeric", "double", "float", "real", "hugeint"))


def _is_likely_key(col_name: str) -> bool:
    """Name-based heuristic: columns that are likely surrogate/primary keys."""
    name = col_name.lower()
    return (
        name.endswith("key")
        or name.endswith("_id")
        or name == "id"
        or name.endswith("_sk")
        or name.endswith("_pk")
    )
