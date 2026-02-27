"""Planner data models."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal
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


class EvidenceLink(BaseModel):
    """Evidence item collected while validating a hypothesis."""

    insight_id: str
    relationship: Literal["supports", "contradicts", "narrows", "confounds"]
    note: str


class EdgeClassification(BaseModel):
    """Structured relationship classification between two insights."""

    to_insight_id: str
    relationship: Literal["supports", "contradicts", "refines", "depends_on", "none"] = "none"
    confidence: float = 0.0
    rationale: str = ""


class RankedCandidate(BaseModel):
    """One ranked candidate entry from frontier re-ranking."""

    rank: int
    action_index: int
    reasoning: str = ""


class RerankedFrontier(BaseModel):
    """Structured LLM output for frontier candidate re-ranking."""

    rankings: list[RankedCandidate] = Field(default_factory=list)


class HypothesisRecord(BaseModel):
    """Backlog record for a testable claim discovered during exploration."""

    hypothesis_id: str = Field(default_factory=lambda: f"hyp_{uuid4().hex[:12]}")
    claim: str
    source_insight_id: str
    initial_confidence: float
    status: Literal["proposed", "testing", "validated", "invalidated", "narrowed", "inconclusive"] = "proposed"
    priority: float = 0.0
    evidence_chain: list[EvidenceLink] = Field(default_factory=list)
    verdict: str | None = None
    verdict_confidence: float | None = None
    validation_step: int = 0
    tables_involved: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @classmethod
    def from_store_row(cls, row: dict) -> "HypothesisRecord":
        """Create a typed hypothesis record from a DB row dict."""
        evidence_raw = row.get("evidence_chain", "[]")
        tables_raw = row.get("tables_involved", "[]")
        evidence_data = json.loads(evidence_raw) if isinstance(evidence_raw, str) and evidence_raw else evidence_raw
        tables_data = json.loads(tables_raw) if isinstance(tables_raw, str) and tables_raw else tables_raw
        return cls(
            hypothesis_id=str(row.get("hypothesis_id") or f"hyp_{uuid4().hex[:12]}"),
            claim=str(row.get("claim", "")),
            source_insight_id=str(row.get("source_insight_id", "")),
            initial_confidence=float(row.get("initial_confidence", 0.0) or 0.0),
            status=str(row.get("status", "proposed")),
            priority=float(row.get("priority", 0.0) or 0.0),
            evidence_chain=[EvidenceLink.model_validate(item) for item in (evidence_data or [])],
            verdict=row.get("verdict"),
            verdict_confidence=row.get("verdict_confidence"),
            validation_step=int(row.get("validation_step", 0) or 0),
            tables_involved=[str(item) for item in (tables_data or [])],
            created_at=row.get("created_at") or datetime.now(tz=timezone.utc),
            updated_at=row.get("updated_at") or datetime.now(tz=timezone.utc),
        )


class PhaseDecision(BaseModel):
    """Outer-loop phase decision for explore/exploit control."""

    phase: Literal["explore", "exploit"]
    hypothesis_id: str | None = None
    reasoning: str
