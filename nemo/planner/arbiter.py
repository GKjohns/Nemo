"""Phase arbiter for explore/exploit outer-loop decisions."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from nemo.config import NemoConfig
from nemo.planner.models import HypothesisRecord, PhaseDecision
from nemo.planner.strategist import Notebook, format_notebook

_CONSECUTIVE_EXPLORE_FORCE_EXPLOIT = 5
_DEFAULT_MAX_CONSECUTIVE_EXPLOIT = 6
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
- Watch for diminishing returns: if a hypothesis under validation keeps
  producing 'confounds' or 'narrows' evidence without clear supporting
  results, consider switching to EXPLORE for more impactful leads rather
  than continuing to invest in a struggling hypothesis.
- Be budget-conscious with validation depth: a single hypothesis consuming
  a large share of the total step budget should have strong supporting
  evidence to justify continued investment. When untested high-priority
  hypotheses are waiting, they represent opportunity cost.
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


def _count_consecutive_exploits(recent_phases: list[PhaseDecision]) -> int:
    """Count how many consecutive EXPLOIT decisions trail the recent list."""
    count = 0
    for decision in reversed(recent_phases):
        if decision.phase == "exploit":
            count += 1
        else:
            break
    return count


def _explore_ratio(recent_phases: list[PhaseDecision]) -> float:
    """Fraction of recent decisions that were EXPLORE (1.0 if no history)."""
    if not recent_phases:
        return 1.0
    explores = sum(1 for d in recent_phases if d.phase == "explore")
    return explores / len(recent_phases)


def effective_max_validation_steps(config: NemoConfig, budget: int) -> int:
    """Scale max validation depth with total step budget.

    On short runs, each validation step is a larger fraction of the total
    budget so we tighten the cap.  On long runs the configured maximum
    applies unchanged.
    """
    configured_max = int(config.max_validation_steps)
    fraction = float(getattr(config, "validation_budget_fraction", 0.15))
    budget_scaled = max(2, math.ceil(budget * fraction))
    return min(configured_max, budget_scaled)


def _has_diminishing_returns(hypothesis: HypothesisRecord) -> bool:
    """Check if recent validation evidence suggests diminishing returns.

    Returns True when the last two evidence items are both confounding or
    narrowing — a signal that further validation on this hypothesis is
    unlikely to produce clean supporting evidence.
    """
    chain = hypothesis.evidence_chain
    if len(chain) < 2:
        return False
    recent = chain[-2:]
    weak = sum(1 for e in recent if e.relationship in ("confounds", "narrows"))
    return weak >= 2


def _recently_validated_themes(hypotheses: list[HypothesisRecord]) -> set[str]:
    """Collect themes/tables from recently resolved hypotheses."""
    themes: set[str] = set()
    resolved = [h for h in hypotheses if h.status in _RESOLVED_HYPOTHESIS_STATUSES]
    for h in resolved:
        themes.update(t.lower().strip() for t in h.tables_involved if t.strip())
        for token in h.claim.lower().split()[:10]:
            cleaned = token.strip(".,;:()\"'")
            if len(cleaned) > 3:
                themes.add(cleaned)
    return themes


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

    max_consecutive_exploit = int(getattr(config, "max_consecutive_exploit", _DEFAULT_MAX_CONSECUTIVE_EXPLOIT))
    min_explore_ratio = float(getattr(config, "min_explore_ratio", 0.35))
    consecutive_exploits = _count_consecutive_exploits(recent_phases)
    ratio = _explore_ratio(recent_phases)

    # --- ANTI-ANCHORING: force explore after too many consecutive exploits ---
    if consecutive_exploits >= max_consecutive_exploit:
        return PhaseDecision(
            phase="explore",
            reasoning=(
                f"Forcing explore: {consecutive_exploits} consecutive exploit steps reached the "
                f"limit ({max_consecutive_exploit}). Switching to exploration for breadth."
            ),
        )

    # --- ANTI-ANCHORING: maintain minimum explore ratio ---
    if len(recent_phases) >= 4 and ratio < min_explore_ratio:
        return PhaseDecision(
            phase="explore",
            reasoning=(
                f"Forcing explore: explore ratio {ratio:.0%} is below minimum "
                f"{min_explore_ratio:.0%} ({len(recent_phases)} recent decisions). "
                f"Need more breadth before further exploitation."
            ),
        )

    if testing:
        active = max(testing, key=lambda h: h.updated_at)
        eff_max = effective_max_validation_steps(config, budget)
        if int(active.validation_step) < eff_max:
            if not _has_diminishing_returns(active):
                return PhaseDecision(
                    phase="exploit",
                    hypothesis_id=active.hypothesis_id,
                    reasoning=(
                        f"Continuing active validation for hypothesis {active.hypothesis_id} "
                        f"(step {active.validation_step}/{eff_max})."
                    ),
                )
            # Evidence is mostly confounding/narrowing — fall through to the
            # LLM arbiter (or subsequent guardrails) rather than auto-continuing.

    if (budget - steps_done) <= 2 and proposed:
        best = _best_hypothesis_with_diversity(proposed, hypotheses, config)
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
        best = _best_hypothesis_with_diversity(high_conf, hypotheses, config)
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
        best = _best_hypothesis_with_diversity(proposed, hypotheses, config)
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
            best = _best_hypothesis_with_diversity(proposed, hypotheses, config)
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


def _best_hypothesis_with_diversity(
    candidates: list[HypothesisRecord],
    all_hypotheses: list[HypothesisRecord],
    config: NemoConfig,
) -> HypothesisRecord:
    """Select the best hypothesis, penalizing those on already-validated themes."""
    decay = float(getattr(config, "hypothesis_priority_decay", 0.15))
    validated_themes = _recently_validated_themes(all_hypotheses)

    def _effective_priority(h: HypothesisRecord) -> float:
        base = float(h.priority)
        if not validated_themes:
            return base
        overlap = sum(
            1 for t in h.tables_involved
            if t.lower().strip() in validated_themes
        )
        claim_tokens = {tok.strip(".,;:()\"'").lower() for tok in h.claim.split()[:15]}
        theme_overlap = len(claim_tokens & validated_themes)
        penalty = decay * (overlap + min(theme_overlap, 3))
        return base - penalty

    return max(candidates, key=_effective_priority)


def _format_hypothesis_health(
    hypotheses: list[HypothesisRecord],
    budget: int,
    steps_done: int,
) -> str:
    """Summarize per-hypothesis budget consumption and evidence quality."""
    testing = [h for h in hypotheses if h.status == "testing"]
    proposed = [h for h in hypotheses if h.status == "proposed"]

    if not testing and not proposed:
        return "(no active hypotheses)"

    lines: list[str] = []
    for h in testing:
        budget_pct = (h.validation_step / max(1, budget)) * 100
        supports = sum(1 for e in h.evidence_chain if e.relationship == "supports")
        contradicts = sum(1 for e in h.evidence_chain if e.relationship == "contradicts")
        weak = sum(1 for e in h.evidence_chain if e.relationship in ("confounds", "narrows"))

        if weak >= 2:
            health = "struggling — recent evidence mostly confounding/narrowing"
        elif contradicts >= supports and h.validation_step >= 2:
            health = "uncertain — contradictions match or exceed supporting evidence"
        else:
            health = "on track"

        lines.append(
            f"- {h.hypothesis_id} [testing]: {h.validation_step} validation steps "
            f"({budget_pct:.0f}% of budget), evidence: {supports} supports, "
            f"{contradicts} contradicts, {weak} confounds/narrows — {health}"
        )

    untested_high = sorted(
        [h for h in proposed if float(h.priority) >= 0.6],
        key=lambda h: float(h.priority),
        reverse=True,
    )
    if untested_high:
        lines.append(
            f"- {len(untested_high)} untested hypothesis(es) with priority ≥ 0.6 waiting "
            f"(top: {untested_high[0].hypothesis_id}, p={untested_high[0].priority:.2f})"
        )

    return "\n".join(lines) if lines else "(no active hypotheses)"


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

    health_section = _format_hypothesis_health(hypotheses, budget, steps_done)
    eff_max = effective_max_validation_steps(config, budget) if config else int(budget * 0.15) or 2

    return f"""\
## Investigation Notebook
{format_notebook(notebook, detail="summary")}

## Hypothesis Backlog
{formatted_hypotheses}

## Hypothesis Health (budget-aware)
{health_section}
Effective max validation steps this run: {eff_max} (budget={budget})

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

If a hypothesis under validation is "struggling" or "uncertain", consider
switching to EXPLORE or picking a different hypothesis rather than continuing
to invest steps in a lead with diminishing returns.

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
