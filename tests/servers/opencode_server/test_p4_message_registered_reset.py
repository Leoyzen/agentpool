"""Tests for P4: _message_registered reset after StreamCompleteEvent and RunFailedEvent.

P4 ensures that ``_message_registered[session_id]`` is reset to ``False``
after a turn completes (either via StreamCompleteEvent or RunFailedEvent).
Without this reset, the next RunStartedEvent finds ``_message_registered=True``
and triggers a false "Finalizing incomplete turn" warning (D1 block).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from agentpool.agents.events.events import (
    RunFailedEvent,
    RunStartedEvent,
    StreamCompleteEvent,
)
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventEnvelope
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageWithParts,
)
from agentpool_server.opencode_server.opencode_event_bridge import (
    OpenCodeEventBridgeMixin,
)


pytestmark = pytest.mark.unit


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _async_iter(items: list[Any]) -> AsyncIterator[Any]:
    """Yield items from a list as an async iterator."""
    for item in items:
        yield item


def _make_test_ctx(
    session_id: str = "sess-p4",
    *,
    completed: int | None = None,
    msg_id: str = "msg-assistant-1",
) -> EventProcessorContext:
    """Create an EventProcessorContext with an AssistantMessage for P4 tests."""
    assistant_msg = MessageWithParts.assistant(
        message_id=msg_id,
        session_id=session_id,
        time=MessageTime(created=1000, completed=completed),
        agent_name="test-agent",
        model_id="test-model",
        parent_id=session_id,
        provider_id="test-provider",
        path=MessagePath(cwd="/tmp", root="/tmp"),
        mode="test-agent",
    )
    return EventProcessorContext(
        session_id=session_id,
        assistant_msg_id=msg_id,
        assistant_msg=assistant_msg,
        state=MagicMock(),
        working_dir="/tmp",
    )


class _FakeBridge(OpenCodeEventBridgeMixin):
    """Minimal concrete subclass for testing the mixin."""

    def __init__(self) -> None:
        self.session_pool = MagicMock()
        self.server_state = MagicMock()
        self._contexts: dict[str, Any] = {}
        self._adapters: dict[str, Any] = {}
        self._message_registered: dict[str, bool] = {}
        self._child_to_parent: dict[str, str] = {}
        self._child_spawns: dict[str, Any] = {}
        self._children_of: dict[str, set[str]] = {}
        self._resume_contexts: dict[str, dict[str, Any]] = {}
        self._pending_message_ids: dict[str, str] = {}
        self._pending_message_metadata: dict[str, dict[str, str | None]] = {}
        # set_session_context_data stores into _resume_contexts
        self.set_session_context_data = self._resume_contexts.__setitem__
        self.get_session_context_data = lambda session_id: self._resume_contexts.pop(
            session_id, None
        )


def _setup_bridge_for_handle(
    session_id: str = "sess-p4",
    *,
    completed: int | None = None,
    message_registered: bool = True,
) -> tuple[_FakeBridge, EventProcessorContext, list[Any]]:
    """Set up a _FakeBridge ready for _handle_event calls.

    Returns:
        A tuple of (bridge, ctx, broadcast_calls) where broadcast_calls is
        a list that accumulates all events passed to broadcast_event.
    """
    bridge = _FakeBridge()
    ctx = _make_test_ctx(session_id, completed=completed)

    bridge._contexts[session_id] = ctx
    bridge._message_registered[session_id] = message_registered

    # Adapter mock: convert_event returns empty async iterator
    adapter_mock = MagicMock()
    adapter_mock.convert_event = lambda _e: _async_iter([])
    bridge._adapters[session_id] = adapter_mock

    # Track broadcast_event calls
    broadcast_calls: list[Any] = []

    async def fake_broadcast(event: Any) -> None:
        broadcast_calls.append(event)

    bridge.server_state.broadcast_event = fake_broadcast  # type: ignore[method-assign]
    bridge.server_state.working_dir = "/tmp"
    bridge.server_state.resolve_default_model_info = Mock(
        return_value=("test-model", "test-provider")
    )
    bridge.session_pool.sessions.get_session = Mock(return_value=None)

    return bridge, ctx, broadcast_calls


@pytest.mark.anyio
async def test_message_registered_resets_after_stream_complete() -> None:
    """P4: _message_registered is reset to False after StreamCompleteEvent.

    Given: A session with _message_registered=True (turn in progress).
    When: StreamCompleteEvent is handled by _handle_event.
    Then: _message_registered[session_id] is False.
    """
    session_id = "sess-p4-sc"
    bridge, _ctx, _broadcast_calls = _setup_bridge_for_handle(
        session_id, completed=None, message_registered=True
    )

    event = StreamCompleteEvent(
        message=ChatMessage(content="done", role="assistant"),
        session_id=session_id,
    )
    envelope = EventEnvelope(source_session_id=session_id, event=event)

    with (
        __import__("unittest.mock").mock.patch(
            "agentpool_server.opencode_server.opencode_event_bridge.set_session_status",
            new_callable=AsyncMock,
        ),
        __import__("unittest.mock").mock.patch(
            "agentpool_server.opencode_server.opencode_event_bridge.append_message_to_session",
            new_callable=AsyncMock,
        ),
    ):
        await bridge._handle_event(session_id, envelope)

    assert bridge._message_registered.get(session_id) is False, (
        "_message_registered should be False after StreamCompleteEvent"
    )


@pytest.mark.anyio
async def test_message_registered_resets_after_run_failed() -> None:
    """P4: _message_registered is reset to False after RunFailedEvent.

    Given: A session with _message_registered=True (turn in progress).
    When: RunFailedEvent is handled by _handle_event.
    Then: _message_registered[session_id] is False.
    """
    session_id = "sess-p4-rf"
    bridge, _ctx, _broadcast_calls = _setup_bridge_for_handle(
        session_id, completed=None, message_registered=True
    )

    event = RunFailedEvent(
        run_id="run-fail-1",
        exception=RuntimeError("test failure"),
        session_id=session_id,
    )
    envelope = EventEnvelope(source_session_id=session_id, event=event)

    with (
        __import__("unittest.mock").mock.patch(
            "agentpool_server.opencode_server.opencode_event_bridge.set_session_status",
            new_callable=AsyncMock,
        ),
        __import__("unittest.mock").mock.patch(
            "agentpool_server.opencode_server.opencode_event_bridge.append_message_to_session",
            new_callable=AsyncMock,
        ),
    ):
        await bridge._handle_event(session_id, envelope)

    assert bridge._message_registered.get(session_id) is False, (
        "_message_registered should be False after RunFailedEvent"
    )


@pytest.mark.anyio
async def test_no_finalize_incomplete_turn_warning_on_turn2() -> None:
    """P4: After StreamCompleteEvent resets _message_registered, no D1 warning on turn 2.

    Given: Turn 1 completed via StreamCompleteEvent (resets _message_registered).
    When: RunStartedEvent arrives for turn 2.
    Then: The D1 "finalize incomplete turn" block is NOT entered (no warning logged).
    """
    import agentpool_server.opencode_server.opencode_event_bridge as bridge_module

    session_id = "sess-p4-d1"
    bridge, _ctx, _broadcast_calls = _setup_bridge_for_handle(
        session_id, completed=None, message_registered=True
    )

    # Step 1: Send StreamCompleteEvent to reset _message_registered
    sc_event = StreamCompleteEvent(
        message=ChatMessage(content="done", role="assistant"),
        session_id=session_id,
    )
    sc_envelope = EventEnvelope(source_session_id=session_id, event=sc_event)

    with (
        __import__("unittest.mock").mock.patch(
            "agentpool_server.opencode_server.opencode_event_bridge.set_session_status",
            new_callable=AsyncMock,
        ),
        __import__("unittest.mock").mock.patch(
            "agentpool_server.opencode_server.opencode_event_bridge.append_message_to_session",
            new_callable=AsyncMock,
        ),
    ):
        await bridge._handle_event(session_id, sc_envelope)

    # Verify _message_registered is now False
    assert bridge._message_registered[session_id] is False

    # Step 2: Send RunStartedEvent for turn 2
    rs_event = RunStartedEvent(
        run_id="run-2",
        agent_name="test-agent",
        session_id=session_id,
    )
    rs_envelope = EventEnvelope(source_session_id=session_id, event=rs_event)

    with (
        __import__("unittest.mock").mock.patch(
            "agentpool_server.opencode_server.opencode_event_bridge.set_session_status",
            new_callable=AsyncMock,
        ),
        __import__("unittest.mock").mock.patch(
            "agentpool_server.opencode_server.opencode_event_bridge.append_message_to_session",
            new_callable=AsyncMock,
        ),
        __import__("unittest.mock").mock.patch.object(
            bridge_module.logger, "warning"
        ) as mock_warning,
    ):
        await bridge._handle_event(session_id, rs_envelope)

    # The D1 warning should NOT have been logged because
    # _message_registered was False (P4 reset worked).
    d1_warnings = [
        call
        for call in mock_warning.call_args_list
        if call.args
        and ("incomplete turn" in call.args[0].lower() or "StreamCompleteEvent" in call.args[0])
    ]
    assert len(d1_warnings) == 0, (
        "D1 'finalize incomplete turn' warning should NOT be logged "
        "when _message_registered was properly reset by P4"
    )
