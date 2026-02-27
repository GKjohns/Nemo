"""Schema and table profiling utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nemo.store import NemoStore


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    nullable: bool
    null_count: int
    null_pct: float
    distinct_count: int
    cardinality_ratio: float
    sample_values: list[Any]
    min_val: Any | None
    max_val: Any | None
    mean: float | None
    stddev: float | None
    p25: float | None
    p50: float | None
    p75: float | None


@dataclass
class TableProfile:
    name: str
    row_count: int
    columns: list[ColumnProfile]


def profile_table(store: NemoStore, table_name: str) -> TableProfile:
    """Build a table profile with per-column summary statistics."""
    if not store.table_exists(table_name):
        raise ValueError(f"table not found: {table_name}")

    safe_table = _quote_ident(table_name)
    row_count = int(store.execute(f"SELECT COUNT(*) FROM {safe_table}").fetchone()[0])
    column_rows = store.execute(f"PRAGMA table_info({safe_table})").fetchall()

    columns: list[ColumnProfile] = []
    for row in column_rows:
        col_name = str(row[1])
        col_type = str(row[2])
        nullable = not bool(row[3])
        safe_col = _quote_ident(col_name)

        null_count = int(
            store.execute(f"SELECT COUNT(*) FROM {safe_table} WHERE {safe_col} IS NULL").fetchone()[0]
        )
        distinct_count = int(
            store.execute(
                f"SELECT COUNT(DISTINCT {safe_col}) FROM {safe_table} WHERE {safe_col} IS NOT NULL"
            ).fetchone()[0]
        )
        cardinality_ratio = float(distinct_count / row_count) if row_count > 0 else 0.0
        null_pct = float(null_count / row_count) if row_count > 0 else 0.0

        sample_rows = store.execute(
            f"""
            SELECT DISTINCT {safe_col}
            FROM {safe_table}
            WHERE {safe_col} IS NOT NULL
            LIMIT 8
            """
        ).fetchall()
        sample_values = [sample[0] for sample in sample_rows]

        min_val: Any | None = None
        max_val: Any | None = None
        mean: float | None = None
        stddev: float | None = None
        p25: float | None = None
        p50: float | None = None
        p75: float | None = None

        if _is_numeric(col_type):
            stats_row = store.execute(
                f"""
                SELECT
                    MIN({safe_col}),
                    MAX({safe_col}),
                    AVG({safe_col}),
                    STDDEV_SAMP({safe_col}),
                    QUANTILE_CONT({safe_col}, 0.25),
                    QUANTILE_CONT({safe_col}, 0.50),
                    QUANTILE_CONT({safe_col}, 0.75)
                FROM {safe_table}
                WHERE {safe_col} IS NOT NULL
                """
            ).fetchone()
            min_val, max_val, mean, stddev, p25, p50, p75 = stats_row
            mean = float(mean) if mean is not None else None
            stddev = float(stddev) if stddev is not None else None
            p25 = float(p25) if p25 is not None else None
            p50 = float(p50) if p50 is not None else None
            p75 = float(p75) if p75 is not None else None
        elif _is_orderable_non_numeric(col_type):
            min_val, max_val = store.execute(
                f"""
                SELECT MIN({safe_col}), MAX({safe_col})
                FROM {safe_table}
                WHERE {safe_col} IS NOT NULL
                """
            ).fetchone()

        columns.append(
            ColumnProfile(
                name=col_name,
                dtype=col_type,
                nullable=nullable,
                null_count=null_count,
                null_pct=null_pct,
                distinct_count=distinct_count,
                cardinality_ratio=cardinality_ratio,
                sample_values=sample_values,
                min_val=min_val,
                max_val=max_val,
                mean=mean,
                stddev=stddev,
                p25=p25,
                p50=p50,
                p75=p75,
            )
        )

    return TableProfile(name=table_name, row_count=row_count, columns=columns)


def profile_all(store: NemoStore) -> list[TableProfile]:
    """Profile all datasets currently registered in the store."""
    profiles: list[TableProfile] = []
    for dataset in store.get_datasets():
        table_name = str(dataset["name"])
        if store.table_exists(table_name):
            profiles.append(profile_table(store, table_name))
    return profiles


def _is_numeric(dtype: str) -> bool:
    value = dtype.lower()
    return any(
        token in value
        for token in ["int", "decimal", "numeric", "double", "real", "float", "hugeint", "smallint", "bigint"]
    )


def _is_orderable_non_numeric(dtype: str) -> bool:
    value = dtype.lower()
    return "date" in value or "time" in value


def _quote_ident(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'
