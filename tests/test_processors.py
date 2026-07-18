"""Test history processor functions used by test_history_processors.py.

These are imported via string paths like ``tests.test_processors:keep_recent``
by NativeAgentConfig.get_history_processors().
"""

from __future__ import annotations

from typing import Any

import pytest


pytestmark = pytest.mark.unit


def keep_recent(messages: list[Any]) -> list[Any]:
    """Sync processor without context — returns last 10 messages."""
    return messages[-10:]


async def filter_thinking_async(messages: list[Any]) -> list[Any]:
    """Async processor without context — filters out thinking blocks."""
    return [m for m in messages if getattr(m, "role", "") != "thinking"]


def context_aware_sync(ctx: Any, messages: list[Any]) -> list[Any]:
    """Sync processor with context — returns messages unchanged."""
    return messages


async def context_aware_async(ctx: Any, messages: list[Any]) -> list[Any]:
    """Async processor with context — returns messages unchanged."""
    return messages


def invalid_processor_too_many(a: Any, b: Any, c: Any) -> Any:
    """Processor with too many args — used to test validation rejection."""
    return a


def invalid_processor_wrong_name(ctx: Any, extra_arg: Any) -> Any:
    """Processor with wrong second param name — used to test name validation."""
    return ctx
