from __future__ import annotations

import asyncio
import sys

from nemo.config import NemoConfig
from nemo.events import EventType, NemoEvent
from nemo.hooks import UserHookSubscriber


def test_hook_failure_does_not_raise():
    config = NemoConfig(hooks={"step:*": ["__definitely_missing_command__"]})
    subscriber = UserHookSubscriber(config)
    event = NemoEvent(type=EventType.STEP_STARTED, run_id="run_test", step_num=1, payload={})

    results = asyncio.run(subscriber.on_event(event))
    assert len(results) == 1
    assert results[0].exit_code == 1
    assert results[0].blocked is False
    assert results[0].stderr


def test_pre_execute_hook_exit_code_two_blocks_step(tmp_path):
    script = tmp_path / "block_hook.py"
    script.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
    command = f"{sys.executable} {script.as_posix()}"
    config = NemoConfig(hooks={"step:started": [command]})
    subscriber = UserHookSubscriber(config)
    event = NemoEvent(type=EventType.STEP_STARTED, run_id="run_test", step_num=1, payload={})

    results = asyncio.run(subscriber.on_event(event))
    assert len(results) == 1
    assert results[0].exit_code == 2
    assert results[0].blocked is True
