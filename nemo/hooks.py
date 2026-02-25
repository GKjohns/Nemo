"""User hook subscriber for event-driven extensibility."""

from __future__ import annotations

import asyncio
import json
import shlex
from dataclasses import dataclass

from nemo.config import NemoConfig
from nemo.events import EventType, NemoEvent


@dataclass
class HookResult:
    """Result of a single hook command execution."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    blocked: bool = False


class UserHookSubscriber:
    """
    Executes configured hook commands when matching events are emitted.

    Exit code semantics:
    - 0: success
    - 1: warning (logged in result, does not block)
    - 2: block/skip (only applied for step:started)
    """

    def __init__(self, config: NemoConfig):
        self._hooks = dict(config.hooks or {})

    async def on_event(self, event: NemoEvent) -> list[HookResult]:
        commands = self._match_commands(event.type)
        if not commands:
            return []
        results: list[HookResult] = []
        for command in commands:
            results.append(await self._run_hook(command, event))
        return results

    async def _run_hook(self, command: str, event: NemoEvent) -> HookResult:
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        payload = json.dumps(event.to_dict()).encode("utf-8")
        stdout, stderr = await proc.communicate(input=payload)
        code = int(proc.returncode or 0)
        blocked = code == 2 and event.type == EventType.STEP_STARTED
        return HookResult(
            command=command,
            exit_code=code,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            blocked=blocked,
        )

    def _match_commands(self, event_type: EventType) -> list[str]:
        event_value = event_type.value
        matched: list[str] = []
        for key, commands in self._hooks.items():
            if not commands:
                continue
            if self._event_matches(key, event_value):
                matched.extend(commands)
        return matched

    @staticmethod
    def _event_matches(pattern: str, event_value: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return event_value.startswith(pattern[:-1])
        return pattern == event_value
