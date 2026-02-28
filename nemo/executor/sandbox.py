"""Restricted Python sandbox for statistical analysis execution."""

from __future__ import annotations

import builtins
import io
import signal
import threading
import time
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from types import ModuleType
from typing import Any

ALLOWED_IMPORTS = {
    "pandas",
    "numpy",
    "scipy",
    "scipy.stats",
    "statsmodels",
    "statsmodels.api",
    "statsmodels.formula.api",
    "statsmodels.stats.proportion",
    "statsmodels.stats.weightstats",
    "statsmodels.stats.diagnostic",
    "statsmodels.stats.outliers_influence",
    "math",
    "statistics",
    "collections",
    "itertools",
    "functools",
    "datetime",
    "decimal",
    "json",
    "re",
}

_PERSISTED_RUNTIME_KEYS = {
    "__builtins__",
    "__name__",
    "__doc__",
    "__package__",
    "__loader__",
    "__spec__",
    "__annotations__",
}


@dataclass
class PythonResult:
    """Structured output from one sandboxed Python execution."""

    result: Any = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    duration_ms: int = 0
    memory_mb: float = 0.0


class PythonSession:
    """Stateful Python execution sandbox with restricted imports."""

    def __init__(self, *, timeout_seconds: int = 30, max_output_chars: int = 50_000):
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.max_output_chars = max(1, int(max_output_chars))
        self.session_vars: dict[str, Any] = {}

    def execute(self, code: str) -> PythonResult:
        """Execute code inside a constrained namespace."""
        namespace = {
            "__builtins__": _safe_builtins(),
            **self.session_vars,
        }
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        started = time.perf_counter()

        try:
            with _timeout(self.timeout_seconds):
                with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                    exec(compile(code, "<analyst>", "exec"), namespace)  # noqa: S102
        except TimeoutError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return PythonResult(
                result=None,
                stdout=_truncate(stdout_buf.getvalue(), self.max_output_chars),
                stderr=_truncate(stderr_buf.getvalue(), self.max_output_chars),
                error=str(exc),
                duration_ms=elapsed,
                memory_mb=self._estimate_memory_mb(),
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - started) * 1000)
            return PythonResult(
                result=None,
                stdout=_truncate(stdout_buf.getvalue(), self.max_output_chars),
                stderr=_truncate(stderr_buf.getvalue(), self.max_output_chars),
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=elapsed,
                memory_mb=self._estimate_memory_mb(),
            )

        elapsed = int((time.perf_counter() - started) * 1000)
        self._persist_session_vars(namespace)
        return PythonResult(
            result=namespace.get("_result"),
            stdout=_truncate(stdout_buf.getvalue(), self.max_output_chars),
            stderr=_truncate(stderr_buf.getvalue(), self.max_output_chars),
            error=None,
            duration_ms=elapsed,
            memory_mb=self._estimate_memory_mb(),
        )

    def _persist_session_vars(self, namespace: dict[str, Any]) -> None:
        persisted: dict[str, Any] = {}
        for key, value in namespace.items():
            if key.startswith("__") or key in _PERSISTED_RUNTIME_KEYS:
                continue
            persisted[key] = value
        self.session_vars = persisted

    def _estimate_memory_mb(self) -> float:
        total_bytes = 0
        for value in self.session_vars.values():
            total_bytes += _estimate_object_bytes(value)
        return round(total_bytes / (1024 * 1024), 6)


def _safe_builtins() -> dict[str, Any]:
    allowed_names = {
        "abs",
        "all",
        "any",
        "bool",
        "bytes",
        "callable",
        "chr",
        "complex",
        "dict",
        "divmod",
        "enumerate",
        "Exception",
        "filter",
        "float",
        "format",
        "frozenset",
        "hash",
        "hex",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "min",
        "next",
        "object",
        "oct",
        "ord",
        "pow",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "type",
        "ValueError",
        "TypeError",
        "RuntimeError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "ZeroDivisionError",
        "zip",
    }
    safe = {name: getattr(builtins, name) for name in allowed_names}
    safe["__import__"] = _safe_import
    return safe


def _safe_import(
    name: str,
    globals: dict[str, Any] | None = None,  # noqa: A002
    locals: dict[str, Any] | None = None,  # noqa: A002
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> ModuleType:
    if level != 0:
        raise ImportError("Relative imports are not allowed")
    if not _is_import_allowed(name):
        raise ImportError(f"Import of '{name}' is not allowed")
    if fromlist:
        for item in fromlist:
            if item == "*":
                continue
            dotted = f"{name}.{item}"
            if not _is_import_allowed(dotted) and not _is_import_allowed(name):
                raise ImportError(f"Import of '{dotted}' is not allowed")
    return builtins.__import__(name, globals, locals, fromlist, level)


def _is_import_allowed(name: str) -> bool:
    if name in ALLOWED_IMPORTS:
        return True
    for allowed in ALLOWED_IMPORTS:
        if name.startswith(f"{allowed}."):
            return True
        if allowed.startswith(f"{name}."):
            return True
    return False


@contextmanager
def _timeout(seconds: int):
    if seconds <= 0:
        yield
        return

    is_main_thread = threading.current_thread() is threading.main_thread()
    if not is_main_thread:
        started = time.perf_counter()
        yield
        elapsed = time.perf_counter() - started
        if elapsed > seconds:
            raise TimeoutError(f"Execution timed out after {seconds} seconds")
        return

    previous = signal.getsignal(signal.SIGALRM)

    def _handler(_signum: int, _frame: Any) -> None:
        raise TimeoutError(f"Execution timed out after {seconds} seconds")

    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _estimate_object_bytes(value: Any) -> int:
    if hasattr(value, "memory_usage"):
        try:
            memory_usage = value.memory_usage(deep=True)
            if hasattr(memory_usage, "sum"):
                return int(memory_usage.sum())
            return int(memory_usage)
        except Exception:  # noqa: BLE001
            return 0
    if hasattr(value, "nbytes"):
        try:
            return int(value.nbytes)
        except Exception:  # noqa: BLE001
            return 0
    return 0
