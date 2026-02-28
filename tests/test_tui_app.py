from __future__ import annotations

import asyncio

from rich.console import Console

from nemo.events import EventType, NemoEvent
from nemo.tui.app import _LiveEventSubscriber


class _DummyStatus:
    def __init__(self) -> None:
        self.last_update = ""

    def update(self, text: str) -> None:
        self.last_update = text


def test_tui_analyzing_phase_updates_status(monkeypatch):
    recorded = Console(record=True, width=120)
    monkeypatch.setattr("nemo.tui.app.console", recorded)
    subscriber = _LiveEventSubscriber(budget=5)
    status = _DummyStatus()
    subscriber.set_status(status)

    asyncio.run(
        subscriber.on_event(
            NemoEvent(
                type=EventType.STEP_PHASE,
                run_id="run_1",
                step_num=2,
                payload={
                    "phase": "analyzing",
                    "iteration": 2,
                    "analyst_max_iterations": 8,
                },
            )
        )
    )

    assert "statistical analysis" in status.last_update.lower()
    assert "2/8" in status.last_update


def test_tui_step_completed_renders_statistical_test_lines(monkeypatch):
    recorded = Console(record=True, width=120)
    monkeypatch.setattr("nemo.tui.app.console", recorded)
    subscriber = _LiveEventSubscriber(budget=5)

    asyncio.run(
        subscriber.on_event(
            NemoEvent(
                type=EventType.STEP_COMPLETED,
                run_id="run_1",
                step_num=2,
                payload={
                    "title": "Difference detected",
                    "claim": "Groups differ significantly.",
                    "confidence": 0.9,
                    "duration_ms": 1200,
                    "edges_created": 1,
                    "row_count": 1,
                    "analysis_type": "statistical",
                    "result_preview": [
                        {"test": "ttest_ind", "p_value": 0.01, "effect_size": 0.42},
                    ],
                },
            )
        )
    )

    text = recorded.export_text()
    assert "ttest_ind" in text
    assert "p=0.01" in text
