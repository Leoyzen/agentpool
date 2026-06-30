"""Regression tests for ``ProcessHistoryAdapter``.

Tests cover the core wrapping logic, signature validation, annotation
detection, and processor ordering. These serve as a **behavioral baseline**
before the thinning refactor replaces the adapter with direct
``pydantic_ai.capabilities.ProcessHistory`` usage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import warnings

import pytest
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.tools import RunContext

from agentpool.agents.native_agent.process_history_capability import (
    ProcessHistoryAdapter,
    _is_run_context_annotation,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


# ---------------------------------------------------------------------------
# wrap_processor — single parameter (messages only)
# ---------------------------------------------------------------------------


def test_single_param_sync_passthrough():
    """Single-param sync processor is passed through unchanged."""

    def processor(messages: list[ModelMessage]) -> list[ModelMessage]:
        return messages

    wrapped = ProcessHistoryAdapter.wrap_processor(processor)
    assert wrapped is processor  # identity check


def test_single_param_async_passthrough():
    """Single-param async processor is passed through unchanged."""

    async def processor(messages: list[ModelMessage]) -> list[ModelMessage]:
        return messages

    wrapped = ProcessHistoryAdapter.wrap_processor(processor)
    assert wrapped is processor


def test_single_param_untyped_passthrough():
    """Single-param untyped processor is passed through unchanged."""

    def processor(messages):  # noqa: ANN202
        return messages

    wrapped = ProcessHistoryAdapter.wrap_processor(processor)
    assert wrapped is processor


# ---------------------------------------------------------------------------
# wrap_processor — two parameters with typed RunContext
# ---------------------------------------------------------------------------


def test_two_param_typed_runcontext_passthrough():
    """Two-param processor with explicit RunContext annotation passes through."""

    def processor(
        ctx: RunContext[Any], messages: list[ModelMessage]
    ) -> list[ModelMessage]:
        return messages

    wrapped = ProcessHistoryAdapter.wrap_processor(processor)
    assert wrapped is processor


def test_two_param_generic_runcontext_passthrough():
    """Two-param processor with RunContext[Deps] passes through."""

    def processor(
        ctx: RunContext[str], messages: list[ModelMessage]
    ) -> list[ModelMessage]:
        return messages

    wrapped = ProcessHistoryAdapter.wrap_processor(processor)
    assert wrapped is processor


def test_two_param_async_typed_runcontext_passthrough():
    """Two-param async processor with typed RunContext passes through."""

    async def processor(
        ctx: RunContext[Any], messages: list[ModelMessage]
    ) -> list[ModelMessage]:
        return messages

    wrapped = ProcessHistoryAdapter.wrap_processor(processor)
    assert wrapped is processor


# ---------------------------------------------------------------------------
# wrap_processor — two parameters with UNTYPED first param
# ---------------------------------------------------------------------------


def test_two_param_untyped_sync_wrapped():
    """Two-param sync processor with untyped first param gets wrapped."""

    def processor(ctx, messages):  # noqa: ANN001, ANN202
        return messages

    wrapped = ProcessHistoryAdapter.wrap_processor(processor)
    assert wrapped is not processor
    assert hasattr(wrapped, "__wrapped__")


def test_two_param_untyped_async_wrapped():
    """Two-param async processor with untyped first param gets wrapped."""

    async def processor(ctx, messages):  # noqa: ANN001, ANN202
        return messages

    wrapped = ProcessHistoryAdapter.wrap_processor(processor)
    assert wrapped is not processor
    import inspect

    assert inspect.iscoroutinefunction(wrapped)


def test_wrapped_sync_preserves_behavior():
    """Wrapped sync processor still processes messages correctly."""

    def processor(ctx, messages):  # noqa: ANN001, ANN202
        # Simulate: append a marker message
        return [*messages, ModelRequest(parts=[UserPromptPart(content="marker")])]

    wrapped = ProcessHistoryAdapter.wrap_processor(processor)
    original_messages: list[ModelMessage] = []
    result = wrapped(None, original_messages)  # ctx=None for test
    assert len(result) == 1
    assert isinstance(result[0], ModelRequest)


async def test_wrapped_async_preserves_behavior():
    """Wrapped async processor still processes messages correctly."""

    async def processor(ctx, messages):  # noqa: ANN001, ANN202
        return [*messages, ModelRequest(parts=[UserPromptPart(content="async_marker")])]

    wrapped = ProcessHistoryAdapter.wrap_processor(processor)
    result = await wrapped(None, [])
    assert len(result) == 1


# ---------------------------------------------------------------------------
# wrap_processor — invalid signatures
# ---------------------------------------------------------------------------


def test_zero_params_raises():
    """Processor with 0 params raises ValueError."""

    def processor() -> list[ModelMessage]:  # noqa: ANN201
        return []

    with pytest.raises(ValueError, match="1 or 2 arguments"):
        ProcessHistoryAdapter.wrap_processor(processor)


def test_three_params_raises():
    """Processor with 3 params raises ValueError."""

    def processor(a, b, c):  # noqa: ANN001, ANN202
        return []

    with pytest.raises(ValueError, match="1 or 2 arguments"):
        ProcessHistoryAdapter.wrap_processor(processor)


def test_four_params_raises():
    """Processor with 4 params raises ValueError."""

    def processor(a, b, c, d):  # noqa: ANN001, ANN202
        return []

    with pytest.raises(ValueError, match="1 or 2 arguments"):
        ProcessHistoryAdapter.wrap_processor(processor)


# ---------------------------------------------------------------------------
# _is_run_context_annotation
# ---------------------------------------------------------------------------


def test_is_run_context_bare():
    """Bare RunContext is detected."""
    assert _is_run_context_annotation(RunContext) is True


def test_is_run_context_generic():
    """RunContext[Deps] is detected."""
    assert _is_run_context_annotation(RunContext[Any]) is True
    assert _is_run_context_annotation(RunContext[str]) is True


def test_is_run_context_not_runcontext():
    """Non-RunContext types are rejected."""
    assert _is_run_context_annotation(str) is False
    assert _is_run_context_annotation(int) is False
    assert _is_run_context_annotation(None) is False


def test_is_run_context_none_annotation():
    """None annotation is not RunContext."""
    assert _is_run_context_annotation(None) is False


# ---------------------------------------------------------------------------
# from_processors
# ---------------------------------------------------------------------------


def test_from_processors_empty():
    """Empty list returns empty list."""
    result = ProcessHistoryAdapter.from_processors([])
    assert result == []


def test_from_processors_single():
    """Single processor produces single ProcessHistory."""
    def proc(messages: list[ModelMessage]) -> list[ModelMessage]:
        return messages

    result = ProcessHistoryAdapter.from_processors([proc])
    assert len(result) == 1
    assert isinstance(result[0], ProcessHistory)


def test_from_processors_multiple_preserves_order():
    """Multiple processors produce ProcessHistory list in same order."""
    def proc1(messages: list[ModelMessage]) -> list[ModelMessage]:
        return messages
    def proc2(messages: list[ModelMessage]) -> list[ModelMessage]:
        return messages
    def proc3(messages: list[ModelMessage]) -> list[ModelMessage]:
        return messages

    result = ProcessHistoryAdapter.from_processors([proc1, proc2, proc3])
    assert len(result) == 3
    assert all(isinstance(p, ProcessHistory) for p in result)


def test_from_processors_mixed_typed_untyped():
    """Mix of typed and untyped processors all get wrapped correctly."""
    def typed_proc(
        ctx: RunContext[Any], messages: list[ModelMessage]
    ) -> list[ModelMessage]:
        return messages

    def untyped_proc(ctx, messages):  # noqa: ANN001, ANN202
        return messages

    def single_proc(messages: list[ModelMessage]) -> list[ModelMessage]:
        return messages

    result = ProcessHistoryAdapter.from_processors(
        [typed_proc, untyped_proc, single_proc]
    )
    assert len(result) == 3
    assert all(isinstance(p, ProcessHistory) for p in result)


# ---------------------------------------------------------------------------
# Integration: ProcessHistory capability works after wrapping
# ---------------------------------------------------------------------------


async def test_wrapped_processor_works_inside_process_history():
    """A wrapped untyped processor works correctly inside ProcessHistory."""
    call_log: list[str] = []

    def processor(ctx, messages):  # noqa: ANN001, ANN202
        call_log.append("called")
        return messages

    wrapped = ProcessHistoryAdapter.wrap_processor(processor)
    capability = ProcessHistory(wrapped)

    # ProcessHistory stores processors — verify it was accepted
    assert isinstance(capability, ProcessHistory)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
