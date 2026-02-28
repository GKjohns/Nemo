"""Nemo configuration parsing and defaults."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import tomli_w
from dotenv import load_dotenv

load_dotenv()


@dataclass
class NemoConfig:
    # Budget
    max_steps: int = 100
    max_runtime_minutes: int = 30
    max_query_runtime_ms: int = 15000
    max_scan_rows: int | None = None
    saturation_threshold: float = 0.15

    # Scoring weights
    weight_info_gain: float = 0.3
    weight_impact: float = 0.25
    weight_novelty: float = 0.2
    weight_feasibility: float = 0.15
    weight_diversity: float = 0.1

    # LLM
    model: str = "gpt-5-mini"
    plan_model: str | None = None
    openai_api_key: str | None = None

    # Exploration
    reflect_every: int = 10
    max_actions_per_thread: int = 5
    join_confidence_threshold: float = 0.7
    use_learnings: bool = True
    max_steps_per_theme: int = 4
    question_similarity_threshold: float = 0.82
    stagnation_step_limit: int = 3
    arbiter_interval: int = 3
    max_validation_steps: int = 5
    arbiter_model: str = "gpt-5.2"
    max_consecutive_exploit: int = 6
    min_explore_ratio: float = 0.35
    hypothesis_priority_decay: float = 0.15
    validation_budget_fraction: float = 0.15
    max_display_rows: int = 15
    max_analysis_rows: int = 50000
    analysis_timeout_seconds: int = 30
    analyst_max_iterations: int = 8
    enable_statistical_analysis: bool = True
    max_analysis_memory_mb: int = 256

    # User-defined hints
    goal: str = ""
    time_columns: list[str] = field(default_factory=list)
    key_metrics: dict[str, str] = field(default_factory=dict)
    important_dimensions: list[str] = field(default_factory=list)
    join_overrides: dict[str, Any] = field(default_factory=dict)

    # Hooks
    hooks: dict[str, list[str]] = field(default_factory=dict)

    # Output
    verbose: bool = False
    quiet: bool = False

    @classmethod
    def load(cls, path: Path) -> "NemoConfig":
        """Load config from nemo.toml; returns defaults if file is missing."""
        config = cls()
        if not path.exists():
            config.openai_api_key = os.getenv("OPENAI_API_KEY")
            return config

        with path.open("rb") as f:
            raw = tomllib.load(f)

        budget = raw.get("budget", {})
        config.max_steps = int(budget.get("max_steps", config.max_steps))
        config.max_runtime_minutes = int(budget.get("max_runtime_minutes", config.max_runtime_minutes))
        config.max_query_runtime_ms = int(budget.get("max_query_runtime_ms", config.max_query_runtime_ms))
        config.max_scan_rows = budget.get("max_scan_rows", config.max_scan_rows)
        config.saturation_threshold = float(budget.get("saturation_threshold", config.saturation_threshold))

        scoring = raw.get("scoring", {})
        config.weight_info_gain = float(scoring.get("weight_info_gain", config.weight_info_gain))
        config.weight_impact = float(scoring.get("weight_impact", config.weight_impact))
        config.weight_novelty = float(scoring.get("weight_novelty", config.weight_novelty))
        config.weight_feasibility = float(scoring.get("weight_feasibility", config.weight_feasibility))
        config.weight_diversity = float(scoring.get("weight_diversity", config.weight_diversity))

        llm = raw.get("llm", {})
        config.model = str(llm.get("model", config.model))
        plan_model = llm.get("plan_model")
        config.plan_model = str(plan_model) if plan_model is not None else None
        file_key = llm.get("openai_api_key")
        config.openai_api_key = str(file_key) if file_key else os.getenv("OPENAI_API_KEY")

        exploration = raw.get("exploration", {})
        config.reflect_every = int(exploration.get("reflect_every", config.reflect_every))
        config.max_actions_per_thread = int(exploration.get("max_actions_per_thread", config.max_actions_per_thread))
        config.join_confidence_threshold = float(
            exploration.get("join_confidence_threshold", config.join_confidence_threshold)
        )
        config.use_learnings = bool(exploration.get("use_learnings", config.use_learnings))
        config.max_steps_per_theme = int(exploration.get("max_steps_per_theme", config.max_steps_per_theme))
        config.question_similarity_threshold = float(
            exploration.get("question_similarity_threshold", config.question_similarity_threshold)
        )
        config.stagnation_step_limit = int(exploration.get("stagnation_step_limit", config.stagnation_step_limit))
        config.arbiter_interval = int(exploration.get("arbiter_interval", config.arbiter_interval))
        config.max_validation_steps = int(exploration.get("max_validation_steps", config.max_validation_steps))
        config.arbiter_model = str(exploration.get("arbiter_model", config.arbiter_model))
        config.max_display_rows = int(exploration.get("max_display_rows", config.max_display_rows))
        config.max_consecutive_exploit = int(
            exploration.get("max_consecutive_exploit", config.max_consecutive_exploit)
        )
        config.min_explore_ratio = float(
            exploration.get("min_explore_ratio", config.min_explore_ratio)
        )
        config.hypothesis_priority_decay = float(
            exploration.get("hypothesis_priority_decay", config.hypothesis_priority_decay)
        )
        config.validation_budget_fraction = float(
            exploration.get("validation_budget_fraction", config.validation_budget_fraction)
        )

        analysis = raw.get("analysis", {})
        config.max_analysis_rows = int(analysis.get("max_analysis_rows", config.max_analysis_rows))
        config.analysis_timeout_seconds = int(
            analysis.get("analysis_timeout_seconds", config.analysis_timeout_seconds)
        )
        config.analyst_max_iterations = int(
            analysis.get("analyst_max_iterations", config.analyst_max_iterations)
        )
        config.enable_statistical_analysis = bool(
            analysis.get("enable_statistical_analysis", config.enable_statistical_analysis)
        )
        config.max_analysis_memory_mb = int(
            analysis.get("max_analysis_memory_mb", config.max_analysis_memory_mb)
        )

        hints = raw.get("hints", {})
        config.goal = str(hints.get("goal", config.goal))
        config.time_columns = list(hints.get("time_columns", config.time_columns))
        config.important_dimensions = list(hints.get("important_dimensions", config.important_dimensions))
        config.join_overrides = dict(hints.get("join_overrides", config.join_overrides))
        config.key_metrics = dict(hints.get("metrics", config.key_metrics))

        hooks = raw.get("hooks", {})
        config.hooks = {
            str(event): [str(command) for command in commands]
            for event, commands in hooks.items()
            if isinstance(commands, list)
        }

        output = raw.get("output", {})
        config.verbose = bool(output.get("verbose", config.verbose))
        config.quiet = bool(output.get("quiet", config.quiet))
        return config

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_default_config(path: Path) -> None:
    """Write a default nemo.toml file."""
    defaults = NemoConfig()
    budget_payload: dict[str, Any] = {
        "max_steps": defaults.max_steps,
        "max_runtime_minutes": defaults.max_runtime_minutes,
        "max_query_runtime_ms": defaults.max_query_runtime_ms,
        "saturation_threshold": defaults.saturation_threshold,
    }
    if defaults.max_scan_rows is not None:
        budget_payload["max_scan_rows"] = int(defaults.max_scan_rows)

    llm_payload: dict[str, Any] = {"model": defaults.model}
    if defaults.plan_model:
        llm_payload["plan_model"] = defaults.plan_model

    payload = {
        "budget": {
            **budget_payload,
        },
        "scoring": {
            "weight_info_gain": defaults.weight_info_gain,
            "weight_impact": defaults.weight_impact,
            "weight_novelty": defaults.weight_novelty,
            "weight_feasibility": defaults.weight_feasibility,
            "weight_diversity": defaults.weight_diversity,
        },
        "llm": llm_payload,
        "exploration": {
            "reflect_every": defaults.reflect_every,
            "max_actions_per_thread": defaults.max_actions_per_thread,
            "join_confidence_threshold": defaults.join_confidence_threshold,
            "use_learnings": defaults.use_learnings,
            "max_steps_per_theme": defaults.max_steps_per_theme,
            "question_similarity_threshold": defaults.question_similarity_threshold,
            "stagnation_step_limit": defaults.stagnation_step_limit,
            "arbiter_interval": defaults.arbiter_interval,
            "max_validation_steps": defaults.max_validation_steps,
            "arbiter_model": defaults.arbiter_model,
            "max_display_rows": defaults.max_display_rows,
            "max_consecutive_exploit": defaults.max_consecutive_exploit,
            "min_explore_ratio": defaults.min_explore_ratio,
            "hypothesis_priority_decay": defaults.hypothesis_priority_decay,
            "validation_budget_fraction": defaults.validation_budget_fraction,
        },
        "analysis": {
            "max_analysis_rows": defaults.max_analysis_rows,
            "analysis_timeout_seconds": defaults.analysis_timeout_seconds,
            "analyst_max_iterations": defaults.analyst_max_iterations,
            "enable_statistical_analysis": defaults.enable_statistical_analysis,
            "max_analysis_memory_mb": defaults.max_analysis_memory_mb,
        },
        "hints": {
            "time_columns": [],
            "important_dimensions": [],
            "join_overrides": {},
            "metrics": {},
        },
        "hooks": {},
        "output": {
            "verbose": defaults.verbose,
            "quiet": defaults.quiet,
        },
    }
    path.write_text(tomli_w.dumps(payload), encoding="utf-8")
