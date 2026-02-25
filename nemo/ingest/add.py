"""Dataset ingestion helpers for Nemo."""

from __future__ import annotations

import re
from glob import glob
from pathlib import Path

from nemo.store import NemoStore


def add_file(store: NemoStore, path: Path, name: str, format: str = "auto") -> str:
    """
    Load a CSV/Parquet file into DuckDB as a table and register metadata.

    Returns the created dataset_id.
    """
    source_path = path.expanduser().resolve()
    if not source_path.exists():
        raise ValueError(f"file not found: {source_path}")
    if not source_path.is_file():
        raise ValueError(f"path is not a file: {source_path}")

    fmt = _detect_format(source_path.suffix, format)
    table_name = _sanitize_identifier(name)
    source_literal = str(source_path).replace("'", "''")

    if fmt == "csv":
        sql = f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_csv_auto('{source_literal}')"
    elif fmt == "parquet":
        sql = f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_parquet('{source_literal}')"
    else:
        raise ValueError(f"unsupported format: {fmt}")

    store.execute(sql)
    return store.insert_dataset(name=table_name, source_uri=str(source_path), fmt=fmt)


def add_tpch(store: NemoStore, scale: float = 1.0) -> list[str]:
    """
    Generate TPC-H tables in DuckDB and register them in datasets.

    Returns dataset_ids for created TPC-H tables.
    """
    if scale <= 0:
        raise ValueError("scale must be > 0")

    try:
        store.execute("INSTALL tpch")
    except Exception:  # noqa: BLE001
        # DuckDB may already have the extension available.
        pass
    store.execute("LOAD tpch")
    store.execute(f"CALL dbgen(sf={float(scale)})")

    tpch_tables = [
        "customer",
        "lineitem",
        "nation",
        "orders",
        "part",
        "partsupp",
        "region",
        "supplier",
    ]
    dataset_ids: list[str] = []
    for table in tpch_tables:
        if store.table_exists(table):
            if _dataset_already_registered(store, table, "tpch"):
                continue
            dataset_ids.append(
                store.insert_dataset(
                    name=table,
                    source_uri=f"tpch://sf={scale}/{table}",
                    fmt="tpch",
                )
            )
    return dataset_ids


def add_glob(store: NemoStore, pattern: str, name: str, format: str = "csv") -> str:
    """
    Load files matching a glob pattern into one table and register metadata.

    Returns the created dataset_id.
    """
    if not glob(pattern) and "://" not in pattern:
        raise ValueError(f"glob did not match any files: {pattern}")

    fmt = _detect_format(Path(pattern).suffix, format)
    table_name = _sanitize_identifier(name)
    pattern_literal = pattern.replace("'", "''")

    if fmt == "csv":
        sql = f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_csv_auto('{pattern_literal}')"
    elif fmt == "parquet":
        sql = f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_parquet('{pattern_literal}')"
    else:
        raise ValueError(f"unsupported format: {fmt}")

    store.execute(sql)
    return store.insert_dataset(name=table_name, source_uri=pattern, fmt=fmt)


def _detect_format(suffix: str, requested: str) -> str:
    req = requested.strip().lower()
    if req in {"csv", "parquet"}:
        return req
    if req != "auto":
        raise ValueError(f"unknown format: {requested}")

    ext = suffix.lower()
    if ext == ".csv":
        return "csv"
    if ext in {".parquet", ".pq"}:
        return "parquet"
    raise ValueError("could not infer format; pass --format csv|parquet")


def _sanitize_identifier(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip())
    clean = clean.strip("_")
    if not clean:
        raise ValueError("table name cannot be empty")
    if clean[0].isdigit():
        clean = f"t_{clean}"
    return clean.lower()


def _dataset_already_registered(store: NemoStore, name: str, fmt: str) -> bool:
    row = store.execute(
        "SELECT COUNT(*) FROM datasets WHERE name = ? AND format = ?",
        [name, fmt],
    ).fetchone()
    return bool(row and int(row[0]) > 0)
