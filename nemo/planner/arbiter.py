"""Phase arbiter for explore/exploit outer-loop decisions."""

from __future__ import annotations

import asyncio
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from nemo.config import NemoConfig
from nemo.planner.models import HypothesisRecord, PhaseDecision
from nemo.planner.strategist import Notebook, format_notebook

ARBITER_SYSTEM = """\
You are the strategic advisor for an automated data exploration agent.
You decide what the agent should do next: continue exploring the data
for new patterns (EXPLORE), or rigorously test a specific hypothesis
that emerged from prior exploration (EXPLOIT).

Think like a senior analyst managing an investigation with a limited
time budget. Balance breadth (coverage) with depth (validation).
If choosing EXPLOIT, pick the highest-priority hypothesis for this moment.
If the user has provided an investigation goal, weight hypotheses that
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

    formatted_hypotheses = _format_hypotheses(hypotheses)
    recent = _format_recent_phases(recent_phases)

    goal_section = ""
    goal = getattr(config, "goal", "")
    if goal and goal.strip():
        goal_section = f"\n## Investigation Goal\n{goal}\n"

    return f"""\
## Investigation State
{format_notebook(notebook)}

## Hypothesis Backlog
{formatted_hypotheses}

## Coverage
Tables explored: {", ".join(touched) if touched else "(none)"}
Tables not yet explored: {", ".join(untouched) if untouched else "(none)"}

## Budget
Steps completed: {steps_done} / {budget}
Time elapsed: {elapsed_minutes:.1f} / {time_budget_minutes:.1f} minutes

## Recent Decisions
{recent}
{goal_section}
## Task
Decide whether the next step should be:
- EXPLORE: open-ended investigation for new patterns
- EXPLOIT: focused validation of one hypothesis

If EXPLOIT, set hypothesis_id to the highest-priority hypothesis to test now.
"""


def _format_hypotheses(hypotheses: list[HypothesisRecord]) -> str:
    if not hypotheses:
        return "(none)"
    lines: list[str] = []
    for h in hypotheses:
        evidence_count = len(h.evidence_chain)
        lines.append(
            f"- {h.hypothesis_id} [{h.status}] p={h.priority:.2f}, init={h.initial_confidence:.2f}, "
            f"validation_step={h.validation_step}, evidence={evidence_count}: {h.claim}"
        )
    return "\n".join(lines)


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
    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                client.responses.parse,
                model=config.arbiter_model or config.model or "gpt-5-mini",
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
