"""Hypothesis validation planner for the exploit phase."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel

from nemo.config import NemoConfig
from nemo.planner.models import HypothesisRecord
from nemo.planner.strategist import Hypothesis, Notebook, format_notebook

VALIDATION_STEPS = ("reproduce", "segment", "confound", "counter", "quantify")

VALIDATOR_SYSTEM = """\
You are a senior analyst validating a previously proposed hypothesis.
Generate one focused SQL investigation step to test the hypothesis.

Rules:
- Use DuckDB-compatible SQL with double-quoted identifiers.
- Ask a materially different validation question each step.
- Keep queries efficient and always include LIMIT 200.
- Prefer subgroup checks, controls, and disconfirming tests over repeats.
- Check sample values in the schema to write correct SQL (values may need casting)."""

VERDICT_SYSTEM = """\
You are a rigorous analyst rendering a final verdict on a hypothesis.
Use the evidence chain only. Be explicit and avoid hand-waving.
Return:
- status: validated, invalidated, inconclusive, or narrowed
- confidence: 0.0 to 1.0
- verdict: concise 1-2 sentence conclusion citing key evidence."""

STEP_GUIDANCE: dict[str, str] = {
    "reproduce": (
        "Re-run the core signal with a robust aggregation. Confirm whether the signal exists at all."
    ),
    "segment": (
        "Test the claim across meaningful subgroups. Identify whether it is broad or concentrated."
    ),
    "confound": (
        "Control for likely confounders and check if the effect persists after adjustment."
    ),
    "counter": (
        "Actively look for disconfirming evidence or counter-examples that could break the claim."
    ),
    "quantify": (
        "Quantify practical significance with effect size framing and clear comparison baselines."
    ),
}


class VerdictResult(BaseModel):
    status: Literal["validated", "invalidated", "inconclusive", "narrowed"]
    confidence: float
    verdict: str


def validation_step_name(hypothesis: HypothesisRecord) -> str:
    idx = min(max(0, int(hypothesis.validation_step)), len(VALIDATION_STEPS) - 1)
    return VALIDATION_STEPS[idx]


async def plan_validation_step(
    hypothesis: HypothesisRecord,
    notebook: Notebook,
    schema_context: str,
    config: NemoConfig,
    client: OpenAI,
) -> Hypothesis:
    """Plan one exploit-phase validation step for a hypothesis under test."""
    step_name = validation_step_name(hypothesis)
    guidance = STEP_GUIDANCE[step_name]
    evidence_lines = _format_evidence(hypothesis)
    user_content = f"""\
## Available Tables
{schema_context}

## Investigation Notebook
{format_notebook(notebook)}

## Hypothesis Under Test
Hypothesis ID: {hypothesis.hypothesis_id}
Claim: {hypothesis.claim}
Current step: {step_name} ({hypothesis.validation_step + 1}/{len(VALIDATION_STEPS)})
Tables involved: {", ".join(hypothesis.tables_involved) if hypothesis.tables_involved else "(unspecified)"}

## Evidence So Far
{evidence_lines}

## Task
Plan exactly one validation query for the {step_name.upper()} step.
Guidance: {guidance}

Return JSON (no markdown):
{{"question": "...", "reasoning": "...", "sql": "...", "table": "..."}}"""

    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                client.responses.parse,
                model=config.plan_model or config.model or "gpt-5-mini",
                instructions=VALIDATOR_SYSTEM,
                input=[{"role": "user", "content": user_content}],
                text_format=Hypothesis,
            )
            if response.output_parsed is not None:
                return response.output_parsed
            raw = _extract_text(response)
            return Hypothesis.model_validate_json(raw)
        except (APIConnectionError, APITimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(2**attempt)
        except RateLimitError:
            if attempt == 2:
                raise
            await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError("plan_validation_step: exhausted retries")


async def render_verdict(
    hypothesis: HypothesisRecord,
    evidence_chain: list[dict[str, Any]],
    config: NemoConfig,
    client: OpenAI,
) -> tuple[str, float, str]:
    """Synthesize a final hypothesis verdict from accumulated evidence."""
    user_content = f"""\
