"""Deterministic frontier action generators."""

from __future__ import annotations

from collections import Counter, namedtuple

from nemo.ingest.profile import ColumnProfile, TableProfile
from nemo.planner.models import FrontierItem

GeneratorContext = namedtuple(
    "GeneratorContext",
    ["store", "profiles", "recent_insights", "join_candidates", "config"],
)


def gen_schema_profile(ctx: GeneratorContext) -> list[FrontierItem]:
    """SCHEMA_PROFILE - profile tables/columns not yet covered by insights."""
    items: list[FrontierItem] = []
    existing = _recent_dedupe_keys(ctx.recent_insights)
    for profile in sorted(ctx.profiles, key=lambda p: p.name):
        dedupe_key = f"schema_profile:{profile.name}"
        if dedupe_key in existing:
            continue
        items.append(
            FrontierItem(
                action_type="SCHEMA_PROFILE",
                payload={"table": profile.name},
                dedupe_key=dedupe_key,
                rationale=f"Profile baseline schema and stats for {profile.name}",
            )
        )
    return items


def gen_metric_trend(ctx: GeneratorContext) -> list[FrontierItem]:
    """METRIC_TREND_SCAN - time-series trend analysis for numeric metrics."""
    items: list[FrontierItem] = []
    for profile in sorted(ctx.profiles, key=lambda p: p.name):
        time_cols = _time_columns(profile, set(ctx.config.time_columns))
        metric_cols = _metric_columns(profile, set(ctx.config.key_metrics) | set(ctx.config.key_metrics.values()))
        for time_col in time_cols:
            for metric_col in metric_cols:
                dedupe_key = f"metric_trend:{profile.name}.{time_col.name}:{metric_col.name}"
                items.append(
                    FrontierItem(
                        action_type="METRIC_TREND_SCAN",
                        payload={"table": profile.name, "time_col": time_col.name, "metric_col": metric_col.name},
                        dedupe_key=dedupe_key,
                        rationale=f"Scan trend of {metric_col.name} over {time_col.name} in {profile.name}",
                    )
                )
    return items


def gen_changepoint(ctx: GeneratorContext) -> list[FrontierItem]:
    """CHANGEPOINT_DETECT - detect sudden metric shifts over time windows."""
    items: list[FrontierItem] = []
    for profile in sorted(ctx.profiles, key=lambda p: p.name):
        for time_col in _time_columns(profile, set(ctx.config.time_columns)):
            for metric_col in _numeric_columns(profile):
                dedupe_key = f"changepoint:{profile.name}.{time_col.name}:{metric_col.name}"
                items.append(
                    FrontierItem(
                        action_type="CHANGEPOINT_DETECT",
                        payload={
                            "table": profile.name,
                            "time_col": time_col.name,
                            "metric_col": metric_col.name,
                            "window": "auto",
                        },
                        dedupe_key=dedupe_key,
                        rationale=f"Detect abrupt changes in {metric_col.name} over {time_col.name}",
                    )
                )
    return items


def gen_segment_compare(ctx: GeneratorContext) -> list[FrontierItem]:
    """SEGMENT_COMPARE - compare a metric across top categorical values."""
    items: list[FrontierItem] = []
    for profile in sorted(ctx.profiles, key=lambda p: p.name):
        dims = _categorical_columns(profile)
        metrics = _numeric_columns(profile)
        for dim in dims:
            for metric in metrics:
                dedupe_key = f"segment_compare:{profile.name}.{dim.name}:{metric.name}"
                items.append(
                    FrontierItem(
                        action_type="SEGMENT_COMPARE",
                        payload={"table": profile.name, "dimension_col": dim.name, "metric_col": metric.name},
                        dedupe_key=dedupe_key,
                        rationale=f"Compare {metric.name} across {dim.name} segments in {profile.name}",
                    )
                )
    return items


