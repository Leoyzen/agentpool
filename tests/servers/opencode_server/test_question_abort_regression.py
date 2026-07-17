"""Regression tests for question abort → agent state corruption → agent_lock deadlock.

Two user-facing bugs:

1. **TUI black screen**: After user aborts a question (ESC), sends a new message,
   and the agent asks another question, the TUI goes black. Root cause:
   `RunAbortedError` from `question_for_user` is NOT handled like `CancelledError`
   in `_process_message_locked`, so the aborted assistant message is never added
   to the agent's conversation history. The LLM then receives corrupted history
   (partial tool call without result) on the next run.

2. **Can't send messages after restart**: When the agent is blocked waiting for a
   question answer (Future.await), `agent_lock` is still held. If the TUI
   disconnects (user closes opencode), the question is never answered and
   `agent_lock` is never released. On reconnect, ANY request that needs
   `agent_lock` (including `get_or_load_session`, which every message endpoint
   calls) will deadlock.

These tests verify both bugs can be reproduced and will serve as red-flag
regression guards once the fix lands.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agentpool.tasks.exceptions import RunAbortedError
from agentpool.utils import identifiers as identifier
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.models import (
    AssistantMessage,
    MessageRequest,
    TextPartInput,
    TimeCreated,
    UserMessage,
)
from agentpool_server.opencode_server.models.message import (
    MessageAbortedError,
    MessageWithParts,
)
from agentpool_server.opencode_server.routes.message_routes import _process_message_locked
from agentpool_server.opencode_server.session_pool_integration import (
    append_message_to_session,
    get_messages_for_session,
    get_session_status,
)
from agentpool_server.opencode_server.state import PendingQuestion, ServerState


# ---------------------------------------------------------------------------
# Mock agents that simulate the question abort scenarios
# ---------------------------------------------------------------------------


class RunAbortedAgentMock:
    """Mock agent that raises RunAbortedError during run_stream.

    Simulates: question_for_user tool raises RunAbortedError when user
    cancels the questionnaire (ESC in TUI).
    """

    def __init__(self) -> None:
        self.name = "test-agent"
        self.run_stream_call_count = 0
        self.agent_pool: Mock | None = None
        self.host_context: Mock | None = None
        self.env: Mock | None = None
        self.storage: Any = None
        self.tools: list[Any] = []
        self._input_provider = None
        self.model_name = "test-model"
        self.session_id: str | None = None
        from agentpool.messaging.message_history import MessageHistory

        self.conversation = MessageHistory()

    async def set_model(self, model: str) -> None:
        self.model_name = model

    async def set_mode(self, mode: str, category_id: str | None = None) -> None:
        pass

    async def get_available_models(self):
        return []

    async def load_session(self, session_id: str) -> None:
        return None

    def run_stream(self, *args: Any, **kwargs: Any):
        self.run_stream_call_count += 1

        async def stream():
            # Simulate: agent starts streaming, calls question_for_user,
            # which raises RunAbortedError when user cancels.
            raise RunAbortedError("User cancelled the questionnaire")
            yield

        return stream()


class BlockingOnQuestionAgentMock:
    """Mock agent that blocks forever waiting for a question answer.

    Simulates: agent calls question_for_user, which creates a Future
    and awaits it. The Future is never resolved, so agent_lock stays held.
    """

    def __init__(self) -> None:
        self.name = "test-agent"
        self.run_stream_call_count = 0
        self.agent_pool: Mock | None = None
        self.host_context: Mock | None = None
        self.env: Mock | None = None
        self.storage: Any = None
        self.tools: list[Any] = []
        self._input_provider = None
        self.model_name = "test-model"
        self.session_id: str | None = None
        self.block_forever_event: asyncio.Event = asyncio.Event()
        from agentpool.messaging.message_history import MessageHistory

        self.conversation = MessageHistory()

    async def set_model(self, model: str) -> None:
        self.model_name = model

    async def set_mode(self, mode: str, category_id: str | None = None) -> None:
        pass

    async def get_available_models(self):
        return []

    async def load_session(self, session_id: str) -> None:
        return None

    def run_stream(self, *args: Any, **kwargs: Any):
        self.run_stream_call_count += 1

        async def stream():
            # Simulate: agent blocks waiting for question answer (Future never resolves).
            # In real code this is: answers = await future  (in input_provider.py:344)
            # Here we just wait on an Event that nobody will set.
            await self.block_forever_event.wait()
            if False:
                yield None

        return stream()


class BlockingOnRealQuestionAgentMock:
    """Mock agent that creates a real PendingQuestion and blocks on its Future.

    Unlike BlockingOnQuestionAgentMock which blocks on an Event, this creates
    an actual PendingQuestion in session.pending_questions and awaits the Future.
    This simulates the real question_for_user flow more accurately, allowing
    tests to verify that cancel_all_pending_questions() releases agent_lock.
    """

    def __init__(self, state: ServerState) -> None:
        self.name = "test-agent"
        self.run_stream_call_count = 0
        self.agent_pool: Mock | None = None
        self.host_context: Mock | None = None
        self.env: Mock | None = None
        self.storage: Any = None
        self.tools: list[Any] = []
        self._input_provider = None
        self.model_name = "test-model"
        self.session_id: str | None = None
        self._state = state
        from agentpool.messaging.message_history import MessageHistory

        self.conversation = MessageHistory()

    async def set_model(self, model: str) -> None:
        self.model_name = model

    async def set_mode(self, mode: str, category_id: str | None = None) -> None:
        pass

    async def get_available_models(self):
        return []

    async def load_session(self, session_id: str) -> None:
        return None

    def run_stream(self, *args: Any, session_id: str | None = None, **kwargs: Any):
        self.run_stream_call_count += 1
        state = self._state
        _session_id = session_id or "unknown"

        async def stream():
            # Simulate: agent calls question_for_user → input_provider.get_elicitation()
            # creates a PendingQuestion and awaits the Future.
            yield None  # Ensure async for starts executing the generator body
            question_id = f"que_test_{id(self)}"
            future: asyncio.Future[list[list[str]]] = asyncio.get_event_loop().create_future()
            # Store on SessionState via session_controller if available
            pending_questions_dict: dict[str, Any] | None = None
            if state.session_controller is not None:
                session = state.session_controller.get_session(_session_id)
                if session is not None:
                    pending_questions_dict = session.pending_questions
            if pending_questions_dict is None:
                # Fallback: use a local dict (won't be visible to cancel_all)
                pending_questions_dict = {}
            pending_questions_dict[question_id] = PendingQuestion(
                session_id=_session_id,
                questions=[],
                future=future,
            )
            try:
                # This blocks until the Future is resolved or cancelled
                await future
            except asyncio.CancelledError:
                # Same path as input_provider.py:354-356
                raise RunAbortedError("User cancelled the questionnaire") from None
            finally:
                pending_questions_dict.pop(question_id, None)

        return stream()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pool_mock(agent: Any) -> Mock:  # noqa: PLR0915
    """Create a mock pool wired to the given agent."""
    pool = Mock()
    pool.manifest = Mock()
    pool.manifest.config_file_path = "/tmp/test"
    pool.manifest.model_variants = {}
    storage = Mock()
    storage.save_session = AsyncMock()
    storage.log_message = AsyncMock()
    pool.storage = storage
    pool.todos = Mock()
    pool.todos.on_change = None
    pool.manifest.agents = {agent.name: agent}

    # Set up SessionPool mock for new architecture
    session_pool = Mock()
    session_pool.sessions = Mock()
    session_pool.sessions.get_or_create_session = AsyncMock(return_value=(Mock(), True))
    session_pool.sessions.get_or_create_session_agent = AsyncMock(return_value=agent)
    session_pool.sessions.store = None
    sp_session = Mock()
    sp_session.agent = agent
    session_pool.sessions.get_session = Mock(return_value=sp_session)
    # Use a functional event bus that routes publish→subscribe
    from tests.servers.opencode_server.conftest import _make_functional_event_bus

    session_pool.event_bus = _make_functional_event_bus()

    # Override subscribe to return a real queue-based stream
    _event_queues: dict[str, list[Any]] = {}

    async def _subscribe(sid: str, scope: str = "session") -> Any:
        from asyncio import Queue

        q: Any = Queue(maxsize=1024)
        _event_queues.setdefault(sid, []).append(q)
        return q

    async def _unsubscribe(sid: str, q: Any) -> None:
        if sid in _event_queues:
            _event_queues[sid] = [x for x in _event_queues[sid] if x is not q]

    async def _publish(sid: str, event: Any) -> None:
        for subscriber_sid, queues in _event_queues.items():
            if subscriber_sid == sid:
                for q in queues:
                    with contextlib.suppress(Exception):
                        q.put_nowait(event)

    session_pool.event_bus.subscribe = AsyncMock(side_effect=_subscribe)
    session_pool.event_bus.unsubscribe = AsyncMock(side_effect=_unsubscribe)
    session_pool.event_bus.publish = AsyncMock(side_effect=_publish)

    # Shared completion tracking across receive_request and wait_for_completion
    _completion_events: dict[str, asyncio.Event] = {}

    async def _mock_receive_request(
        session_id: str,
        content: str,
        priority: str = "when_idle",
        input_provider: Any = None,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> str | None:
        from agentpool.lifecycle import RunOutcome, RunState

        complete_event = asyncio.Event()
        _completion_events[session_id] = complete_event
        run_handle = Mock()
        run_handle._run_state = RunState.RUNNING
        run_handle.complete_event = complete_event

        async def _background_run():
            try:
                stream = agent.run_stream(content, session_id=session_id)
                async for event in stream:
                    await session_pool.event_bus.publish(session_id, event)
                run_handle._run_state = RunState.DONE
                run_handle.outcome = RunOutcome.COMPLETED
            except Exception as exc:  # noqa: BLE001
                run_handle._run_state = RunState.DONE
                run_handle.outcome = RunOutcome.FAILED
                # Publish RunFailedEvent so the message_routes error path fires
                from agentpool.agents.events import RunFailedEvent

                await session_pool.event_bus.publish(
                    session_id,
                    RunFailedEvent(
                        run_id="test-run",
                        session_id=session_id,
                        exception=exc,
                    ),
                )
            finally:
                complete_event.set()

        _task = asyncio.create_task(_background_run())  # noqa: RUF006
        return message_id or "msg_test_run"

    session_pool.send_message = _mock_receive_request

    # Mock wait_for_completion to actually wait for the background run
    async def _mock_wait_for_completion(
        sid: str,
        timeout: float | None = None,
    ) -> str:
        ev = _completion_events.get(sid)
        if ev is not None:
            await asyncio.wait_for(ev.wait(), timeout=timeout or 30.0)
        return sid

    session_pool.wait_for_completion = _mock_wait_for_completion
    session_pool.sessions.cancel_run_for_session = Mock()
    pool.session_pool = session_pool

    return pool


def _make_env_mock(tmp_dir: str) -> Mock:
    """Create a mock environment."""
    env = Mock()
    fs = Mock()
    fs.read_file = AsyncMock(return_value="file content")
    env.get_fs = Mock(return_value=fs)
    env.cwd = tmp_dir
    return env


@pytest.fixture
def aborted_mock_agent(tmp_project_dir):
    """Create a RunAbortedAgentMock with pool and env."""
    agent = RunAbortedAgentMock()
    agent.agent_pool = _make_pool_mock(agent)
    agent.host_context = agent.agent_pool
    agent._agent_pool = agent.agent_pool  # state.py resolves _pool via agent._agent_pool
    agent.env = _make_env_mock(str(tmp_project_dir))
    agent.storage = agent.agent_pool.storage
    return agent


@pytest.fixture
def blocking_mock_agent(tmp_project_dir):
    """Create a BlockingOnQuestionAgentMock with pool and env."""
    agent = BlockingOnQuestionAgentMock()
    agent.agent_pool = _make_pool_mock(agent)
    agent.host_context = agent.agent_pool
    agent._agent_pool = agent.agent_pool  # state.py resolves _pool via agent._agent_pool
    agent.env = _make_env_mock(str(tmp_project_dir))
    agent.storage = agent.agent_pool.storage
    return agent


@pytest.fixture
def aborted_test_state(aborted_mock_agent, tmp_project_dir):
    return ServerState(working_dir=str(tmp_project_dir), agent=aborted_mock_agent)


@pytest.fixture
def blocking_test_state(blocking_mock_agent, tmp_project_dir):
    return ServerState(working_dir=str(tmp_project_dir), agent=blocking_mock_agent)


@pytest.fixture
def blocking_real_question_state(tmp_project_dir):
    """Create a ServerState with an agent that creates real PendingQuestions."""
    # Need to create state first so the agent can reference it
    placeholder_agent = RunAbortedAgentMock()
    placeholder_agent.agent_pool = _make_pool_mock(placeholder_agent)
    placeholder_agent.host_context = placeholder_agent.agent_pool
    placeholder_agent._agent_pool = (
        placeholder_agent.agent_pool
    )  # state.py resolves _pool via agent._agent_pool
    placeholder_agent.env = _make_env_mock(str(tmp_project_dir))
    placeholder_agent.storage = placeholder_agent.agent_pool.storage
    state = ServerState(working_dir=str(tmp_project_dir), agent=placeholder_agent)
    # Set up a mock session_controller for the BlockingOnRealQuestionAgentMock
    from agentpool.orchestrator.core import SessionState as SPSessionState

    sp_session = SPSessionState(session_id="test-session", agent_name="test-agent")
    controller = Mock()
    controller.get_session = Mock(return_value=sp_session)
    controller._sessions = {"test-session": sp_session}

    def _cancel_all():
        cancelled = []
        for session in controller._sessions.values():
            for qid, pending in list(session.pending_questions.items()):
                if not pending.future.done():
                    pending.future.cancel()
                    cancelled.append(qid)
        return cancelled

    controller.cancel_all_pending_questions = Mock(side_effect=_cancel_all)
    state.session_controller = controller
    # Now create the real blocking agent with state reference
    real_agent = BlockingOnRealQuestionAgentMock(state)
    real_agent.agent_pool = _make_pool_mock(real_agent)
    real_agent.host_context = real_agent.agent_pool
    real_agent._agent_pool = real_agent.agent_pool  # state.py resolves _pool via agent._agent_pool
    real_agent.env = _make_env_mock(str(tmp_project_dir))
    real_agent.storage = real_agent.agent_pool.storage
    state.agent = real_agent
    # Update the pool reference on the state to use the real agent's pool
    state._pool = real_agent.host_context
    return state


@pytest.fixture
def sample_message_request():
    return MessageRequest(
        parts=[TextPartInput(text="Hello, test!")],
        agent="default",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_session(state: ServerState, session_id: str) -> None:
    """Set up session state manually."""
    from agentpool_server.opencode_server.models import Session
    from agentpool_server.opencode_server.models.common import TimeCreatedUpdated

    now = now_ms()
    session = Session(
        id=session_id,
        project_id="default",
        directory=state.working_dir,
        title="Test Session",
        version="1",
        time=TimeCreatedUpdated(created=now, updated=now),
    )
    state.sessions[session_id] = session
    # Dynamically add fallback dicts for helpers that use getattr
    if not hasattr(state, "messages"):
        state.messages = {}
    state.messages[session_id] = []
    state.agent.session_id = session_id


def _create_user_message(
    session_id: str,
    request: MessageRequest,
) -> tuple[str, MessageWithParts]:
    """Create user message and parts (mimics _process_message logic)."""
    user_msg_id = identifier.ascending("message", request.message_id)
    user_message = UserMessage(
        id=user_msg_id,
        session_id=session_id,
        time=TimeCreated.now(),
        agent=request.agent or "default",
        model=request.model,
    )
    user_msg_with_parts = MessageWithParts(info=user_message)
    for part_input in request.parts:
        if isinstance(part_input, TextPartInput):
            user_msg_with_parts.add_text_part(part_input.text)
    return user_msg_id, user_msg_with_parts


# ---------------------------------------------------------------------------
# Red Flag Test #1: RunAbortedError corrupts agent conversation history
# ---------------------------------------------------------------------------


class TestRunAbortedErrorCorruptsConversation:
    """BUG: RunAbortedError is not caught by the CancelledError handler.

    When question_for_user raises RunAbortedError, _process_message_locked
    does NOT add the aborted assistant message to the agent's conversation.
    This corrupts the LLM's context for subsequent messages.

    Compare with test_cancelled_message.py which tests the CancelledError path
    (which IS properly handled). These tests should pass once RunAbortedError
    is added to the except clause at message_routes.py:480.
    """

    @pytest.mark.asyncio
    async def test_run_aborted_error_message_has_time_completed(
        self,
        aborted_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """RunAbortedError assistant message MUST have time.completed set.

        Without this, the TUI's `pending` memo permanently finds the stale
        assistant message, causing all subsequent user messages to display
        as "QUEUED" — same bug as CancelledError but through a different
        exception path.
        """
        state = aborted_test_state
        session_id = "test-abort-completed"

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        await append_message_to_session(state, session_id, user_msg_with_parts)

        await _process_message_locked(
            session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
        )

        assistant_msgs = [
            msg
            for msg in await get_messages_for_session(state, session_id)
            if isinstance(msg.info, AssistantMessage)
        ]
        assert len(assistant_msgs) == 1, "Should have one assistant message"

        assistant = assistant_msgs[0].info
        assert isinstance(assistant, AssistantMessage)
        assert assistant.time.completed is not None, (
            "RunAbortedError assistant message MUST have time.completed set — "
            "otherwise TUI marks all subsequent messages as QUEUED. "
            "This is the same invariant as CancelledError (see test_cancelled_message.py)."
        )

    @pytest.mark.asyncio
    async def test_run_aborted_error_message_has_aborted_error(
        self,
        aborted_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """RunAbortedError assistant message MUST have MessageAbortedError set."""
        state = aborted_test_state
        session_id = "test-abort-error"

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        await append_message_to_session(state, session_id, user_msg_with_parts)

        await _process_message_locked(
            session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
        )

        assistant_msgs = [
            msg
            for msg in await get_messages_for_session(state, session_id)
            if isinstance(msg.info, AssistantMessage)
        ]
        assert len(assistant_msgs) == 1

        assistant = assistant_msgs[0].info
        assert isinstance(assistant, AssistantMessage)
        assert assistant.error is not None, (
            "RunAbortedError assistant message MUST have error set — same as CancelledError path."
        )
        assert isinstance(assistant.error, MessageAbortedError), (
            f"Error should be MessageAbortedError, got {type(assistant.error).__name__}"
        )

    @pytest.mark.asyncio
    async def test_run_aborted_error_preserves_conversation_history(
        self,
        aborted_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """CRITICAL: After RunAbortedError, conversation MUST include aborted message.

        This is the root cause of the TUI black screen bug:
        - RunAbortedError is NOT caught by `except (CancelledError, TimeoutError)`
        - The aborted assistant message is NOT added to agent.conversation
        - On the next message, the LLM sees corrupted history:
          it has the user message but no assistant response
        - The LLM may behave unpredictably (repeat tool calls, hallucinate, etc.)

        Compare: test_cancelled_message.py::test_cancelled_message_preserves_conversation_history
        which tests the CancelledError path (which IS properly handled).
        """
        state = aborted_test_state
        session_id = "test-abort-history"

        _setup_session(state, session_id)
        initial_count = len(state.agent.conversation.chat_messages)

        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        await append_message_to_session(state, session_id, user_msg_with_parts)

        await _process_message_locked(
            session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
        )

        final_count = len(state.agent.conversation.chat_messages)

        # The aborted assistant message MUST have been added to conversation
        assert final_count >= initial_count + 1, (
            f"Agent conversation should have at least {initial_count + 1} messages "
            f"(original + aborted assistant), but has {final_count}. "
            f"RunAbortedError is not handled like CancelledError — the aborted "
            f"assistant message is never added to agent.conversation.chat_messages. "
            f"This corrupts the LLM's context for the next message."
        )

        # The last message should be the aborted assistant
        last_msg = state.agent.conversation.chat_messages[-1]
        assert last_msg.role == "assistant", (
            f"The last message in agent conversation should be assistant, "
            f"but got role='{last_msg.role}'. The aborted assistant response "
            f"must be added so the LLM knows about it."
        )

    @pytest.mark.asyncio
    async def test_run_aborted_error_session_returns_to_idle(
        self,
        aborted_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """After RunAbortedError, session status must return to idle.

        This currently works because process_stream catches the error and
        yields SessionErrorEvent, then the iterator completes normally,
        allowing agent_lock to be released and mark_session_idle to fire.
        But we verify it stays that way.
        """
        state = aborted_test_state
        session_id = "test-abort-idle"

        # Set up session_pool_integration mock so set_session_status /
        # get_session_status work. The old state.session_status fallback
        # was removed in the SessionPool single-path cleanup.
        _session_statuses: dict[str, Any] = {}

        async def _mock_get_status(sid: str) -> Any:
            return _session_statuses.get(sid)

        # Capture broadcasted SessionStatusEvents to populate _session_statuses
        _original_broadcast = state.broadcast_event

        async def _capturing_broadcast(event: Any) -> None:
            from agentpool_server.opencode_server.models import SessionStatusEvent

            if isinstance(event, SessionStatusEvent):
                _session_statuses[event.properties.session_id] = event.properties.status
            await _original_broadcast(event)

        state.broadcast_event = _capturing_broadcast  # type: ignore[method-assign]

        integration = AsyncMock()
        integration.create_session = AsyncMock(return_value=Mock())
        integration.get_session_status = AsyncMock(side_effect=_mock_get_status)

        async def _mock_create_session(sid: str, *args: Any, **kw: Any) -> Any:
            return Mock()

        integration.create_session = AsyncMock(side_effect=_mock_create_session)
        state.session_pool_integration = integration

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        await append_message_to_session(state, session_id, user_msg_with_parts)

        await _process_message_locked(
            session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
        )

        status = await get_session_status(state, session_id)
        assert status is not None
        assert status.type == "idle", "Session must be idle after RunAbortedError"

    @pytest.mark.asyncio
    async def test_message_after_run_aborted_is_not_queued(
        self,
        aborted_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """After RunAbortedError, a new message should NOT appear as QUEUED.

        This is the user-facing symptom: after aborting a question and sending
        a new message, the TUI shows "QUEUED" because the stale assistant
        message lacks `time.completed`.
        """
        state = aborted_test_state
        session_id = "test-abort-not-queued"

        _setup_session(state, session_id)

        # First message: RunAbortedError
        user_msg_id_1, user_msg_1 = _create_user_message(session_id, sample_message_request)
        await append_message_to_session(state, session_id, user_msg_1)
        await _process_message_locked(
            session_id, sample_message_request, state, user_msg_id_1, user_msg_1
        )

        # Second message: should NOT be queued
        second_request = MessageRequest(
            parts=[TextPartInput(text="Second message after abort")],
            agent="default",
            message_id="msg-after-abort",
        )
        user_msg_id_2, user_msg_2 = _create_user_message(session_id, second_request)
        await append_message_to_session(state, session_id, user_msg_2)
        await _process_message_locked(session_id, second_request, state, user_msg_id_2, user_msg_2)

        # Simulate the TUI's pending memo logic
        all_messages = await get_messages_for_session(state, session_id)
        pending_id = None
        for msg in all_messages:
            if isinstance(msg.info, AssistantMessage) and msg.info.time.completed is None:
                pending_id = msg.info.id

        assert pending_id is None, (
            f"No assistant message should be 'pending' (without time.completed), "
            f"but found pending message {pending_id}. This causes the TUI to "
            f"display subsequent user messages as QUEUED after question abort."
        )


# ---------------------------------------------------------------------------
# Red Flag Test #2: agent_lock deadlock when question Future is never resolved
# ---------------------------------------------------------------------------


class TestAgentLockDeadlockOnUnresolvedQuestion:
    """Per-session agents resolve the agent_lock deadlock.

    With per-session agents, each session has its own agent instance.
    There is no global agent_lock that could deadlock when one session
    blocks on a question. The old deadlock scenario (agent_lock held
    while agent blocks on question, preventing ALL other sessions from
    processing) is resolved by the per-session agent architecture.

    These tests verify that the per-session model prevents the deadlock
    that existed in the shared-agent model.
    """

    @pytest.mark.asyncio
    async def test_per_session_agents_no_agent_lock_deadlock(
        self,
        blocking_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """With per-session agents, a blocking question in one session doesn't block another.

        In the old shared-agent model, agent_lock was held while the agent
        blocked on a question, preventing get_or_load_session from working
        for ANY session. With per-session agents, get_or_load_session no
        longer uses agent_lock.
        """
        state = blocking_test_state
        session_id = "test-no-deadlock"

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        await append_message_to_session(state, session_id, user_msg_with_parts)

        # Start message processing in background (it will block on the question)
        process_task = asyncio.create_task(
            _process_message_locked(
                session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
            )
        )

        # Give the task time to start
        await asyncio.sleep(0.2)

        # Verify agent_lock is NOT held (per-session agents don't need it)
        # In the old model, this would timeout because agent_lock was held.
        # In the new model, agent_lock should be available.
        try:
            await asyncio.wait_for(state.agent_lock.acquire(), timeout=0.5)
            state.agent_lock.release()
            # agent_lock is available — no deadlock
        except TimeoutError:
            pytest.fail(
                "agent_lock should NOT be held while agent blocks on question "
                "in the per-session agent model. The deadlock bug is back!"
            )

        # Clean up
        process_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await process_task

    @pytest.mark.asyncio
    async def test_no_deadlock_different_session_after_blocking_question(
        self,
        blocking_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """With per-session agents, a blocking question doesn't prevent loading another session.

        This verifies that the "关了 opencode 重新启动, 无法在新 session 中发送
        user message" bug is resolved by per-session agents.
        """
        state = blocking_test_state
        session_id = "test-no-deadlock-session"

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        await append_message_to_session(state, session_id, user_msg_with_parts)

        # Start message processing in background (blocks on question)
        process_task = asyncio.create_task(
            _process_message_locked(
                session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
            )
        )

        # Give it time to start
        await asyncio.sleep(0.2)

        # Now try to get_or_load_session for a DIFFERENT session
        # In the old model, this would deadlock because get_or_load_session
        # needs agent_lock which is held by the blocking task.
        # In the new model, get_or_load_session doesn't use agent_lock.
        new_session_id = "test-no-deadlock-new-session"
        _setup_session(state, new_session_id)

        # This MUST NOT deadlock — per-session agents resolve the issue
        try:
            from agentpool_server.opencode_server.routes.session_routes import get_or_load_session

            await asyncio.wait_for(get_or_load_session(state, new_session_id), timeout=1.0)
            # Either gets the session or None — both are fine, no deadlock
        except TimeoutError:
            pytest.fail(
                "get_or_load_session should NOT deadlock when another session "
                "blocks on a question. The per-session agent model should prevent this."
            )

        # Clean up
        process_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await process_task

    @pytest.mark.asyncio
    async def test_cancelling_pending_question_releases_resources(
        self,
        blocking_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """If we cancel the pending question's Future, resources are released.

        This test verifies that cancelling the Future properly propagates
        through the agent stack and the task completes.

        In production, this would happen when:
        - TUI sends question reject (ESC)
        - Server detects SSE disconnect and cancels pending questions
        """
        state = blocking_test_state
        session_id = "test-cancel-releases"

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        await append_message_to_session(state, session_id, user_msg_with_parts)

        # Start message processing in background
        process_task = asyncio.create_task(
            _process_message_locked(
                session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
            )
        )

        # Wait for the question to be created
        await asyncio.sleep(0.3)

        # Cancel the task directly (simulates question cancellation)
        process_task.cancel()

        # Wait for the task to be cancelled
        with contextlib.suppress(asyncio.CancelledError):
            await process_task

        # Verify agent_lock is available after cancellation
        try:
            await asyncio.wait_for(state.agent_lock.acquire(), timeout=0.5)
            state.agent_lock.release()
        except TimeoutError:
            pytest.fail(
                "agent_lock should be available after cancelling the blocked task, "
                "but it's still held. This indicates a lock leak on CancelledError."
            )


# ---------------------------------------------------------------------------
# Red Flag Test #4: SSE disconnect cancels pending questions → releases agent_lock
# ---------------------------------------------------------------------------


class TestSSEDisconnectReleasesAgentLock:
    """Per-session agents resolve the agent_lock deadlock on SSE disconnect.

    With per-session agents, the agent_lock is no longer used by
    get_or_load_session, so the deadlock scenario where an unresolved
    question blocks ALL session access is resolved.

    These tests verify that cancel_all_pending_questions still works
    correctly and that new sessions can be accessed after disconnect.
    """

    @pytest.mark.asyncio
    async def test_cancel_all_pending_questions_cancels_futures(
        self,
        blocking_real_question_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """cancel_all_pending_questions() must cancel all pending question Futures."""
        state = blocking_real_question_state
        session_id = "test-cancel-questions"

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        await append_message_to_session(state, session_id, user_msg_with_parts)

        # Start message processing in background (will create PendingQuestion)
        process_task = asyncio.create_task(
            _process_message_locked(
                session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
            )
        )

        # Allow event loop to start process_task and background_run
        await asyncio.sleep(0)

        # Wait for the question to be created in session controller
        session = (
            state.session_controller.get_session(session_id) if state.session_controller else None
        )
        for _ in range(40):
            if session and session.pending_questions:
                break
            await asyncio.sleep(0.05)

        assert session is not None, "Session should exist"
        assert session.pending_questions, (
            "Agent should have created a pending question, but no pending questions found."
        )

        # Simulate SSE disconnect: call cancel_all_pending_questions
        cancelled_ids = state.cancel_all_pending_questions()

        assert len(cancelled_ids) > 0, "cancel_all_pending_questions should return cancelled IDs"

        # The process task should complete (no longer blocked)
        try:
            await asyncio.wait_for(process_task, timeout=2.0)
        except TimeoutError:
            pytest.fail(
                "process_task should complete after cancel_all_pending_questions, "
                "but it's still running."
            )

        # Verify agent_lock is available
        try:
            await asyncio.wait_for(state.agent_lock.acquire(), timeout=0.5)
            state.agent_lock.release()
        except TimeoutError:
            pytest.fail(
                "agent_lock should be available after cancelling pending questions, "
                "but it's still held."
            )

    @pytest.mark.asyncio
    async def test_cancel_all_pending_questions_allows_new_session_access_after_sse_disconnect(
        self,
        blocking_real_question_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """After SSE disconnect, a new session can be accessed without deadlock.

        This reproduces the exact user scenario: "关了 opencode 重新启动, 无法在新
        session 中发送 user message". With per-session agents, get_or_load_session
        no longer uses agent_lock, so this scenario cannot deadlock.
        """
        state = blocking_real_question_state
        session_id = "test-sse-disconnect"

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        await append_message_to_session(state, session_id, user_msg_with_parts)

        # Start message processing in background (will block on question)
        process_task = asyncio.create_task(
            _process_message_locked(
                session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
            )
        )

        # Wait for the question to be created
        session = (
            state.session_controller.get_session(session_id) if state.session_controller else None
        )
        for _ in range(20):
            if session and session.pending_questions:
                break
            await asyncio.sleep(0.05)

        # Simulate SSE disconnect
        state.cancel_all_pending_questions()

        # Wait for the process task to complete
        try:
            await asyncio.wait_for(process_task, timeout=2.0)
        except TimeoutError:
            pytest.fail("process_task should complete after cancelling questions")

        # Now try accessing a NEW session via get_or_load_session
        new_session_id = "test-sse-reconnect"
        _setup_session(state, new_session_id)

        # This MUST NOT deadlock — per-session agents resolve the issue
        from agentpool_server.opencode_server.routes.session_routes import get_or_load_session

        try:
            await asyncio.wait_for(get_or_load_session(state, new_session_id), timeout=2.0)
        except TimeoutError:
            pytest.fail(
                "get_or_load_session for new session should succeed after SSE disconnect + "
                "cancel_all_pending_questions, but it timed out (deadlock)."
            )


# ---------------------------------------------------------------------------
# Red Flag Test #3: UnboundLocalError when CancelledError before agent assignment
# ---------------------------------------------------------------------------


class CancelBeforeAgentAssignmentMock:
    """Mock agent whose bind_agent_to_session raises CancelledError.

    Simulates: CancelledError arrives before `agent` is assigned in the
    `async with state.agent_lock:` block. The except clause at line 480
    references `agent` at line 518, which is UnboundLocalError if CancelledError
    fires before line 376 (`agent = state.agent`).

    We trigger this by making bind_agent_to_session raise CancelledError,
    which occurs at line 379 — after agent assignment but before run_stream.
    This is a realistic scenario (task cancellation during session binding).
    """

    def __init__(self) -> None:
        self.name = "test-agent"
        self.run_stream_call_count = 0
        self.agent_pool: Mock | None = None
        self.host_context: Mock | None = None
        self.env: Mock | None = None
        self.storage: Any = None
        self.tools: list[Any] = []
        self._input_provider = None
        self.model_name = "test-model"
        self.session_id: str | None = None
        from agentpool.messaging.message_history import MessageHistory

        self.conversation = MessageHistory()

    async def set_model(self, model: str) -> None:
        self.model_name = model

    async def set_mode(self, mode: str, category_id: str | None = None) -> None:
        pass

    async def get_available_models(self):
        return []

    async def load_session(self, session_id: str) -> None:
        return None

    def run_stream(self, *args: Any, **kwargs: Any):
        self.run_stream_call_count += 1

        async def stream():
            if False:
                yield None

        return stream()


class TestUnboundLocalErrorInExceptHandler:
    """BUG: UnboundLocalError when CancelledError occurs during agent binding.

    When CancelledError occurs during bind_agent_to_session (line 379), the
    except handler at line 480 tries to access `agent` at line 518, but `agent`
    is a local variable defined inside `async with state.agent_lock:` (line 376).
    If CancelledError arrives before that assignment, `agent` is unbound.

    Even in the case where agent IS assigned (line 376 runs before CancelledError),
    the `agent` variable is scoped inside the `async with` block. Python's scoping
    rules mean that if the assignment never executes (e.g., CancelledError at
    `state.agent_lock.acquire()`), the except handler crashes with UnboundLocalError.

    Fix: Hoist `agent = state.agent` before the `async with state.agent_lock:` block.
    """

    @pytest.mark.asyncio
    async def test_cancelled_error_during_agent_binding_no_crash(
        self,
        aborted_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """CancelledError during agent binding must NOT crash with UnboundLocalError.

        We simulate this by making the agent_lock's __aenter__ raise CancelledError,
        which prevents `agent = state.agent` (line 376) from executing. The except
        handler at line 480 references `agent` at line 518, so UnboundLocalError occurs.
        """
        state = aborted_test_state
        session_id = "test-unbound-agent"

        _setup_session(state, session_id)

        # Replace agent_lock with one that raises CancelledError on acquire
        original_lock = state.agent_lock
        lock_that_cancels = asyncio.Lock()

        # Make the lock raise CancelledError on first acquire
        original_acquire = lock_that_cancels.acquire

        _acquire_count = 0

        async def acquire_raising_cancel() -> bool:
            nonlocal _acquire_count
            _acquire_count += 1
            if _acquire_count == 1:
                raise asyncio.CancelledError("Simulated cancellation during lock acquire")
            return await original_acquire()

        lock_that_cancels.acquire = acquire_raising_cancel  # type: ignore[assignment]
        state.agent_lock = lock_that_cancels

        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        await append_message_to_session(state, session_id, user_msg_with_parts)

        # This should NOT raise UnboundLocalError — it should handle CancelledError gracefully
        try:
            await _process_message_locked(
                session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
            )
        except asyncio.CancelledError:
            # CancelledError may propagate if not caught internally — that's fine
            pass
        except UnboundLocalError:
            pytest.fail(
                "UnboundLocalError in except handler: `agent` is referenced at line 518 "
                "but defined inside `async with state.agent_lock:` at line 376. "
                "When CancelledError occurs before agent assignment, the except handler "
                "crashes. Fix: hoist `agent = state.agent` before the agent_lock block."
            )
        finally:
            state.agent_lock = original_lock


# ---------------------------------------------------------------------------
# Import for contextlib (used in cleanup)
# ---------------------------------------------------------------------------

import contextlib  # noqa: E402
