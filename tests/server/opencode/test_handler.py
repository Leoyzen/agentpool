"""Unit and end-to-end tests for OpenCodeProtocolHandler.

Covers:
- Per-agent canary flag resolution (global vs. per-agent metadata)
- Event consumer lifecycle (subscribe, forward, sentinel shutdown)
- Event conversion (StreamCompleteEvent → SessionIdleEvent, etc.)
- Session lifecycle via mocked SessionPool
- End-to-end flow with a real SessionPool and TestModel agent
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.events import RunErrorEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.models.agents import NativeAgentConfig
from agentpool.orchestrator.core import SessionPool
from agentpool_server.opencode_server.handler import OpenCodeProtocolHandler
from agentpool_server.opencode_server.models.events import (
    SessionErrorEvent,
    SessionIdleEvent,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_manifest() -> MagicMock:
    """Create a mock manifest with configurable opencode flag."""
    manifest = MagicMock()
    manifest.opencode.use_session_pool = False
    manifest.agents = {}
    return manifest


@pytest.fixture
def mock_agent_pool(mock_manifest: MagicMock) -> MagicMock:
    """Create a mock AgentPool wired to *mock_manifest*."""
    pool = MagicMock()
    pool.manifest = mock_manifest
    pool.session_pool = None
    return pool


@pytest.fixture
def mock_state() -> MagicMock:
    """Create a mock ServerState with an async broadcast_event."""
    state = MagicMock()
    state.broadcast_event = AsyncMock()
    return state


@pytest.fixture
def mock_session_pool() -> MagicMock:
    """Create a mock SessionPool with async EventBus methods."""
    pool = MagicMock()
    pool.event_bus.subscribe = AsyncMock(return_value=asyncio.Queue())
    pool.event_bus.unsubscribe = AsyncMock()
    pool.event_bus.close_session = AsyncMock()
    pool.create_session = AsyncMock()
    pool.process_prompt = AsyncMock()
    pool.close_session = AsyncMock()
    return pool


@pytest.fixture
def handler(
    mock_agent_pool: MagicMock,
    mock_state: MagicMock,
) -> OpenCodeProtocolHandler:
    """Create an OpenCodeProtocolHandler with mocked dependencies."""
    return OpenCodeProtocolHandler(agent_pool=mock_agent_pool, state=mock_state)


@pytest.fixture
def test_model() -> TestModel:
    """Return a TestModel that produces deterministic output."""
    return TestModel(custom_output_text="test response")


# =============================================================================
# Canary flag resolution (5.12)
# =============================================================================


class TestCanaryFlag:
    """Test per-agent and global canary flag resolution."""

    def test_global_flag_off_no_agent_name(self, handler: OpenCodeProtocolHandler) -> None:
        """When global flag is off and no agent given, returns False."""
        handler._agent_pool.manifest.opencode.use_session_pool = False
        assert handler._agent_uses_session_pool() is False

    def test_global_flag_on_no_agent_name(self, handler: OpenCodeProtocolHandler) -> None:
        """When global flag is on and no agent given, returns True."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        assert handler._agent_uses_session_pool() is True

    def test_per_agent_override_global_on(
        self,
        handler: OpenCodeProtocolHandler,
    ) -> None:
        """Per-agent metadata=False overrides global=True."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        cfg = NativeAgentConfig(name="agent-a", model="test")
        cfg = cfg.model_copy(update={"metadata": {"use_session_pool": False}})
        handler._agent_pool.manifest.agents = {"agent-a": cfg}
        assert handler._agent_uses_session_pool("agent-a") is False

    def test_per_agent_override_global_off(
        self,
        handler: OpenCodeProtocolHandler,
    ) -> None:
        """Per-agent metadata=True overrides global=False."""
        handler._agent_pool.manifest.opencode.use_session_pool = False
        cfg = NativeAgentConfig(name="agent-a", model="test")
        cfg = cfg.model_copy(update={"metadata": {"use_session_pool": True}})
        handler._agent_pool.manifest.agents = {"agent-a": cfg}
        assert handler._agent_uses_session_pool("agent-a") is True

    def test_missing_agent_falls_back_to_global(
        self,
        handler: OpenCodeProtocolHandler,
    ) -> None:
        """Unknown agent name falls back to global flag."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        assert handler._agent_uses_session_pool("nonexistent") is True

    def test_agent_without_metadata_falls_back(
        self,
        handler: OpenCodeProtocolHandler,
    ) -> None:
        """Agent with empty metadata falls back to global flag."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        cfg = NativeAgentConfig(name="agent-a", model="test")
        handler._agent_pool.manifest.agents = {"agent-a": cfg}
        assert handler._agent_uses_session_pool("agent-a") is True

    def test_non_dict_metadata_falls_back(
        self,
        handler: OpenCodeProtocolHandler,
    ) -> None:
        """Agent with non-dict metadata falls back to global flag."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        cfg = NativeAgentConfig(name="agent-a", model="test")
        # Simulate corrupted metadata by patching after creation
        object.__setattr__(cfg, "metadata", "not-a-dict")  # type: ignore[literal-assign]
        handler._agent_pool.manifest.agents = {"agent-a": cfg}
        assert handler._agent_uses_session_pool("agent-a") is True


