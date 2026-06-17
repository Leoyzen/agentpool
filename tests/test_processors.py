"""Test processor functions for history processor testing.

These are used by tests/sessions/test_history_processors.py to test
config resolution of history processors via import paths.
"""

from __future__ import annotations


def keep_recent(messages: list) -> list:
    """Keep only recent messages."""
    return messages[-2:] if len(messages) > 2 else messages


async def filter_thinking_async(messages: list) -> list:
    """Filter out thinking messages asynchronously."""
    return messages


def context_aware_sync(ctx: object, messages: list) -> list:
    """A sync processor that takes context."""
    return messages


async def context_aware_async(ctx: object, messages: list) -> list:
    """An async processor that takes context."""
    return messages


def invalid_processor_too_many(a: object, b: object, c: object) -> list:
    """Invalid: too many arguments."""
    return []


def invalid_processor_wrong_name(ctx: object, wrong_name: object) -> list:
    """Invalid by old convention, but allowed by current validation."""
    return []
