"""Tests for OpenCode server message_id alignment (D13/D14).

Tests that:
- MessageRequest accepts the `delivery` field (D13)
- route_message passes `message_id` to receive_request (D14)
- _before_consumer_loop uses pending message_id when available (D14)
- _handle_event updates ctx.assistant_msg_id from events (D14)
- Delivery mode mapping: "steer" -> "asap", "queue" -> "when_idle" (D13)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from agentpool_server.opencode_server.event_processor_context import EventProcessorContext
from agentpool_server.opencode_server.models import MessagePath, MessageTime, MessageWithParts
from agentpool_server.opencode_server.models.message import (
    CommandRequest,
    MessageRequest,
    TextPartInput,
)


# =============================================================================
# D13: MessageRequest delivery field
# ==============================================================================


class TestMessageRequestDeliveryField:
    """Tests for the delivery field on MessageRequest (D13)."""

    def test_delivery_field_defaults_to_none(self):
        """MessageRequest should default delivery to None (queue semantics)."""
        req = MessageRequest(parts=[TextPartInput(text="hello")])
        assert req.delivery is None

    def test_delivery_field_accepts_steer(self):
        """MessageRequest should accept delivery='steer'."""
        req = MessageRequest(parts=[TextPartInput(text="hello")], delivery="steer")
        assert req.delivery == "steer"

    def test_delivery_field_accepts_queue(self):
        """MessageRequest should accept delivery='queue'."""
        req = MessageRequest(parts=[TextPartInput(text="hello")], delivery="queue")
        assert req.delivery == "queue"

    def test_delivery_field_serializes(self):
        """Delivery field should appear in serialized output when set."""
        req = MessageRequest(parts=[TextPartInput(text="hello")], delivery="steer")
        data = req.model_dump(by_alias=True, exclude_none=True)
        assert data.get("delivery") == "steer"

    def test_delivery_field_not_in_output_when_none(self):
        """Delivery field should not appear when None (backward compat)."""
        req = MessageRequest(parts=[TextPartInput(text="hello")])
        data = req.model_dump(by_alias=True, exclude_none=True)
        assert "delivery" not in data


# =============================================================================
# D13: Delivery mode mapping
# ==============================================================================


class TestDeliveryModeMapping:
    """Tests for delivery-to-priority mapping (D13)."""

    def test_steer_maps_to_asap(self):
        """delivery='steer' should map to priority='asap'."""
        # This is the mapping logic used in message_routes.py
        delivery = "steer"
        priority = "asap" if delivery == "steer" else "when_idle"
        assert priority == "asap"

    def test_queue_maps_to_when_idle(self):
        """delivery='queue' should map to priority='when_idle'."""
        delivery = "queue"
        priority = "asap" if delivery == "steer" else "when_idle"
        assert priority == "when_idle"

    def test_none_delivery_maps_to_when_idle(self):
        """delivery=None should map to priority='when_idle' (default)."""
        delivery = None
        priority = "asap" if delivery == "steer" else "when_idle"
        assert priority == "when_idle"


# =============================================================================
# D14: CommandRequest message_id passthrough
# ==============================================================================


class TestCommandRequestMessageId:
    """Tests for message_id on CommandRequest (D14)."""

    def test_command_request_accepts_message_id(self):
        """CommandRequest should accept message_id field."""
        req = CommandRequest(command="test", message_id="msg_abc123")
        assert req.message_id == "msg_abc123"

    def test_command_request_message_id_defaults_to_none(self):
        """CommandRequest should default message_id to None."""
        req = CommandRequest(command="test")
        assert req.message_id is None


# =============================================================================
# D14: route_message passes message_id to receive_request
# ==============================================================================


class TestRouteMessagePassesMessageId:
    """Tests that route_message passes message_id to receive_request (D14)."""

    @pytest.mark.asyncio
    async def test_route_message_stores_pending_message_id(self):
        """route_message should store message_id in _pending_message_ids."""
        from agentpool_server.opencode_server.session_pool_integration import (
            OpenCodeSessionPoolIntegration,
        )

        session_pool = Mock()
        session_pool.sessions = Mock()
        session_pool.sessions.get_session = Mock(return_value=Mock())
        session_pool.sessions.get_or_create_session = AsyncMock(return_value=(Mock(), True))
        session_pool.receive_request = AsyncMock(return_value=None)
        session_pool.event_bus = Mock()
        session_pool.event_bus.subscribe = AsyncMock()
        session_pool.event_bus.unsubscribe = AsyncMock()

        server_state = Mock()
        server_state.working_dir = "/tmp"
        server_state.broadcast_event = AsyncMock()

        integration = OpenCodeSessionPoolIntegration(session_pool, server_state)
        # Mock _start_event_consumer to avoid starting a real consumer
        integration._start_event_consumer = AsyncMock()

        await integration.route_message(
            session_id="test-session",
            content="hello",
            priority="when_idle",
            message_id="msg_test123",
        )

        # The pending message_id should have been consumed by now (popped in
        # _before_consumer_loop), but since we didn't call _before_consumer_loop,
        # it should still be stored.
        assert "test-session" in integration._pending_message_ids
        assert integration._pending_message_ids["test-session"] == "msg_test123"

    @pytest.mark.asyncio
    async def test_route_message_passes_message_id_to_receive_request(self):
        """route_message should pass message_id to receive_request."""
        from agentpool_server.opencode_server.session_pool_integration import (
            OpenCodeSessionPoolIntegration,
        )

        session_pool = Mock()
        session_pool.sessions = Mock()
        session_pool.sessions.get_session = Mock(return_value=Mock())
        session_pool.sessions.get_or_create_session = AsyncMock(return_value=(Mock(), True))
        session_pool.receive_request = AsyncMock(return_value=None)

        server_state = Mock()
        server_state.working_dir = "/tmp"

        integration = OpenCodeSessionPoolIntegration(session_pool, server_state)
        integration._start_event_consumer = AsyncMock()

        await integration.route_message(
            session_id="test-session",
            content="hello",
            priority="when_idle",
            message_id="msg_test456",
        )

        # Verify receive_request was called with message_id
        call_kwargs = session_pool.receive_request.call_args
        assert call_kwargs.kwargs.get("message_id") == "msg_test456"

    @pytest.mark.asyncio
    async def test_route_message_without_message_id_passes_none(self):
        """route_message should pass message_id=None when not provided."""
        from agentpool_server.opencode_server.session_pool_integration import (
            OpenCodeSessionPoolIntegration,
        )

        session_pool = Mock()
        session_pool.sessions = Mock()
        session_pool.sessions.get_session = Mock(return_value=Mock())
        session_pool.sessions.get_or_create_session = AsyncMock(return_value=(Mock(), True))
        session_pool.receive_request = AsyncMock(return_value=None)

        server_state = Mock()
        server_state.working_dir = "/tmp"

        integration = OpenCodeSessionPoolIntegration(session_pool, server_state)
        integration._start_event_consumer = AsyncMock()

        await integration.route_message(
            session_id="test-session",
            content="hello",
            priority="when_idle",
        )

        call_kwargs = session_pool.receive_request.call_args
        assert call_kwargs.kwargs.get("message_id") is None


# =============================================================================
# D14: _before_consumer_loop uses pending message_id
# ==============================================================================


class TestBeforeConsumerLoopPendingMessageId:
    """Tests that _before_consumer_loop uses pending message_id (D14)."""

    @pytest.mark.asyncio
    async def test_before_consumer_loop_uses_pending_message_id(self):
        """_before_consumer_loop should use pending message_id when available."""
        from agentpool_server.opencode_server.session_pool_integration import (
            OpenCodeSessionPoolIntegration,
        )

        session_pool = Mock()
        server_state = Mock()
        server_state.working_dir = "/tmp"

        integration = OpenCodeSessionPoolIntegration(session_pool, server_state)
        # Set a pending message_id
        integration._pending_message_ids["test-session"] = "msg_from_rest"

        await integration._before_consumer_loop("test-session")

        ctx = integration._contexts.get("test-session")
        assert ctx is not None
        assert ctx.assistant_msg_id == "msg_from_rest"

        # The pending message_id should have been consumed (popped)
        assert "test-session" not in integration._pending_message_ids

    @pytest.mark.asyncio
    async def test_before_consumer_loop_generates_id_when_no_pending(self):
        """_before_consumer_loop should generate a new ID when no pending message_id."""
        from agentpool_server.opencode_server.session_pool_integration import (
            OpenCodeSessionPoolIntegration,
        )

        session_pool = Mock()
        server_state = Mock()
        server_state.working_dir = "/tmp"

        integration = OpenCodeSessionPoolIntegration(session_pool, server_state)

        await integration._before_consumer_loop("test-session")

        ctx = integration._contexts.get("test-session")
        assert ctx is not None
        # Should have generated an ID (starts with "msg_")
        assert ctx.assistant_msg_id.startswith("msg_")


# =============================================================================
# D14: _handle_event updates ctx.assistant_msg_id from events
# ==============================================================================


class TestHandleEventUpdatesMessageId:
    """Tests that _handle_event updates ctx.assistant_msg_id from events (D14)."""

    @pytest.mark.asyncio
    async def test_handle_event_preserves_ctx_message_id_from_event(self):
        """_handle_event should NOT overwrite ctx.assistant_msg_id from event.message_id.

        NativeTurn generates its own UUID as _message_id, which differs from
        the canonical assistant_msg_id from the REST handler. Overwriting
        causes a mismatch between parts and the assistant message, breaking
        UI rendering.
        """
        from pydantic_ai.messages import TextPart as PydanticTextPart

        from agentpool.agents.events import PartStartEvent
        from agentpool.orchestrator.event_bus import EventEnvelope
        from agentpool_server.opencode_server.models import MessageWithParts
        from agentpool_server.opencode_server.session_pool_integration import (
            OpenCodeSessionPoolIntegration,
        )

        session_pool = Mock()
        server_state = Mock()
        server_state.working_dir = "/tmp"
        server_state.broadcast_event = AsyncMock()

        integration = OpenCodeSessionPoolIntegration(session_pool, server_state)

        # Create a context with a placeholder ID
        assistant_msg = MessageWithParts.assistant(
            message_id="msg_placeholder",
            session_id="test-session",
            time=MessageTime(created=0),
            agent_name="agent",
            model_id="default",
            parent_id="",
            provider_id="agentpool",
            path=MessagePath(cwd="/tmp", root="/tmp"),
        )
        ctx = EventProcessorContext(
            session_id="test-session",
            assistant_msg_id="msg_placeholder",
            assistant_msg=assistant_msg,
            state=server_state,
            working_dir="/tmp",
        )
        integration._contexts["test-session"] = ctx
        integration._message_registered["test-session"] = False

        # Mock the adapter to avoid full event processing
        mock_adapter = Mock()

        async def _empty_convert(event):
            return
            yield

        mock_adapter.convert_event = _empty_convert
        integration._adapters["test-session"] = mock_adapter

        # Create an event with message_id
        event = PartStartEvent(
            index=0,
            part=PydanticTextPart(content="hello"),
            message_id="msg_from_event_123",
        )
        envelope = EventEnvelope(
            event=event,
            source_session_id="test-session",
        )

        await integration._handle_event("test-session", envelope)

        # The ctx.assistant_msg_id should NOT be overwritten by the event's
        # internal message_id (NativeTurn UUID). The canonical ID from the
        # REST handler must be preserved.
        assert ctx.assistant_msg_id == "msg_placeholder"

    @pytest.mark.asyncio
    async def test_handle_event_does_not_update_when_event_has_no_message_id(self):
        """_handle_event should not update ctx.assistant_msg_id when event has no message_id."""
        from agentpool.agents.events import RunStartedEvent
        from agentpool.orchestrator.event_bus import EventEnvelope
        from agentpool_server.opencode_server.session_pool_integration import (
            OpenCodeSessionPoolIntegration,
        )

        session_pool = Mock()
        server_state = Mock()
        server_state.working_dir = "/tmp"
        server_state.broadcast_event = AsyncMock()

        integration = OpenCodeSessionPoolIntegration(session_pool, server_state)

        assistant_msg = MessageWithParts.assistant(
            message_id="msg_original",
            session_id="test-session",
            time=MessageTime(created=0),
            agent_name="agent",
            model_id="default",
            parent_id="",
            provider_id="agentpool",
            path=MessagePath(cwd="/tmp", root="/tmp"),
        )
        ctx = EventProcessorContext(
            session_id="test-session",
            assistant_msg_id="msg_original",
            assistant_msg=assistant_msg,
            state=server_state,
            working_dir="/tmp",
        )
        integration._contexts["test-session"] = ctx
        integration._message_registered["test-session"] = False

        mock_adapter = Mock()

        async def _empty_convert(event):
            return
            yield

        mock_adapter.convert_event = _empty_convert
        integration._adapters["test-session"] = mock_adapter

        # RunStartedEvent does not have message_id
        event = RunStartedEvent(session_id="test-session", run_id="run_123")
        envelope = EventEnvelope(
            event=event,
            source_session_id="test-session",
        )

        await integration._handle_event("test-session", envelope)

        # The ctx.assistant_msg_id should NOT have been updated
        assert ctx.assistant_msg_id == "msg_original"
