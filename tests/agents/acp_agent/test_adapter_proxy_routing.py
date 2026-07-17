"""Tests for proxy chain routing through ACPClientAdapter.

TDD tests for the critical proxy chain bypass fix.
When conductor is present, prompt() must route through the proxy chain
via conductor._route_to_terminal(), NOT through api.prompt() directly.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.acp_agent.adapter import ACPClientAdapter


# ---------------------------------------------------------------------------
# Test 1: prompt() routes through conductor when conductor is present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_prompt_routes_through_conductor() -> None:
    """When conductor is provided, prompt() routes through conductor._route_to_terminal."""
    conductor = MagicMock()
    conductor._route_to_terminal = AsyncMock(return_value={"result": {"stopReason": "end_turn"}})
    conductor._should_intercept = MagicMock(return_value=True)

    api = MagicMock()
    api.prompt = AsyncMock()

    queue: asyncio.Queue[Any] = asyncio.Queue()
    adapter = ACPClientAdapter(api=api, notification_source=queue, conductor=conductor)

    await adapter.prompt("session-1", [{"type": "text", "text": "hello"}])

    # Wait for background task to complete
    assert adapter._prompt_task is not None
    await adapter._prompt_task

    # Conductor should be called with session/prompt method
    conductor._route_to_terminal.assert_called_once()
    call_args = conductor._route_to_terminal.call_args
    assert call_args[0][0] == "session/prompt"  # method name

    # api.prompt should NOT be called — proxy chain handles routing
    api.prompt.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: prompt() falls back to api.prompt when no conductor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_prompt_falls_back_to_api_without_conductor() -> None:
    """Without conductor, prompt() calls api.prompt directly (backward compat)."""
    from acp.schema import PromptResponse

    api = MagicMock()
    api.prompt = AsyncMock(return_value=PromptResponse(stop_reason="end_turn"))

    queue: asyncio.Queue[Any] = asyncio.Queue()
    adapter = ACPClientAdapter(api=api, notification_source=queue)

    await adapter.prompt("session-1", [{"type": "text", "text": "hello"}])

    # Wait for background task
    assert adapter._prompt_task is not None
    await adapter._prompt_task

    api.prompt.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3: stop_reason extracted from conductor response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_stop_reason_from_conductor_response() -> None:
    """stop_reason is extracted from conductor's route_to_terminal response."""
    conductor = MagicMock()
    conductor._route_to_terminal = AsyncMock(return_value={"result": {"stopReason": "end_turn"}})
    conductor._should_intercept = MagicMock(return_value=True)

    api = MagicMock()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    adapter = ACPClientAdapter(api=api, notification_source=queue, conductor=conductor)

    await adapter.prompt("session-1", [])
    assert adapter._prompt_task is not None
    await adapter._prompt_task

    assert adapter.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# Test 4: prompt() routes through api when conductor doesn't intercept
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_prompt_falls_back_when_conductor_doesnt_intercept() -> None:
    """When conductor exists but doesn't intercept session/prompt, use api.prompt."""
    from acp.schema import PromptResponse

    conductor = MagicMock()
    conductor._should_intercept = MagicMock(return_value=False)

    api = MagicMock()
    api.prompt = AsyncMock(return_value=PromptResponse(stop_reason="end_turn"))

    queue: asyncio.Queue[Any] = asyncio.Queue()
    adapter = ACPClientAdapter(api=api, notification_source=queue, conductor=conductor)

    await adapter.prompt("session-1", [])
    assert adapter._prompt_task is not None
    await adapter._prompt_task

    # api.prompt called because conductor doesn't intercept
    api.prompt.assert_called_once()