# =============================================================================
# handle_message with canary
# =============================================================================


class TestHandleMessage:
    """Test handle_message under various canary configurations."""

    @pytest.mark.anyio
    async def test_raises_when_global_flag_off(
        self,
        handler: OpenCodeProtocolHandler,
    ) -> None:
        """When global flag is off, handle_message raises RuntimeError."""
        handler._agent_pool.manifest.opencode.use_session_pool = False
        with pytest.raises(RuntimeError, match="use_session_pool is disabled"):
            await handler.handle_message("sess-1", "hello")

    @pytest.mark.anyio
    async def test_raises_when_per_agent_flag_off(
        self,
        handler: OpenCodeProtocolHandler,
    ) -> None:
        """When per-agent flag is off, handle_message raises RuntimeError."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        cfg = NativeAgentConfig(name="agent-a", model="test")
        cfg = cfg.model_copy(update={"metadata": {"use_session_pool": False}})
        handler._agent_pool.manifest.agents = {"agent-a": cfg}
        with pytest.raises(RuntimeError, match="use_session_pool is disabled"):
            await handler.handle_message("sess-1", "hello", agent_name="agent-a")

    @pytest.mark.anyio
    async def test_uses_session_pool_when_flag_on(
        self,
        handler: OpenCodeProtocolHandler,
        mock_session_pool: MagicMock,
    ) -> None:
        """When flag is on, handle_message delegates to SessionPool."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        handler._agent_pool.session_pool = mock_session_pool
        await handler.handle_message("sess-1", "hello")
        mock_session_pool.create_session.assert_awaited_once_with("sess-1")
        mock_session_pool.process_prompt.assert_awaited_once_with("sess-1", "hello")

    @pytest.mark.anyio
    async def test_raises_when_session_pool_none(
        self,
        handler: OpenCodeProtocolHandler,
    ) -> None:
        """When flag is on but SessionPool is None, raises RuntimeError."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        handler._agent_pool.session_pool = None
        with pytest.raises(RuntimeError, match="SessionPool is not initialized"):
            await handler.handle_message("sess-1", "hello")


# =============================================================================
# _ensure_event_consumer
# =============================================================================


class TestEnsureEventConsumer:
    """Test event consumer subscription logic."""

    @pytest.mark.anyio
    async def test_skips_when_flag_off(
        self,
        handler: OpenCodeProtocolHandler,
    ) -> None:
        """When canary is off, _ensure_event_consumer is a no-op."""
        handler._agent_pool.manifest.opencode.use_session_pool = False
        await handler._ensure_event_consumer("sess-1")
        assert "sess-1" not in handler._consumer_tasks

    @pytest.mark.anyio
    async def test_skips_when_session_pool_none(
        self,
        handler: OpenCodeProtocolHandler,
    ) -> None:
        """When SessionPool is None, consumer is not started."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        handler._agent_pool.session_pool = None
        await handler._ensure_event_consumer("sess-1")
        assert "sess-1" not in handler._consumer_tasks

    @pytest.mark.anyio
    async def test_starts_consumer_when_flag_on(
        self,
        handler: OpenCodeProtocolHandler,
        mock_session_pool: MagicMock,
    ) -> None:
        """When flag is on and pool exists, consumer task is created."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        handler._agent_pool.session_pool = mock_session_pool
        await handler._ensure_event_consumer("sess-1")
        assert "sess-1" in handler._consumer_tasks
        task = handler._consumer_tasks["sess-1"]
        assert not task.done()
        # Clean up
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.anyio
    async def test_idempotent(
        self,
        handler: OpenCodeProtocolHandler,
        mock_session_pool: MagicMock,
    ) -> None:
        """Second call for same session is a no-op."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        handler._agent_pool.session_pool = mock_session_pool
        await handler._ensure_event_consumer("sess-1")
        first_task = handler._consumer_tasks["sess-1"]
        await handler._ensure_event_consumer("sess-1")
        assert handler._consumer_tasks["sess-1"] is first_task
        # Clean up
        first_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_task


