"""Phase arbiter for explore/exploit outer-loop decisions."""

from __future__ import annotations

import asyncio
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from nemo.config import NemoConfig
from nemo.planner.models import HypothesisRecord, PhaseDecision
from nemo.planner.strategist import Notebook, format_notebook

_CONSECUTIVE_EXPLORE_FORCE_EXPLOIT = 5
_HIGH_CONFIDENCE_THRESHOLD = 0.7
_MIN_HIGH_CONFIDENCE_HYPOTHESES = 2
_ACTIVE_HYPOTHESIS_STATUSES = {"proposed", "testing"}
_RESOLVED_HYPOTHESIS_STATUSES = {"validated", "invalidated", "narrowed", "inconclusive"}
_CLAIM_PREVIEW_CHARS = 150

ARBITER_SYSTEM = """\
You are the strategic advisor for an automated data exploration agent.
You decide what the agent should do next: continue exploring the data
for new patterns (EXPLORE), or rigorously test a specific hypothesis
that emerged from prior exploration (EXPLOIT).

Think like a senior analyst managing an investigation with a limited
time budget. Balance breadth (coverage) with depth (validation).

IMPORTANT decision guidelines:
- If multiple high-confidence hypotheses (≥0.7) have accumulated without
  any being tested, you SHOULD switch to EXPLOIT. Endless exploration
  without validation wastes budget.
- If the last several decisions were all EXPLORE and testable hypotheses
  exist, strongly prefer EXPLOIT.
- Exploration is valuable early, but once hypotheses emerge, the
  investigation must shift to validating them.
- If choosing EXPLOIT, pick the highest-priority hypothesis for this moment.
- If the user has provided an investigation goal, weight hypotheses that
  serve that goal more heavily.
Return a structured decision with clear reasoning."""


def should_consult_arbiter(
    step_num: int,
    last_arbiter_step: int,
    arbiter_interval: int,
    significant_event: bool,
) -> bool:
    """Determine whether the arbiter should run for this step."""
    if step_num <= 1:
        return True
    if significant_event:
        return True
    return (step_num - last_arbiter_step) >= max(1, int(arbiter_interval))


def _count_consecutive_explores(recent_phases: list[PhaseDecision]) -> int:
    """Count how many consecutive EXPLORE decisions trail the recent list."""
    count = 0
    for decision in reversed(recent_phases):
        if decision.phase == "explore":
            count += 1
        else:
            break
    return count


