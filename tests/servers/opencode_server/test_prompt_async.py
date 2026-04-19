"""Regression tests for async prompt handling in OpenCode server."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest

from agentpool_server.opencode_server.models import MessageRequest, TextPartInput
from agentpool_server.opencode_server.models.common import TimeCreated
from agentpool_server.opencode_server.models.events import SessionIdleEvent, SessionStatusEvent
from agentpool_server.opencode_server.models.message import MessageWithParts, UserMessage
from agentpool_server.opencode_server.routes import message_routes


if TYPE_CHECKING:
    from collections.abc import Awaitable


def async_mock_return_value(value):
    """Create an async function that returns *value*, suitable for monkeypatching."""

    async def _mock(*args, **kwargs):
        return value

    return _mock


class TestPromptAsync:
    """Tests for `/prompt_async` session serialization."""

    @pytest.mark.asyncio
    async def test_prompt_async_marks_busy_before_scheduling(
        self,
        async_client,
        server_state,
    ) -> None:
        """The first async prompt should lock the session before scheduling work."""
        response = await async_client.post("/session", json={"title": "Async Lock"})
        session_id = response.json()["id"]

        background_calls: list[str | None] = []

        def fake_create_background_task(coro, *, name=None):
            background_calls.append(name)
            coro.close()
            task = Mock()
            task.get_name.return_value = name
            task.done.return_value = False
            server_state.background_tasks.add(task)
            return task

        server_state.create_background_task = Mock(side_effect=fake_create_background_task)

        request = MessageRequest(
            parts=[TextPartInput(text="first")],
            agent="default",
            message_id="msg-1",
        )
        response = await async_client.post(
            f"/session/{session_id}/prompt_async",
            json=request.model_dump(mode="json"),
        )
        assert response.status_code == 204
        assert server_state.session_status[session_id].type == "busy"
        assert server_state.create_background_task.call_count == 1

        second_request = MessageRequest(
            parts=[TextPartInput(text="second")],
            agent="default",
            message_id="msg-2",
        )
        response = await async_client.post(
            f"/session/{session_id}/prompt_async",
            json=second_request.model_dump(mode="json"),
        )
        assert response.status_code == 204
        assert server_state.create_background_task.call_count == 1
        assert background_calls == [f"process_message_{session_id}"]
        assert len(server_state.pending_async_prompts[session_id]) == 2

    @pytest.mark.asyncio
    async def test_prompt_async_drains_server_queue_in_order(
        self,
        async_client,
        server_state,
        monkeypatch,
    ) -> None:
        """Queued async prompts should be processed FIFO by one background worker."""
        response = await async_client.post("/session", json={"title": "Async Queue"})
        session_id = response.json()["id"]

        processed: list[str] = []
        drained = asyncio.Event()

        async def fake_process_message_locked(
            session_id: str,
            request: MessageRequest,
            state,
            user_msg_id: str,
            user_msg_with_parts,
            *,
            mark_busy: bool = True,
            mark_idle: bool = True,
        ):
            processed.append(request.parts[0].text)
            if len(processed) == 2:
                drained.set()
            return user_msg_with_parts

        monkeypatch.setattr(
            message_routes,
            "_process_message_locked",
            fake_process_message_locked,
        )

        first_request = MessageRequest(
            parts=[TextPartInput(text="first")],
            agent="default",
            message_id="msg-1",
        )
        second_request = MessageRequest(
            parts=[TextPartInput(text="second")],
            agent="default",
            message_id="msg-2",
        )

        first_response = await async_client.post(
            f"/session/{session_id}/prompt_async",
            json=first_request.model_dump(mode="json"),
        )
        second_response = await async_client.post(
            f"/session/{session_id}/prompt_async",
            json=second_request.model_dump(mode="json"),
        )

        assert first_response.status_code == 204
        assert second_response.status_code == 204

        await asyncio.wait_for(drained.wait(), timeout=1.0)
        await asyncio.sleep(0)

        assert processed == ["first", "second"]
        assert session_id not in server_state.pending_async_prompts
        assert server_state.session_status[session_id].type == "idle"

    @pytest.mark.asyncio
    async def test_prompt_async_emits_turn_complete_between_queued_prompts(
        self,
        server_state,
        monkeypatch,
    ) -> None:
        """Queued prompts should emit a turn-complete idle signal between turns."""
        session = await server_state.ensure_session("async-turn-complete")
        session_id = session.id

        event_types: list[str] = []

        original_broadcast = server_state.broadcast_event

        async def tracking_broadcast(event) -> None:
            if isinstance(event, SessionStatusEvent):
                event_types.append(f"status:{event.properties.status.type}")
            elif isinstance(event, SessionIdleEvent):
                event_types.append("session.idle")
            await original_broadcast(event)

        server_state.broadcast_event = tracking_broadcast  # type: ignore[method-assign]

        for idx in range(2):
            request = MessageRequest(
                parts=[TextPartInput(text=f"prompt-{idx}")],
                agent="default",
                message_id=f"msg-{idx}",
            )
            queued_user = UserMessage(
                id=f"msg-{idx}",
                session_id=session_id,
                time=TimeCreated(created=idx),
                agent="default",
                model=None,
            )
            server_state.enqueue_async_prompt(
                session_id,
                message_routes.QueuedAsyncPrompt(
                    request=request,
                    user_msg_id=f"msg-{idx}",
                    user_msg_with_parts=MessageWithParts(info=queued_user),
                ),
            )

        server_state.session_status[session_id] = message_routes.SessionStatus(type="busy")

        async def fake_process_message_locked(
            session_id: str,
            request: MessageRequest,
            state,
            user_msg_id: str,
            user_msg_with_parts,
            *,
            mark_busy: bool = True,
            mark_idle: bool = True,
        ):
            return user_msg_with_parts

        monkeypatch.setattr(message_routes, "_process_message_locked", fake_process_message_locked)

        await message_routes._run_async_prompt_queue(session_id, server_state)

        assert event_types.count("session.idle") == 2
        assert event_types == ["session.idle", "status:idle", "session.idle"]

    @pytest.mark.asyncio
    async def test_ensure_async_prompt_worker_starts_worker_for_queued_prompts(
        self,
        server_state,
    ) -> None:
        """Queued async prompts should start a worker when a turn hands off."""
        session = await server_state.ensure_session("sync-handoff")
        session_id = session.id

        request = MessageRequest(
            parts=[TextPartInput(text="queued")],
            agent="default",
            message_id="queued-msg",
        )
        queued_user = UserMessage(
            id="queued-msg",
            session_id=session_id,
            time=TimeCreated(created=0),
            agent="default",
            model=None,
        )
        server_state.enqueue_async_prompt(
            session_id,
            message_routes.QueuedAsyncPrompt(
                request=request,
                user_msg_id="queued-msg",
                user_msg_with_parts=MessageWithParts(info=queued_user),
            ),
        )

        started_workers: list[str | None] = []

        def fake_create_background_task(coro: Awaitable[object], *, name: str | None = None):
            started_workers.append(name)
            coro.close()
            return Mock()

        server_state.create_background_task = fake_create_background_task  # type: ignore[method-assign]

        await message_routes._ensure_async_prompt_worker(session_id, server_state, mark_busy=True)

        assert started_workers == [f"process_message_{session_id}"]
        assert server_state.session_status[session_id].type == "busy"

    @pytest.mark.asyncio
    async def test_handoff_skips_idle_when_async_prompts_queued(
        self,
        server_state,
        monkeypatch,
    ) -> None:
        """When mark_idle=True and async prompts are queued with no worker,
        skip idle→busy flicker."""
        session = await server_state.ensure_session("handoff-flicker")
        session_id = session.id

        # Enqueue a pending async prompt so has_pending_async_prompts returns True.
        request = MessageRequest(
            parts=[TextPartInput(text="queued")],
            agent="default",
            message_id="msg-queued",
        )
        queued_user = UserMessage(
            id="msg-queued",
            session_id=session_id,
            time=TimeCreated(created=0),
            agent="default",
            model=None,
        )
        server_state.enqueue_async_prompt(
            session_id,
            message_routes.QueuedAsyncPrompt(
                request=request,
                user_msg_id="msg-queued",
                user_msg_with_parts=MessageWithParts(info=queued_user),
            ),
        )

        # Track status/idle events.
        event_types: list[str] = []
        original_broadcast = server_state.broadcast_event

        async def tracking_broadcast(event) -> None:
            if isinstance(event, SessionStatusEvent):
                event_types.append(f"status:{event.properties.status.type}")
            elif isinstance(event, SessionIdleEvent):
                event_types.append("session.idle")
            await original_broadcast(event)

        server_state.broadcast_event = tracking_broadcast  # type: ignore[method-assign]

        user_msg = UserMessage(
            id="msg-handoff",
            session_id=session_id,
            time=TimeCreated(created=0),
            agent="default",
            model=None,
        )
        msg_with_parts = MessageWithParts(info=user_msg)

        # Mock the agent's run_stream to yield nothing (empty response).
        async def empty_run_stream(*args, **kwargs):
            return
            yield  # noqa: unreachable — makes this an async generator

        server_state.agent.run_stream = empty_run_stream  # type: ignore[assignment]

        # Mock extract_user_prompt_from_parts to return a simple text prompt.
        monkeypatch.setattr(
            message_routes,
            "extract_user_prompt_from_parts",
            async_mock_return_value(["hello"]),
        )

        # Prevent background tasks (title gen, async prompt worker) from running.
        worker_names: list[str | None] = []

        def fake_create_background_task(coro, *, name=None):
            worker_names.append(name)
            coro.close()
            return Mock()

        server_state.create_background_task = fake_create_background_task  # type: ignore[method-assign]

        assert not server_state.has_session_background_task(session_id)

        # Call the REAL _process_message_locked — the handoff logic at
        # lines 487-497 will execute against actual state methods.
        await message_routes._process_message_locked(
            session_id,
            request,
            server_state,
            "msg-handoff",
            msg_with_parts,
            mark_busy=True,
            mark_idle=True,
        )

        # No status:idle should appear — the real handoff code skipped
        # mark_session_idle and went straight to _ensure_async_prompt_worker.
        assert "status:idle" not in event_types, f"Expected no status:idle but got {event_types}"
        # The async prompt worker should have been started.
        assert f"process_message_{session_id}" in worker_names

    @pytest.mark.asyncio
    async def test_handoff_emits_idle_when_no_async_prompts_queued(
        self,
        server_state,
        monkeypatch,
    ) -> None:
        """When mark_idle=True and no async prompts are queued, idle is emitted normally."""
        session = await server_state.ensure_session("handoff-idle-normal")
        session_id = session.id

        event_types: list[str] = []
        original_broadcast = server_state.broadcast_event

        async def tracking_broadcast(event) -> None:
            if isinstance(event, SessionStatusEvent):
                event_types.append(f"status:{event.properties.status.type}")
            elif isinstance(event, SessionIdleEvent):
                event_types.append("session.idle")
            await original_broadcast(event)

        server_state.broadcast_event = tracking_broadcast  # type: ignore[method-assign]

        request = MessageRequest(
            parts=[TextPartInput(text="hello")],
            agent="default",
            message_id="msg-idle",
        )
        user_msg = UserMessage(
            id="msg-idle",
            session_id=session_id,
            time=TimeCreated(created=0),
            agent="default",
            model=None,
        )
        msg_with_parts = MessageWithParts(info=user_msg)

        # Mock the agent's run_stream to yield nothing (empty response).
        async def empty_run_stream(*args, **kwargs):
            return
            yield  # noqa: unreachable — makes this an async generator

        server_state.agent.run_stream = empty_run_stream  # type: ignore[assignment]

        # Mock extract_user_prompt_from_parts to return a simple text prompt.
        monkeypatch.setattr(
            message_routes,
            "extract_user_prompt_from_parts",
            async_mock_return_value(["hello"]),
        )

        # Prevent background tasks from running.
        worker_names: list[str | None] = []

        def fake_create_background_task(coro, *, name=None):
            worker_names.append(name)
            coro.close()
            return Mock()

        server_state.create_background_task = fake_create_background_task  # type: ignore[method-assign]

        assert not server_state.has_pending_async_prompts(session_id)

        # Call the REAL _process_message_locked.
        await message_routes._process_message_locked(
            session_id,
            request,
            server_state,
            "msg-idle",
            msg_with_parts,
            mark_busy=True,
            mark_idle=True,
        )

        # The real mark_session_idle emits SessionStatusEvent(idle) then
        # SessionIdleEvent, so the full sequence is:
        # status:busy (mark_busy) → status:idle (mark_session_idle) → session.idle
        assert event_types == ["status:busy", "status:idle", "session.idle"]
        # No async prompt worker started — nothing was queued.
        assert f"process_message_{session_id}" not in worker_names

    @pytest.mark.asyncio
    async def test_snapshot_binds_resolved_agent(
        self,
        server_state,
    ) -> None:
        """snapshot_for_session(agent=...) must bind the resolved agent, not self.agent."""
        session_id = "snapshot-resolved"

        # Create an alternate agent that differs from the default.
        alt_agent = Mock()
        alt_agent.model_name = "alt-model-v2"
        alt_agent._input_provider = None
        alt_agent._current_mode = "reasoning"

        # Ensure the default agent also has model_name so the comparison is meaningful.
        server_state.agent.model_name = "default-model"

        snapshot = await server_state.snapshot_for_session(session_id, agent=alt_agent)

        # The snapshot must reflect the alternate agent, not the default.
        assert snapshot.model_name == "alt-model-v2"
        assert snapshot.mode_name == "reasoning"
        # The alternate agent must have received the input provider and session id.
        assert alt_agent._input_provider is server_state.input_providers[session_id]
        assert alt_agent.session_id == session_id
        # The default agent must NOT have been touched.
        assert server_state.agent._input_provider is None

    @pytest.mark.asyncio
    async def test_snapshot_default_agent_unchanged(
        self,
        server_state,
    ) -> None:
        """snapshot_for_session(session_id) without agent= still binds self.agent."""
        session_id = "snapshot-default"

        server_state.agent.model_name = "default-model"

        snapshot = await server_state.snapshot_for_session(session_id)

        # The snapshot must reflect the default agent.
        assert snapshot.model_name == "default-model"
        # The default agent must have received the input provider and session id.
        assert server_state.agent._input_provider is server_state.input_providers[session_id]
        assert server_state.agent.session_id == session_id

    @pytest.mark.asyncio
    async def test_snapshot_mode_name_after_mutation(
        self,
        server_state,
        monkeypatch,
    ) -> None:
        """Snapshot must reflect mode_name AFTER set_mode mutation, not before."""
        from agentpool_server.opencode_server.models.common import ModelRef

        session = await server_state.ensure_session("snapshot-mode-mutation")
        session_id = session.id

        # Set up agent with initial mode
        server_state.agent.model_name = "test-model"
        server_state.agent._current_mode = "low"

        # Make set_mode actually mutate _current_mode on the mock agent
        original_set_mode = server_state.agent.set_mode

        async def fake_set_mode(variant: str, **kwargs: object) -> None:
            server_state.agent._current_mode = variant

        server_state.agent.set_mode = fake_set_mode
        server_state.agent.get_available_models = AsyncMock(return_value=[])

        # Mock run_stream and extract to allow _process_message_locked to complete
        async def empty_run_stream(*args, **kwargs):
            return
            yield  # noqa: unreachable — makes this an async generator

        server_state.agent.run_stream = empty_run_stream  # type: ignore[assignment]

        monkeypatch.setattr(
            message_routes,
            "extract_user_prompt_from_parts",
            async_mock_return_value(["hello"]),
        )

        # Prevent background tasks from running
        def fake_create_background_task(coro, *, name=None):
            coro.close()
            return Mock()

        server_state.create_background_task = fake_create_background_task  # type: ignore[method-assign]

        request = MessageRequest(
            parts=[TextPartInput(text="hello")],
            agent=None,
            message_id="msg-mutation",
            model=ModelRef(provider_id="test", model_id="test-model", variant="high"),
        )
        user_msg = UserMessage(
            id="msg-mutation",
            session_id=session_id,
            time=TimeCreated(created=0),
            agent="default",
            model=request.model,
        )
        msg_with_parts = MessageWithParts(info=user_msg)

        # We need to capture the snapshot that _process_message_locked uses.
        # Intercept snapshot_for_session to capture the returned snapshot.
        captured_snapshot = None
        original_snapshot = server_state.snapshot_for_session

        async def capturing_snapshot(*args, **kwargs):
            nonlocal captured_snapshot
            snap = await original_snapshot(*args, **kwargs)
            captured_snapshot = snap
            return snap

        server_state.snapshot_for_session = capturing_snapshot  # type: ignore[method-assign]

        await message_routes._process_message_locked(
            session_id,
            request,
            server_state,
            "msg-mutation",
            msg_with_parts,
            mark_busy=True,
            mark_idle=True,
        )

        # The snapshot must reflect the post-mutation mode_name
        assert captured_snapshot is not None
        assert captured_snapshot.mode_name == "high", (
            f"Expected mode_name='high' but got '{captured_snapshot.mode_name}'"
        )

    @pytest.mark.asyncio
    async def test_async_queue_survives_prompt_failure(
        self,
        server_state,
        monkeypatch,
    ) -> None:
        """A single prompt failure should not kill remaining queued prompts."""
        session = await server_state.ensure_session("queue-resilience")
        session_id = session.id

        processed: list[str] = []
        call_count = 0

        async def fake_process_message_locked(
            session_id: str,
            request: MessageRequest,
            state,
            user_msg_id: str,
            user_msg_with_parts,
            *,
            mark_busy: bool = True,
            mark_idle: bool = True,
        ):
            nonlocal call_count
            call_count += 1
            text = request.parts[0].text
            # Make the 2nd call fail
            if call_count == 2:
                msg = "Simulated prompt failure"
                raise RuntimeError(msg)
            processed.append(text)
            return user_msg_with_parts

        monkeypatch.setattr(
            message_routes,
            "_process_message_locked",
            fake_process_message_locked,
        )

        # Enqueue 3 prompts
        for idx in range(3):
            request = MessageRequest(
                parts=[TextPartInput(text=f"prompt-{idx}")],
                agent="default",
                message_id=f"msg-{idx}",
            )
            queued_user = UserMessage(
                id=f"msg-{idx}",
                session_id=session_id,
                time=TimeCreated(created=idx),
                agent="default",
                model=None,
            )
            server_state.enqueue_async_prompt(
                session_id,
                message_routes.QueuedAsyncPrompt(
                    request=request,
                    user_msg_id=f"msg-{idx}",
                    user_msg_with_parts=MessageWithParts(info=queued_user),
                ),
            )

        server_state.session_status[session_id] = message_routes.SessionStatus(type="busy")

        # Run the queue worker
        await message_routes._run_async_prompt_queue(session_id, server_state)

        # Prompts 1 and 3 should have been processed (2nd failed but queue continued)
        assert processed == ["prompt-0", "prompt-2"], f"Expected ['prompt-0', 'prompt-2'] but got {processed}"
        # Session should end idle
        assert server_state.session_status[session_id].type == "idle"
        # No orphaned prompts
        assert not server_state.has_pending_async_prompts(session_id)

    @pytest.mark.asyncio
    async def test_handoff_no_idle_when_worker_running(
        self,
        server_state,
        monkeypatch,
    ) -> None:
        """When has_queued=True AND has_worker=True, no idle status is emitted."""
        session = await server_state.ensure_session("handoff-worker-running")
        session_id = session.id

        # Enqueue a pending async prompt so has_pending_async_prompts returns True.
        request = MessageRequest(
            parts=[TextPartInput(text="queued")],
            agent="default",
            message_id="msg-queued",
        )
        queued_user = UserMessage(
            id="msg-queued",
            session_id=session_id,
            time=TimeCreated(created=0),
            agent="default",
            model=None,
        )
        server_state.enqueue_async_prompt(
            session_id,
            message_routes.QueuedAsyncPrompt(
                request=request,
                user_msg_id="msg-queued",
                user_msg_with_parts=MessageWithParts(info=queued_user),
            ),
        )

        # Track status/idle events.
        event_types: list[str] = []
        original_broadcast = server_state.broadcast_event

        async def tracking_broadcast(event) -> None:
            if isinstance(event, SessionStatusEvent):
                event_types.append(f"status:{event.properties.status.type}")
            elif isinstance(event, SessionIdleEvent):
                event_types.append("session.idle")
            await original_broadcast(event)

        server_state.broadcast_event = tracking_broadcast  # type: ignore[method-assign]

        user_msg = UserMessage(
            id="msg-handoff",
            session_id=session_id,
            time=TimeCreated(created=0),
            agent="default",
            model=None,
        )
        msg_with_parts = MessageWithParts(info=user_msg)

        # Mock the agent's run_stream to yield nothing (empty response).
        async def empty_run_stream(*args, **kwargs):
            return
            yield  # noqa: unreachable — makes this an async generator

        server_state.agent.run_stream = empty_run_stream  # type: ignore[assignment]

        # Mock extract_user_prompt_from_parts to return a simple text prompt.
        monkeypatch.setattr(
            message_routes,
            "extract_user_prompt_from_parts",
            async_mock_return_value(["hello"]),
        )

        # Simulate a running worker by creating a mock task with the expected name.
        worker_names: list[str | None] = []

        def fake_create_background_task(coro, *, name=None):
            worker_names.append(name)
            coro.close()
            task = Mock()
            task.get_name.return_value = name
            task.done.return_value = False
            server_state.background_tasks.add(task)
            return task

        server_state.create_background_task = fake_create_background_task  # type: ignore[method-assign]

        # Simulate that a worker is already running
        mock_task = Mock()
        mock_task.get_name.return_value = f"process_message_{session_id}"
        mock_task.done.return_value = False
        server_state.background_tasks.add(mock_task)

        assert server_state.has_session_background_task(session_id)

        # Call the REAL _process_message_locked — the handoff logic will
        # execute with has_queued=True AND has_worker=True.
        await message_routes._process_message_locked(
            session_id,
            request,
            server_state,
            "msg-handoff",
            msg_with_parts,
            mark_busy=True,
            mark_idle=True,
        )

        # No idle should appear — the existing worker will drain the queue
        assert "status:idle" not in event_types, f"Expected no status:idle but got {event_types}"
        assert "session.idle" not in event_types, f"Expected no session.idle but got {event_types}"
        # No new worker should be started since one is already running
        assert f"process_message_{session_id}" not in worker_names
