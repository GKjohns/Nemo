from __future__ import annotations

import asyncio

from nemo.events import EventBus, EventType, NemoEvent


def test_event_bus_dispatches_to_subscribers():
    bus = EventBus()
    received: list[str] = []

    async def handler(event: NemoEvent) -> None:
        received.append(event.type.value)

    bus.subscribe(handler)
    asyncio.run(bus.emit(NemoEvent(type=EventType.RUN_STARTED, run_id="run_test")))

    assert received == ["run:started"]


def test_event_bus_type_filtering():
    bus = EventBus()
    received: list[str] = []

    async def filtered(event: NemoEvent) -> None:
        received.append(event.type.value)

    bus.subscribe(filtered, types=[EventType.STEP_COMPLETED])
    asyncio.run(bus.emit(NemoEvent(type=EventType.STEP_STARTED, run_id="run_1")))
    asyncio.run(bus.emit(NemoEvent(type=EventType.STEP_COMPLETED, run_id="run_1")))

    assert received == ["step:completed"]


def test_event_to_dict_payload_shape():
    event = NemoEvent(
        type=EventType.INSIGHT_CREATED,
        run_id="run_x",
        step_num=3,
        payload={"insight_id": "insight_1", "claim": "something"},
    )
    serialized = event.to_dict()
    assert serialized["type"] == "insight:created"
    assert serialized["run_id"] == "run_x"
    assert serialized["step_num"] == 3
    assert serialized["insight_id"] == "insight_1"
