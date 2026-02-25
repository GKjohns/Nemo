from __future__ import annotations

import duckdb
import pytest

from nemo.ingest.add import add_file, add_glob, add_tpch
from nemo.ingest.profile import profile_table


def test_add_csv_and_profile(store, project_dir):
    csv_path = project_dir / "orders.csv"
    csv_path.write_text("order_id,amount,segment\n1,10.0,enterprise\n2,20.0,smb\n3,,smb\n", encoding="utf-8")

    dataset_id = add_file(store, path=csv_path, name="orders")

    assert dataset_id.startswith("dataset_")
    assert store.table_exists("orders")
    assert store.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3

    profile = profile_table(store, "orders")
    amount = next(col for col in profile.columns if col.name == "amount")
    assert profile.row_count == 3
    assert amount.null_count == 1
    assert amount.distinct_count == 2
    assert amount.max_val == 20.0


def test_add_file_missing_path_raises(store, project_dir):
    missing = project_dir / "missing.csv"
    with pytest.raises(ValueError, match="file not found"):
        add_file(store, path=missing, name="missing")


def test_add_parquet_glob(store, project_dir):
    conn = duckdb.connect(str(project_dir / "prep.duckdb"))
    conn.execute("CREATE TABLE p1 AS SELECT 1 AS id, 'a' AS value UNION ALL SELECT 2, 'b'")
    conn.execute("CREATE TABLE p2 AS SELECT 3 AS id, 'c' AS value")
    conn.execute(f"COPY p1 TO '{(project_dir / 'part1.parquet').as_posix()}' (FORMAT PARQUET)")
    conn.execute(f"COPY p2 TO '{(project_dir / 'part2.parquet').as_posix()}' (FORMAT PARQUET)")
    conn.close()

    dataset_id = add_glob(
        store,
        pattern=(project_dir / "*.parquet").as_posix(),
        name="events",
        format="parquet",
    )

    assert dataset_id.startswith("dataset_")
    assert store.table_exists("events")
    assert store.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3


def test_add_tpch_registers_tables(store):
    try:
        dataset_ids = add_tpch(store, scale=0.01)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"tpch extension unavailable: {exc}")

    assert dataset_ids
    assert store.table_exists("orders")
    assert store.table_exists("customer")


def test_add_tpch_is_idempotent_for_dataset_registry(store):
    try:
        add_tpch(store, scale=0.01)
        second_ids = add_tpch(store, scale=0.01)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"tpch extension unavailable: {exc}")

    # Subsequent runs should not duplicate dataset rows.
    assert second_ids == []
