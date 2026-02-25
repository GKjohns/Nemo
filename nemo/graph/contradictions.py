"""Contradiction cluster detection from graph edges."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from nemo.store import NemoStore


def find_contradiction_clusters(store: NemoStore) -> list[dict[str, Any]]:
    """Group connected contradiction edges into clusters."""
    rows = store.execute(
        """
        SELECT from_insight_id, to_insight_id
        FROM edges
        WHERE type = 'contradicts'
        """
    ).fetchall()
    if not rows:
        return []

    graph: dict[str, set[str]] = defaultdict(set)
    for left, right in rows:
        a = str(left)
        b = str(right)
        graph[a].add(b)
        graph[b].add(a)

    visited: set[str] = set()
    clusters: list[dict[str, Any]] = []
    for node in sorted(graph):
        if node in visited:
            continue
        members = _bfs_component(node, graph, visited)
        claims = _claims_for(store, members)
        clusters.append(
            {
                "insight_ids": members,
                "claims": claims,
                "open_questions": ["What additional slice can explain this contradiction?"],
            }
        )
    clusters.sort(key=lambda item: len(item["insight_ids"]), reverse=True)
    return clusters


def _bfs_component(start: str, graph: dict[str, set[str]], visited: set[str]) -> list[str]:
    q: deque[str] = deque([start])
    members: list[str] = []
    visited.add(start)
    while q:
        cur = q.popleft()
        members.append(cur)
        for nxt in graph[cur]:
            if nxt not in visited:
                visited.add(nxt)
                q.append(nxt)
    members.sort()
    return members


def _claims_for(store: NemoStore, insight_ids: list[str]) -> list[str]:
    if not insight_ids:
        return []
    quoted = ", ".join(f"'{insight_id}'" for insight_id in insight_ids)
    rows = store.execute(
        f"""
        SELECT claim
        FROM insights
        WHERE insight_id IN ({quoted})
        ORDER BY created_at DESC
        """
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0] is not None]
