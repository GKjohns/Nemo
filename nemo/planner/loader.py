"""Custom planner generator loading."""

from __future__ import annotations

import importlib.util
import inspect
import logging
from collections.abc import Callable
from pathlib import Path

from nemo.planner.generators import ALL_GENERATORS

LOGGER = logging.getLogger(__name__)


def load_custom_generators(generators_dir: Path) -> list[Callable]:
    """
    Scan .nemo/generators/ for Python files.
    Each file must export:
      def generate(ctx) -> list[FrontierItem]
    Invalid files are skipped with warnings.
    """
    discovered: list[Callable] = []
    if not generators_dir.exists():
        return discovered

    for file_path in sorted(generators_dir.glob("*.py")):
        module_name = f"nemo_custom_generator_{file_path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                LOGGER.warning("Skipping custom generator with invalid import spec: %s", file_path)
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            generate = getattr(module, "generate", None)
            if not callable(generate):
                LOGGER.warning("Skipping %s: missing callable `generate`", file_path.name)
                continue
            signature = inspect.signature(generate)
            if len(signature.parameters) != 1:
                LOGGER.warning("Skipping %s: `generate` must accept exactly one argument", file_path.name)
                continue
            discovered.append(generate)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Skipping invalid custom generator %s: %s", file_path.name, exc)
    return discovered


def get_all_generators(generators_dir: Path | None = None) -> list[Callable]:
    """Return built-in generators + any custom generators."""
    generators: list[Callable] = list(ALL_GENERATORS)
    if generators_dir is None:
        return generators
    generators.extend(load_custom_generators(generators_dir))
    return generators
