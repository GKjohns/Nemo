"""Canonicalization helpers for claims and hypotheses."""

from __future__ import annotations

import re
import time
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel
from nemo.config import NemoConfig


class ClaimExtraction(BaseModel):
    metric: str
    direction: str
    population: str
    segment: str | None
    time_range: str | None
    magnitude: float | None
    comparison_base: str | None


class HypothesisExtraction(BaseModel):
    metric: str
    population: str
    question: str


def canonicalize_claim(claim_text: str, config: NemoConfig, client: OpenAI | None = None) -> dict[str, Any]:
    """Extract structured claim fields, using LLM when available."""
    if client is not None:
        try:
            return _canonicalize_claim_llm(claim_text, client)
        except Exception:  # noqa: BLE001
            pass
    _ = config
    text = claim_text.strip()
    lowered = text.lower()
    direction = "different"
    if any(token in lowered for token in ("increase", "higher", "up", "grew")):
        direction = "higher"
    elif any(token in lowered for token in ("decrease", "lower", "down", "fell")):
        direction = "lower"
    elif any(token in lowered for token in ("same", "flat", "unchanged", "no change")):
        direction = "no_change"

    metric = _first_match(r"(revenue|amount|count|orders|sales|avg_[a-z_]+)", lowered) or "unknown_metric"
    population = _first_match(r"(in|for)\s+([a-z0-9_ ]+)", lowered, group=2) or "all rows"
    magnitude = _parse_percent_or_number(lowered)
    return {
        "metric": metric,
        "direction": direction,
        "population": population.strip(),
        "segment": None,
        "time_range": None,
        "magnitude": magnitude,
        "comparison_base": None,
    }


def canonicalize_hypothesis(question: str, config: NemoConfig, client: OpenAI | None = None) -> dict[str, Any]:
    """Extract structured hypothesis fields, using LLM when available."""
    if client is not None:
        try:
            return _canonicalize_hypothesis_llm(question, client)
        except Exception:  # noqa: BLE001
            pass
    _ = config
    text = question.strip()
    lowered = text.lower()
    metric = _first_match(r"(revenue|amount|count|orders|sales|avg_[a-z_]+)", lowered) or "unknown_metric"
    return {
        "metric": metric,
        "population": "all rows",
        "question": text,
    }


def _first_match(pattern: str, text: str, group: int = 1) -> str | None:
    match = re.search(pattern, text)
    if not match:
        return None
    value = match.group(group)
    return value if isinstance(value, str) and value.strip() else None


def _parse_percent_or_number(text: str) -> float | None:
    pct = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text)
    if pct:
        return float(pct.group(1))
    num = re.search(r"\b(-?\d+(?:\.\d+)?)\b", text)
    if num:
        return float(num.group(1))
    return None


def _canonicalize_claim_llm(claim_text: str, client: OpenAI) -> dict[str, Any]:
    instructions = (
        "Extract a structured analytical claim. "
        "Use direction values only: higher, lower, no_change, different. "
        "If information is missing, set nullable fields to null."
    )
    for attempt in range(3):
        try:
            response = client.responses.parse(
                model="gpt-5-nano",
                instructions=instructions,
                input=claim_text,
                text_format=ClaimExtraction,
            )
            if getattr(response, "refusal", None):
                break
            parsed = response.output_parsed
            if parsed is None:
                break
            return parsed.model_dump()
        except (APIConnectionError, APITimeoutError):
            if attempt == 2:
                break
            time.sleep(2**attempt)
        except RateLimitError:
            if attempt == 2:
                break
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("claim canonicalization failed")


def _canonicalize_hypothesis_llm(question: str, client: OpenAI) -> dict[str, Any]:
    instructions = (
        "Extract a structured hypothesis from an analytics question. "
        "Return concise metric and population fields. "
        "If uncertain, use 'unknown_metric' and 'all rows'."
    )
    for attempt in range(3):
        try:
            response = client.responses.parse(
                model="gpt-5-nano",
                instructions=instructions,
                input=question,
                text_format=HypothesisExtraction,
            )
            if getattr(response, "refusal", None):
                break
            parsed = response.output_parsed
            if parsed is None:
                break
            return parsed.model_dump()
        except (APIConnectionError, APITimeoutError):
            if attempt == 2:
                break
            time.sleep(2**attempt)
        except RateLimitError:
            if attempt == 2:
                break
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("hypothesis canonicalization failed")
