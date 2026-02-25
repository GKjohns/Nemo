from __future__ import annotations

from nemo.ingest.joins import discover_joins
from nemo.ingest.profile import profile_table


def test_join_discovery_finds_customer_orders_key(store):
    store.execute(
        """
        CREATE TABLE customer (
            c_custkey INTEGER,
            name VARCHAR
        )
        """
    )
    store.execute(
        """
        INSERT INTO customer VALUES
        (1, 'Acme'),
        (2, 'Globex'),
        (3, 'Initech')
        """
    )
    store.execute(
        """
        CREATE TABLE orders (
            order_id INTEGER,
            o_custkey INTEGER,
            total DOUBLE
        )
        """
    )
    store.execute(
        """
        INSERT INTO orders VALUES
        (10, 1, 100.0),
        (11, 1, 50.0),
        (12, 2, 80.0),
        (13, 3, 20.0)
        """
    )

    customer_profile = profile_table(store, "customer")
    orders_profile = profile_table(store, "orders")
    candidates = discover_joins(store, [customer_profile, orders_profile])

    assert candidates
    assert any(
        {
            candidate.column_a,
            candidate.column_b,
        }
        == {"o_custkey", "c_custkey"}
        for candidate in candidates
    )