async def decide_phase(
    notebook: Notebook,
    hypotheses: list[HypothesisRecord],
    all_tables: list[str],
    steps_done: int,
    budget: int,
    recent_phases: list[PhaseDecision],
    config: NemoConfig,
    client: OpenAI | None,
    *,
    elapsed_minutes: float = 0.0,
    time_budget_minutes: float = 0.0,
) -> PhaseDecision:
    """Decide whether to explore or exploit next, with guardrails first."""
    proposed = [h for h in hypotheses if h.status == "proposed"]
    testing = [h for h in hypotheses if h.status == "testing"]

    if not proposed and not testing:
        return PhaseDecision(phase="explore", reasoning="No hypotheses to validate yet.")

    if testing:
        active = max(testing, key=lambda h: h.updated_at)
        if int(active.validation_step) < int(config.max_validation_steps):
            return PhaseDecision(
                phase="exploit",
                hypothesis_id=active.hypothesis_id,
                reasoning=(
                    f"Continuing active validation for hypothesis {active.hypothesis_id} "
                    f"(step {active.validation_step}/{config.max_validation_steps})."
                ),
            )

    if (budget - steps_done) <= 2 and proposed:
        best = max(proposed, key=lambda h: float(h.priority))
        return PhaseDecision(
            phase="exploit",
            hypothesis_id=best.hypothesis_id,
            reasoning="Budget nearly exhausted; prioritize highest-priority open hypothesis.",
        )

    high_conf = [h for h in proposed if float(h.initial_confidence) >= _HIGH_CONFIDENCE_THRESHOLD]
    consecutive_explores = _count_consecutive_explores(recent_phases)

    if (
        len(high_conf) >= _MIN_HIGH_CONFIDENCE_HYPOTHESES
        and consecutive_explores >= _CONSECUTIVE_EXPLORE_FORCE_EXPLOIT
    ):
        best = max(high_conf, key=lambda h: float(h.priority))
        return PhaseDecision(
            phase="exploit",
            hypothesis_id=best.hypothesis_id,
            reasoning=(
                f"Forcing exploit: {len(high_conf)} high-confidence hypotheses accumulated "
                f"after {consecutive_explores} consecutive explore steps. "
                f"Validating top hypothesis {best.hypothesis_id} (confidence={best.initial_confidence:.2f})."
            ),
        )

    budget_fraction_used = steps_done / max(1, budget)
    if budget_fraction_used >= 0.5 and proposed and consecutive_explores >= 3:
        best = max(proposed, key=lambda h: float(h.priority))
        return PhaseDecision(
            phase="exploit",
            hypothesis_id=best.hypothesis_id,
            reasoning=(
                f"Over half the budget used ({steps_done}/{budget}) with "
                f"{len(proposed)} untested hypotheses and {consecutive_explores} "
                f"consecutive explores. Switching to exploit."
            ),
        )

    if client is None:
        if proposed:
            best = max(proposed, key=lambda h: float(h.priority))
            return PhaseDecision(
                phase="exploit",
                hypothesis_id=best.hypothesis_id,
                reasoning="LLM arbiter unavailable; defaulting to highest-priority proposed hypothesis.",
            )
        return PhaseDecision(phase="explore", reasoning="LLM arbiter unavailable; continue exploration.")

    context = _build_arbiter_context(
        notebook=notebook,
        hypotheses=hypotheses,
        all_tables=all_tables,
        steps_done=steps_done,
        budget=budget,
        recent_phases=recent_phases,
        elapsed_minutes=elapsed_minutes,
        time_budget_minutes=time_budget_minutes,
        config=config,
    )
    return await _call_arbiter_llm(context, config, client)


def _build_arbiter_context(
    *,
    notebook: Notebook,
    hypotheses: list[HypothesisRecord],
    all_tables: list[str],
    steps_done: int,
    budget: int,
    recent_phases: list[PhaseDecision],
    elapsed_minutes: float,
    time_budget_minutes: float,
    config: NemoConfig | None = None,
) -> str:
    touched = sorted({t for entry in notebook.entries for t in entry.tables_touched if t})
    untouched = sorted([t for t in all_tables if t not in set(touched)])

    formatted_hypotheses = _format_hypotheses(hypotheses, include_resolved=False)
    recent = _format_recent_phases(recent_phases)
    consecutive_explores = _count_consecutive_explores(recent_phases)

    goal_section = ""
    goal = getattr(config, "goal", "")
    if goal and goal.strip():
        goal_section = f"\n## Investigation Goal\n{goal}\n"

    if len(all_tables) <= 1:
        themes = [entry.theme for entry in notebook.entries]
        coverage_section = (
            f"Single-table dataset. Themes explored: {', '.join(themes) if themes else '(none)'}\n"
            f"Theme depth: {', '.join(f'{e.theme} ({e.step_count} steps)' for e in notebook.entries) if notebook.entries else '(none)'}"
        )
    else:
        coverage_section = (
            f"Tables explored: {', '.join(touched) if touched else '(none)'}\n"
            f"Tables not yet explored: {', '.join(untouched) if untouched else '(none)'}"
        )

    return f"""\
## Investigation Notebook
{format_notebook(notebook, detail="summary")}

## Hypothesis Backlog
{formatted_hypotheses}

## Coverage
{coverage_section}

## Budget
Steps completed: {steps_done} / {budget}
Time elapsed: {elapsed_minutes:.1f} / {time_budget_minutes:.1f} minutes
Consecutive EXPLORE decisions so far: {consecutive_explores}

## Recent Decisions
{recent}
{goal_section}
## Task
Decide whether the next step should be:
- EXPLORE: open-ended investigation for new patterns
- EXPLOIT: focused validation of one hypothesis

If multiple hypotheses are ready for testing (especially with confidence ≥0.7),
you should switch to EXPLOIT. Gathering more exploratory data when you already
have strong untested hypotheses wastes budget.

If EXPLOIT, set hypothesis_id to the highest-priority hypothesis to test now.
"""


