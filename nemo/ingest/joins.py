"""Join candidate discovery across profiled tables."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from nemo.ingest.profile import TableProfile
from nemo.store import NemoStore


@dataclass
class JoinCandidate:
    table_a: str
    column_a: str
    table_b: str
    column_b: str
    confidence: float
    uniqueness_a: float
    uniqueness_b: float
    overlap_ratio: float
    rationale: str


def discover_joins(store: NemoStore, profiles: list[TableProfile]) -> list[JoinCandidate]:
    """Discover likely join keys across all table pairs."""
    candidates: list[JoinCandidate] = []

    for left, right in combinations(profiles, 2):
        for col_a in left.columns:
            for col_b in right.columns:
                if not _types_compatible(col_a.dtype, col_b.dtype):
                    continue
                if col_a.null_pct > 0.50 or col_b.null_pct > 0.50:
                    continue

                name_score = _name_match_score(col_a.name, col_b.name)
                if name_score <= 0:
                    continue

                overlap_ratio = _overlap_ratio(store, left.name, col_a.name, right.name, col_b.name)
                uniqueness_a = col_a.cardinality_ratio
                uniqueness_b = col_b.cardinality_ratio
                uniqueness_score = max(uniqueness_a, uniqueness_b)

                confidence = min(1.0, (0.40 * name_score) + (0.35 * overlap_ratio) + (0.25 * uniqueness_score))
                if confidence < 0.30:
                    continue

                rationale = (
                    f"name_match={name_score:.2f}, overlap={overlap_ratio:.2f}, "
                    f"uniqueness=({uniqueness_a:.2f},{uniqueness_b:.2f})"
                )
                candidates.append(
                    JoinCandidate(
                        table_a=left.name,
                        column_a=col_a.name,
                        table_b=right.name,
                        column_b=col_b.name,
                        confidence=confidence,
                        uniqueness_a=uniqueness_a,
                        uniqueness_b=uniqueness_b,
                        overlap_ratio=overlap_ratio,
                        rationale=rationale,
                    )
                )

    return sorted(candidates, key=lambda c: c.confidence, reverse=True)


def _overlap_ratio(store: NemoStore, table_a: str, col_a: str, table_b: str, col_b: str) -> float:
    sample_a = _sample_distinct(store, table_a, col_a)
    sample_b = _sample_distinct(store, table_b, col_b)

    if not sample_a or not sample_b:
        return 0.0

    union = sample_a | sample_b
    if not union:
        return 0.0
    intersection = sample_a & sample_b
    return len(intersection) / len(union)


def _sample_distinct(store: NemoStore, table: str, column: str, limit: int = 1000) -> set[str]:
    safe_table = _quote_ident(table)
    safe_col = _quote_ident(column)
    rows = store.execute(
        f"""
        SELECT DISTINCT CAST({safe_col} AS VARCHAR)
        FROM {safe_table}
        WHERE {safe_col} IS NOT NULL
        LIMIT {int(limit)}
        """
    ).fetchall()
    return {str(row[0]) for row in rows if row and row[0] is not None}


def _types_compatible(dtype_a: str, dtype_b: str) -> bool:
    kind_a = _type_group(dtype_a)
    kind_b = _type_group(dtype_b)
    if kind_a == "other" or kind_b == "other":
        return False
    return kind_a == kind_b


def _type_group(dtype: str) -> str:
    value = dtype.lower()
    if any(token in value for token in ("int", "decimal", "numeric", "double", "real", "float", "hugeint")):
        return "numeric"
    if "char" in value or "text" in value or "varchar" in value or "string" in value:
        return "string"
    if "date" in value or "time" in value:
        return "temporal"
    if "bool" in value:
        return "bool"
    return "other"


def _name_match_score(name_a: str, name_b: str) -> float:
    a = name_a.lower().strip()
    b = name_b.lower().strip()
    if a == b:
        if "id" in a:
            return 1.0
        return 0.7

    if a.endswith("_id") and b.endswith("_id"):
        if a.split("_")[-2:] == b.split("_")[-2:]:
            return 0.9
        return 0.6

    if _is_key_name(a) and _is_key_name(b) and (_strip_prefix(a) == _strip_prefix(b)):
        return 0.8
    return 0.0


def _strip_prefix(name: str) -> str:
    if "_" not in name:
        return name
    return name.split("_", 1)[1]


def _is_key_name(name: str) -> bool:
    return "id" in name or name.endswith("key")


def _quote_ident(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'