# =============================================================================
# Event consumer loop
# =============================================================================


class TestEventConsumerLoop:
    """Test the internal _event_consumer_loop."""

    @pytest.mark.anyio
    async def test_forwards_events_to_state(
        self,
        handler: OpenCodeProtocolHandler,
        mock_state: MagicMock,
    ) -> None:
        """Events from the queue are forwarded as OpenCode events."""
        queue: asyncio.Queue[Any] = asyncio.Queue()
        await queue.put(StreamCompleteEvent(message=ChatMessage(content="done", role="assistant")))
        await queue.put(None)  # sentinel

        await handler._event_consumer_loop("sess-1", queue)

        mock_state.broadcast_event.assert_awaited_once()
        event = mock_state.broadcast_event.await_args[0][0]
        assert isinstance(event, SessionIdleEvent)

    @pytest.mark.anyio
    async def test_run_error_event_converted(
        self,
        handler: OpenCodeProtocolHandler,
        mock_state: MagicMock,
    ) -> None:
        """RunErrorEvent is converted to SessionErrorEvent."""
        queue: asyncio.Queue[Any] = asyncio.Queue()
        await queue.put(RunErrorEvent(message="boom", run_id="r1"))
        await queue.put(None)

        await handler._event_consumer_loop("sess-1", queue)

        event = mock_state.broadcast_event.await_args[0][0]
        assert isinstance(event, SessionErrorEvent)
        assert "boom" in event.properties.error.data["message"]

    @pytest.mark.anyio
    async def test_unknown_event_ignored(
        self,
        handler: OpenCodeProtocolHandler,
        mock_state: MagicMock,
    ) -> None:
        """Unknown events are silently dropped (no broadcast)."""
        queue: asyncio.Queue[Any] = asyncio.Queue()
        await queue.put(RunStartedEvent(session_id="sess-1", run_id="r1"))
        await queue.put(None)

        await handler._event_consumer_loop("sess-1", queue)

        mock_state.broadcast_event.assert_not_awaited()

    @pytest.mark.anyio
    async def test_cancelled_task_exits_cleanly(
        self,
        handler: OpenCodeProtocolHandler,
    ) -> None:
        """CancelledError propagates out of the loop."""
        queue: asyncio.Queue[Any] = asyncio.Queue()
        task = asyncio.create_task(handler._event_consumer_loop("sess-1", queue))
        await asyncio.sleep(0)  # let task start
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "sess-1" not in handler._consumer_tasks


# =============================================================================
# Event conversion
# =============================================================================


class TestConvertEvent:
    """Test _convert_event mappings."""

    def test_stream_complete_to_idle(self, handler: OpenCodeProtocolHandler) -> None:
        """StreamCompleteEvent becomes SessionIdleEvent."""
        event = StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))
        result = handler._convert_event("s1", event)
        assert isinstance(result, SessionIdleEvent)

    def test_run_error_to_session_error(self, handler: OpenCodeProtocolHandler) -> None:
        """RunErrorEvent becomes SessionErrorEvent."""
        event = RunErrorEvent(message="something failed", run_id="r1")
        result = handler._convert_event("s1", event)
        assert isinstance(result, SessionErrorEvent)

    def test_unknown_returns_none(self, handler: OpenCodeProtocolHandler) -> None:
        """Unmapped events return None."""
        event = RunStartedEvent(session_id="s1", run_id="r1")
        assert handler._convert_event("s1", event) is None


