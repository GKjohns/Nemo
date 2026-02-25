"""Planner data models."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


class FrontierItem(BaseModel):
    """Normalized action item persisted in the frontier queue."""

    action_id: str = Field(default_factory=lambda: f"action_{uuid4().hex[:12]}")
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    run_id: str | None = None
    action_type: str
    payload: dict
    score: float = 0.0
    status: str = "queued"
    last_error: str | None = None
    thread_id: str | None = None
    depends_on_action_id: str | None = None
    dedupe_key: str
    rationale: str = ""

    @classmethod
    def from_store_row(cls, row: dict) -> "FrontierItem":
        """Create a typed frontier item from a DB row dict."""
        payload_raw = row.get("payload_json", row.get("payload", {}))
        if isinstance(payload_raw, str):
            payload = json.loads(payload_raw) if payload_raw.strip() else {}
        elif isinstance(payload_raw, dict):
            payload = payload_raw
        else:
            payload = {}

        created_at = row.get("created_at")
        if isinstance(created_at, datetime):
            created = created_at
        elif isinstance(created_at, str):
            created = datetime.fromisoformat(created_at)
        else:
            created = datetime.now(tz=timezone.utc)

        return cls(
            action_id=str(row.get("action_id") or f"action_{uuid4().hex[:12]}"),
            created_at=created,
            run_id=row.get("run_id"),
            action_type=str(row.get("action_type", "")),
            payload=payload,
            score=float(row.get("score", 0.0) or 0.0),
            status=str(row.get("status", "queued")),
            last_error=row.get("last_error"),
            thread_id=row.get("thread_id"),
            depends_on_action_id=row.get("depends_on_action_id"),
            dedupe_key=str(row.get("dedupe_key", "")),
            rationale=str(row.get("rationale", "")),
        )

    def to_store_payload(self) -> dict[str, str | float | None]:
        """Return DB-compatible payload fields for frontier table insertion."""
        return {
            "action_id": self.action_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "action_type": self.action_type,
            "payload_json": json.dumps(self.payload),
            "score": float(self.score),
            "status": self.status,
            "last_error": self.last_error,
            "depends_on_action_id": self.depends_on_action_id,
            "dedupe_key": self.dedupe_key,
        }
