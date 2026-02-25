"""Execution utilities for compiling and running SQL actions."""

from nemo.executor.compile import compile_action
from nemo.executor.run import ExecutionResult, execute_query

__all__ = ["ExecutionResult", "compile_action", "execute_query"]