# =============================================================================
# close_session
# =============================================================================


class TestCloseSession:
    """Test session cleanup via close_session."""

    @pytest.mark.anyio
    async def test_cancels_consumer_task(
        self,
        handler: OpenCodeProtocolHandler,
        mock_session_pool: MagicMock,
    ) -> None:
        """close_session cancels the running consumer task."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        handler._agent_pool.session_pool = mock_session_pool
        await handler._ensure_event_consumer("sess-1")
        task = handler._consumer_tasks["sess-1"]
        assert not task.done()

        await handler.close_session("sess-1")

        assert task.cancelled()
        assert "sess-1" not in handler._consumer_tasks

    @pytest.mark.anyio
    async def test_unsubscribes_from_event_bus(
        self,
        handler: OpenCodeProtocolHandler,
        mock_session_pool: MagicMock,
    ) -> None:
        """close_session unsubscribes the queue from the EventBus."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        handler._agent_pool.session_pool = mock_session_pool
        await handler._ensure_event_consumer("sess-1")
        await handler.close_session("sess-1")

        mock_session_pool.event_bus.unsubscribe.assert_awaited_once()

    @pytest.mark.anyio
    async def test_calls_session_pool_close(
        self,
        handler: OpenCodeProtocolHandler,
        mock_session_pool: MagicMock,
    ) -> None:
        """close_session delegates to SessionPool.close_session."""
        handler._agent_pool.manifest.opencode.use_session_pool = True
        handler._agent_pool.session_pool = mock_session_pool
        await handler.close_session("sess-1")

        mock_session_pool.close_session.assert_awaited_once_with("sess-1")

    @pytest.mark.anyio
    async def test_noop_when_no_consumer(
        self,
        handler: OpenCodeProtocolHandler,
        mock_session_pool: MagicMock,
    ) -> None:
        """close_session is safe when no consumer was started."""
        handler._agent_pool.session_pool = mock_session_pool
        await handler.close_session("sess-1")
        assert "sess-1" not in handler._consumer_tasks


# =============================================================================
# End-to-end with real SessionPool (5.11)
# =============================================================================


