"""Analyst-facing aliases for planner strategist primitives.

The planner role was originally named "strategist". This module provides
clearer analyst-oriented naming without breaking existing imports.
"""

from nemo.planner.strategist import (
    Hypothesis,
    InterpretationResult,
    Notebook,
    NotebookEntry,
    apply_notebook_update,
    build_schema_context,
    format_notebook,
    interpret_and_update,
    plan_next_step,
)

__all__ = [
    "Hypothesis",
    "InterpretationResult",
    "Notebook",
    "NotebookEntry",
    "apply_notebook_update",
    "build_schema_context",
    "format_notebook",
    "interpret_and_update",
    "plan_next_step",
]