def gen_top_groups(ctx: GeneratorContext) -> list[FrontierItem]:
    """TOP_GROUPS - find strongest/weakest groups by metric and dimension."""
    items: list[FrontierItem] = []
    for profile in sorted(ctx.profiles, key=lambda p: p.name):
        for dim in _categorical_columns(profile):
            for metric in _numeric_columns(profile):
                dedupe_key = f"top_groups:{profile.name}.{dim.name}:{metric.name}"
                items.append(
                    FrontierItem(
                        action_type="TOP_GROUPS",
                        payload={"table": profile.name, "group_col": dim.name, "metric_col": metric.name, "k": 10},
                        dedupe_key=dedupe_key,
                        rationale=f"Rank top/bottom {dim.name} groups for {metric.name}",
                    )
                )
    return items


def gen_outlier_groups(ctx: GeneratorContext) -> list[FrontierItem]:
    """OUTLIER_GROUPS - detect unusual group-level metric behavior."""
    items: list[FrontierItem] = []
    for profile in sorted(ctx.profiles, key=lambda p: p.name):
        for dim in _categorical_columns(profile):
            for metric in _numeric_columns(profile):
                dedupe_key = f"outlier_groups:{profile.name}.{dim.name}:{metric.name}"
                items.append(
                    FrontierItem(
                        action_type="OUTLIER_GROUPS",
                        payload={"table": profile.name, "group_col": dim.name, "metric_col": metric.name},
                        dedupe_key=dedupe_key,
                        rationale=f"Find outlier {dim.name} groups for {metric.name}",
                    )
                )
    return items


def gen_correlation_scan(ctx: GeneratorContext) -> list[FrontierItem]:
    """CORRELATION_SCAN - pairwise numeric correlation scan."""
    items: list[FrontierItem] = []
    for profile in sorted(ctx.profiles, key=lambda p: p.name):
        numeric = _numeric_columns(profile)
        if len(numeric) < 2:
            continue
        dedupe_key = f"correlation_scan:{profile.name}"
        items.append(
            FrontierItem(
                action_type="CORRELATION_SCAN",
                payload={"table": profile.name, "columns": [col.name for col in numeric]},
                dedupe_key=dedupe_key,
                rationale=f"Scan pairwise correlations in {profile.name}",
            )
        )
    return items


def gen_data_quality(ctx: GeneratorContext) -> list[FrontierItem]:
    """DATA_QUALITY_CHECK - null spikes, duplicates, and join-key integrity."""
    items: list[FrontierItem] = []
    for profile in sorted(ctx.profiles, key=lambda p: p.name):
        dedupe_key = f"data_quality:{profile.name}"
        key_cols = [col.name for col in profile.columns if col.name.endswith("_id") or "key" in col.name.lower()]
        items.append(
            FrontierItem(
                action_type="DATA_QUALITY_CHECK",
                payload={"table": profile.name, "key_columns": key_cols},
                dedupe_key=dedupe_key,
                rationale=f"Check data quality anomalies for {profile.name}",
            )
        )
    return items


def gen_coverage_explorer(ctx: GeneratorContext) -> list[FrontierItem]:
    """COVERAGE_EXPLORER - boost coverage for under-explored tables."""
    items: list[FrontierItem] = []
    table_hits = Counter(_table_from_insight(insight) for insight in ctx.recent_insights if _table_from_insight(insight))
    for profile in sorted(ctx.profiles, key=lambda p: (table_hits.get(p.name, 0), p.name)):
        dedupe_key = f"coverage_explorer:{profile.name}"
        items.append(
            FrontierItem(
                action_type="COVERAGE_EXPLORER",
                payload={"table": profile.name, "observed_insights": int(table_hits.get(profile.name, 0))},
                dedupe_key=dedupe_key,
                rationale=f"Increase coverage for less-explored table {profile.name}",
            )
        )
    return items


