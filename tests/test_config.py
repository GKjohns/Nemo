from __future__ import annotations

from nemo.config import NemoConfig


def test_config_analysis_section_parses(tmp_path):
    config_path = tmp_path / "nemo.toml"
    config_path.write_text(
        """
[analysis]
max_analysis_rows = 12345
analysis_timeout_seconds = 9
analyst_max_iterations = 4
enable_statistical_analysis = false
max_analysis_memory_mb = 64

[exploration]
max_display_rows = 9
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config = NemoConfig.load(config_path)
    assert config.max_analysis_rows == 12345
    assert config.analysis_timeout_seconds == 9
    assert config.analyst_max_iterations == 4
    assert config.enable_statistical_analysis is False
    assert config.max_analysis_memory_mb == 64
    assert config.max_display_rows == 9


def test_config_analysis_defaults_apply_when_missing(tmp_path):
    config_path = tmp_path / "nemo.toml"
    config_path.write_text("", encoding="utf-8")

    config = NemoConfig.load(config_path)
    assert config.max_analysis_rows == 50000
    assert config.analysis_timeout_seconds == 30
    assert config.analyst_max_iterations == 8
    assert config.enable_statistical_analysis is True
    assert config.max_analysis_memory_mb == 256
    assert config.max_display_rows == 15