class TestEndToEndSession:
    """End-to-end tests using a real SessionPool and TestModel agent."""

    @pytest.fixture
    def e2e_pool(self) -> MagicMock:
        """Create a mock AgentPool suitable for SessionPool construction."""
        pool = MagicMock()
        pool.manifest = MagicMock()
        pool.manifest.opencode.use_session_pool = True
        pool.manifest.agents = {}
        pool.main_agent = MagicMock()
        pool.main_agent.name = "main-agent"
        pool.get_agent = MagicMock()
        return pool

    @pytest.mark.anyio
    async def test_full_session_lifecycle(
        self,
        e2e_pool: MagicMock,
        test_model: TestModel,
        mock_state: MagicMock,
    ) -> None:
        """create → process → close with real SessionPool and TestModel.

        Uses a real Agent (backed by TestModel) so that process_prompt
        actually runs a turn and emits events on the EventBus.
        """
        agent = Agent(name="e2e-agent", model=test_model)
        agent.session_id = "e2e-sess-1"
        e2e_pool.get_agent.return_value = agent

        session_pool = SessionPool(e2e_pool, enable_auto_resume=False)
        await session_pool.start()
        e2e_pool.session_pool = session_pool

        handler = OpenCodeProtocolHandler(agent_pool=e2e_pool, state=mock_state)

        # Create session and send message
        await handler.handle_message("e2e-sess-1", "hello")

        # Give the consumer a moment to process events
        await asyncio.sleep(0.1)

        # Send sentinel to cleanly stop the consumer before close_session
        queue = handler._event_bus_subscriptions.get("e2e-sess-1")
        if queue:
            await queue.put(None)
            await asyncio.sleep(0.1)

        # Clean up
        await handler.close_session("e2e-sess-1")
        await session_pool.shutdown()

        # At minimum we should have received a SessionIdleEvent from
        # StreamCompleteEvent.
        assert mock_state.broadcast_event.await_count >= 1
        last_call = mock_state.broadcast_event.await_args
        assert last_call is not None
        event = last_call[0][0]
        assert isinstance(event, SessionIdleEvent)
        assert event.properties.session_id == "e2e-sess-1"

    @pytest.mark.anyio
    async def test_per_agent_canary_with_real_pool(
        self,
        e2e_pool: MagicMock,
        test_model: TestModel,
        mock_state: MagicMock,
    ) -> None:
        """Per-agent canary flag controls whether SessionPool is used.

        Agent with metadata.use_session_pool=False should raise RuntimeError
        even when global flag is True.
        """
        e2e_pool.manifest.opencode.use_session_pool = True
        cfg = NativeAgentConfig(name="legacy-agent", model="test")
        cfg = cfg.model_copy(update={"metadata": {"use_session_pool": False}})
        e2e_pool.manifest.agents = {"legacy-agent": cfg}

        handler = OpenCodeProtocolHandler(agent_pool=e2e_pool, state=mock_state)
        with pytest.raises(RuntimeError, match="use_session_pool is disabled"):
            await handler.handle_message("sess-1", "hello", agent_name="legacy-agent")

    @pytest.mark.anyio
    async def test_per_agent_canary_enabled_with_real_pool(
        self,
        e2e_pool: MagicMock,
        test_model: TestModel,
        mock_state: MagicMock,
    ) -> None:
        """Agent with metadata.use_session_pool=True uses SessionPool.

        Global flag is False, but per-agent flag overrides it.
        """
        agent = Agent(name="canary-agent", model=test_model)
        agent.session_id = "sess-1"
        e2e_pool.get_agent.return_value = agent
        e2e_pool.manifest.opencode.use_session_pool = False
        cfg = NativeAgentConfig(name="canary-agent", model="test")
        cfg = cfg.model_copy(update={"metadata": {"use_session_pool": True}})
        e2e_pool.manifest.agents = {"canary-agent": cfg}

        session_pool = SessionPool(e2e_pool, enable_auto_resume=False)
        await session_pool.start()
        e2e_pool.session_pool = session_pool

        handler = OpenCodeProtocolHandler(agent_pool=e2e_pool, state=mock_state)
        await handler.handle_message("sess-1", "hello", agent_name="canary-agent")

        # Give the consumer a moment to process events
        await asyncio.sleep(0.1)

        # Send sentinel to cleanly stop the consumer before close_session
        queue = handler._event_bus_subscriptions.get("sess-1")
        if queue:
            await queue.put(None)
            await asyncio.sleep(0.1)

        # Clean up
        await handler.close_session("sess-1")
        await session_pool.shutdown()

        # Should have broadcast at least the idle event
        assert mock_state.broadcast_event.await_count >= 1

    @pytest.mark.anyio
    async def test_event_consumer_receives_real_events(
        self,
        e2e_pool: MagicMock,
        test_model: TestModel,
        mock_state: MagicMock,
    ) -> None:
        """Consumer loop receives and forwards real agent events.

        Verifies that events emitted by a TestModel-backed agent flow
        through the EventBus, into the consumer loop, and are broadcast
        as OpenCode events.
        """
        agent = Agent(name="event-agent", model=test_model)
        agent.session_id = "evt-sess"
        e2e_pool.get_agent.return_value = agent

        session_pool = SessionPool(e2e_pool, enable_auto_resume=False)
        await session_pool.start()
        e2e_pool.session_pool = session_pool

        handler = OpenCodeProtocolHandler(agent_pool=e2e_pool, state=mock_state)

        await handler.handle_message("evt-sess", "ping")
        await asyncio.sleep(0.1)

        # Send sentinel to cleanly stop the consumer before close_session
        queue = handler._event_bus_subscriptions.get("evt-sess")
        if queue:
            await queue.put(None)
            await asyncio.sleep(0.1)

        await handler.close_session("evt-sess")
        await session_pool.shutdown()

        # Collect all broadcast events
        calls = mock_state.broadcast_event.await_args_list
        event_types = [type(c[0][0]).__name__ for c in calls]
        # We expect at least SessionIdleEvent from StreamCompleteEvent
        assert "SessionIdleEvent" in event_types
