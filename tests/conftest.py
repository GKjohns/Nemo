from pathlib import Path

import pytest

from nemo.store import NemoStore


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def store(project_dir: Path) -> NemoStore:
    instance = NemoStore(project_dir / "nemo.duckdb")
    instance.initialize()
    try:
        yield instance
    finally:
        instance.close()