## Hypothesis
ID: {hypothesis.hypothesis_id}
Claim: {hypothesis.claim}
Initial confidence: {hypothesis.initial_confidence:.2f}

## Evidence Chain
{_format_evidence_items(evidence_chain)}

## Task
Render a final verdict for this hypothesis.
Return JSON:
{{"status": "validated|invalidated|inconclusive|narrowed", "confidence": 0.0, "verdict": "..."}}"""
    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                client.responses.parse,
                model=config.model or "gpt-5-mini",
                instructions=VERDICT_SYSTEM,
                input=[{"role": "user", "content": user_content}],
                text_format=VerdictResult,
            )
            parsed = response.output_parsed
            if parsed is not None:
                return parsed.status, max(0.0, min(1.0, parsed.confidence)), parsed.verdict
            raw = _extract_text(response)
            fallback = VerdictResult.model_validate_json(raw)
            return fallback.status, max(0.0, min(1.0, fallback.confidence)), fallback.verdict
        except (APIConnectionError, APITimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(2**attempt)
        except RateLimitError:
            if attempt == 2:
                raise
            await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError("render_verdict: exhausted retries")


def should_render_verdict(
    hypothesis: HypothesisRecord,
    max_validation_steps: int,
    latest_relationship: str | None = None,
) -> bool:
    """Decide if validation should terminate and render a verdict now."""
    if int(hypothesis.validation_step) >= int(max_validation_steps):
        return True
    if not hypothesis.evidence_chain:
        return False

    supports = sum(1 for e in hypothesis.evidence_chain if e.relationship == "supports")
    contradicts = sum(1 for e in hypothesis.evidence_chain if e.relationship == "contradicts")
    narrows = sum(1 for e in hypothesis.evidence_chain if e.relationship == "narrows")
    confounds = sum(1 for e in hypothesis.evidence_chain if e.relationship == "confounds")

    if latest_relationship == "contradicts" and int(hypothesis.validation_step) <= 1:
        return True
    if contradicts >= 2 and supports == 0:
        return True
    if supports >= 3 and contradicts == 0:
        return True
    if confounds >= 1 and supports <= 1:
        return True
    if narrows >= 2 and supports <= 1:
        return True
    return False


def classify_validation_evidence(text: str) -> Literal["supports", "contradicts", "narrows", "confounds"]:
    """Classify one interpretation claim into an evidence relationship label."""
    normalized = (text or "").lower()
    if any(token in normalized for token in ("confound", "after controlling", "alternative explanation")):
        return "confounds"
    if any(token in normalized for token in ("only for", "only in", "subset", "narrow", "specific segment")):
        return "narrows"
    if any(
        token in normalized
        for token in (
            "does not hold",
            "failed to reproduce",
            "not supported",
            "contradict",
            "opposite",
            "no difference",
        )
    ):
        return "contradicts"
    return "supports"


def _format_evidence(hypothesis: HypothesisRecord) -> str:
    if not hypothesis.evidence_chain:
        return "(none yet)"
    lines = [
        f"- [{link.relationship}] {link.note} (insight_id={link.insight_id})"
        for link in hypothesis.evidence_chain[-8:]
    ]
    return "\n".join(lines)


def _format_evidence_items(evidence_chain: list[dict[str, Any]]) -> str:
    if not evidence_chain:
        return "(none)"
    lines: list[str] = []
    for item in evidence_chain:
        rel = str(item.get("relationship", "supports"))
        note = str(item.get("note", ""))
        insight_id = str(item.get("insight_id", ""))
        lines.append(f"- [{rel}] {note} (insight_id={insight_id})")
    return "\n".join(lines)


def _extract_text(response: Any) -> str:
    for item in getattr(response, "output", []):
        if getattr(item, "type", None) == "message":
            for block in getattr(item, "content", []):
                if hasattr(block, "text"):
                    return str(block.text)
    return ""