def gen_robustness_check(ctx: GeneratorContext) -> list[FrontierItem]:
    """ROBUSTNESS_CHECK - validate prior claims with alternate slices."""
    items: list[FrontierItem] = []
    for insight in sorted(ctx.recent_insights, key=lambda row: str(row.get("insight_id", ""))):
        insight_id = str(insight.get("insight_id", ""))
        if not insight_id:
            continue
        dedupe_key = f"robustness:{insight_id}"
        items.append(
            FrontierItem(
                action_type="ROBUSTNESS_CHECK",
                payload={"insight_id": insight_id, "thread_id": insight.get("thread_id")},
                dedupe_key=dedupe_key,
                rationale=f"Cross-check prior claim from insight {insight_id}",
            )
        )
    return items


def gen_contradiction_resolve(ctx: GeneratorContext) -> list[FrontierItem]:
    """CONTRADICTION_RESOLVE - propose tests for conflicting insight threads."""
    items: list[FrontierItem] = []
    by_thread: dict[str, int] = Counter(
        str(insight.get("thread_id"))
        for insight in ctx.recent_insights
        if insight.get("thread_id") is not None and str(insight.get("thread_id")).strip()
    )
    for thread_id in sorted(thread for thread, count in by_thread.items() if count > 1):
        dedupe_key = f"contradiction_resolve:{thread_id}"
        items.append(
            FrontierItem(
                action_type="CONTRADICTION_RESOLVE",
                payload={"thread_id": thread_id, "insight_count": by_thread[thread_id]},
                dedupe_key=dedupe_key,
                thread_id=thread_id,
                rationale=f"Resolve potentially conflicting insights in thread {thread_id}",
            )
        )
    return items


ALL_GENERATORS = [
    gen_schema_profile,
    gen_metric_trend,
    gen_changepoint,
    gen_segment_compare,
    gen_top_groups,
    gen_outlier_groups,
    gen_correlation_scan,
    gen_data_quality,
    gen_coverage_explorer,
    gen_robustness_check,
    gen_contradiction_resolve,
]


def run_generators(ctx: GeneratorContext) -> list[FrontierItem]:
    """Run all generators and return combined frontier items."""
    items: list[FrontierItem] = []
    for generator in ALL_GENERATORS:
        items.extend(generator(ctx))
    return items


def _numeric_columns(profile: TableProfile) -> list[ColumnProfile]:
    return [col for col in profile.columns if _is_numeric(col)]


def _time_columns(profile: TableProfile, configured_time_columns: set[str]) -> list[ColumnProfile]:
    configured = {name.lower() for name in configured_time_columns}
    return [
        col
        for col in profile.columns
        if _is_temporal(col) or col.name.lower() in configured or f"{profile.name}.{col.name}".lower() in configured
    ]


def _metric_columns(profile: TableProfile, configured_metric_names: set[str]) -> list[ColumnProfile]:
    configured = {name.lower() for name in configured_metric_names}
    numeric = _numeric_columns(profile)
    preferred = [
        col
        for col in numeric
        if col.name.lower() in configured or f"{profile.name}.{col.name}".lower() in configured
    ]
    return preferred or numeric


def _categorical_columns(profile: TableProfile) -> list[ColumnProfile]:
    return [
        col
        for col in profile.columns
        if not _is_numeric(col)
        and not _is_temporal(col)
        and (col.distinct_count <= 50 or col.cardinality_ratio <= 0.2)
    ]


def _is_numeric(col: ColumnProfile) -> bool:
    dtype = col.dtype.lower()
    return any(token in dtype for token in ("int", "decimal", "numeric", "double", "float", "real", "hugeint"))


def _is_temporal(col: ColumnProfile) -> bool:
    dtype = col.dtype.lower()
    return "date" in dtype or "time" in dtype


def _table_from_insight(insight: dict) -> str | None:
    sources = insight.get("source_tables_json")
    if isinstance(sources, str) and sources:
        return sources.split(",")[0].strip()
    question = str(insight.get("question") or "")
    for token in question.replace(",", " ").split():
        if "." in token:
            return token.split(".", 1)[0]
    return None


def _recent_dedupe_keys(recent_insights: list[dict]) -> set[str]:
    keys: set[str] = set()
    for insight in recent_insights:
        value = insight.get("dedupe_key")
        if isinstance(value, str) and value:
            keys.add(value)
    return keys