def _format_hypotheses(
    hypotheses: list[HypothesisRecord],
    include_resolved: bool = False,
    max_active: int = 10,
    max_resolved: int = 3,
) -> str:
    if not hypotheses:
        return "(none)"

    max_active_count = max(0, int(max_active))
    max_resolved_count = max(0, int(max_resolved))
    active = [h for h in hypotheses if h.status in _ACTIVE_HYPOTHESIS_STATUSES]
    resolved = [h for h in hypotheses if h.status in _RESOLVED_HYPOTHESIS_STATUSES]

    lines: list[str] = []
    lines.append("Active hypotheses:")
    if active:
        ranked_active = sorted(active, key=lambda h: (float(h.priority), h.updated_at), reverse=True)
        for h in ranked_active[:max_active_count]:
            evidence_count = len(h.evidence_chain)
            lines.append(
                f"- {h.hypothesis_id} [{h.status}] p={h.priority:.2f}, init={h.initial_confidence:.2f}, "
                f"validation_step={h.validation_step}, evidence={evidence_count}: {_truncate_claim(h.claim)}"
            )
    else:
        lines.append("(none)")

    lines.append(f"Resolved hypotheses: {len(resolved)} ({_summarize_resolved_statuses(resolved)})")
    if include_resolved and resolved and max_resolved_count > 0:
        lines.append("Recent resolved hypotheses:")
        recent_resolved = sorted(resolved, key=lambda h: h.updated_at, reverse=True)
        for h in recent_resolved[:max_resolved_count]:
            verdict_suffix = f", verdict={h.verdict}" if h.verdict else ""
            lines.append(
                f"- {h.hypothesis_id} [{h.status}] confidence={h.verdict_confidence or h.initial_confidence:.2f}"
                f"{verdict_suffix}: {_truncate_claim(h.claim)}"
            )
    return "\n".join(lines)


def _truncate_claim(claim: str, max_chars: int = _CLAIM_PREVIEW_CHARS) -> str:
    trimmed = claim.strip()
    if len(trimmed) <= max_chars:
        return trimmed
    return trimmed[: max_chars - 3].rstrip() + "..."


def _summarize_resolved_statuses(resolved: list[HypothesisRecord]) -> str:
    if not resolved:
        return "none"
    order = ["validated", "invalidated", "narrowed", "inconclusive"]
    counts: dict[str, int] = {}
    for record in resolved:
        counts[record.status] = counts.get(record.status, 0) + 1
    parts: list[str] = [f"{counts[status]} {status}" for status in order if counts.get(status, 0) > 0]
    extras = [status for status in counts if status not in order]
    for status in sorted(extras):
        parts.append(f"{counts[status]} {status}")
    return ", ".join(parts) if parts else "none"


def _format_recent_phases(recent_phases: list[PhaseDecision]) -> str:
    if not recent_phases:
        return "(none)"
    lines = [
        f"- {idx + 1}. {decision.phase.upper()} ({decision.hypothesis_id or 'n/a'}): {decision.reasoning}"
        for idx, decision in enumerate(recent_phases[-5:])
    ]
    return "\n".join(lines)


async def _call_arbiter_llm(context: str, config: NemoConfig, client: OpenAI) -> PhaseDecision:
    messages = [{"role": "user", "content": context}]
    model = config.arbiter_model or "gpt-5.2"
    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                client.responses.parse,
                model=model,
                instructions=ARBITER_SYSTEM,
                input=messages,
                text_format=PhaseDecision,
            )
            if response.output_parsed is not None:
                return response.output_parsed
            raw = _extract_text(response)
            return PhaseDecision.model_validate_json(raw)
        except (APIConnectionError, APITimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(2**attempt)
        except RateLimitError:
            if attempt == 2:
                raise
            await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError("decide_phase: exhausted retries")


def _extract_text(response: Any) -> str:
    for item in getattr(response, "output", []):
        if getattr(item, "type", None) == "message":
            for block in getattr(item, "content", []):
                if hasattr(block, "text"):
                    return str(block.text)
    return ""
