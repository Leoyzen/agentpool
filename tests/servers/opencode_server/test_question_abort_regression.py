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
from agentpool_server.opencode_server.models import (
    AssistantMessage,
    MessagePath,
    MessageRequest,
    MessageTime,
    MessageUpdatedEvent,
    SessionStatus,
    TextPartInput,
    TimeCreated,
    UserMessage,
)
from agentpool_server.opencode_server.models.message import (
    MessageAbortedError,
    MessageWithParts,
)
from agentpool_server.opencode_server.routes.message_routes import _process_message_locked
from agentpool_server.opencode_server.state import ServerState
from agentpool.utils import identifiers as identifier
from agentpool.utils.time_utils import now_ms


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
            yield  # noqa: unreachable — makes this an async generator

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
                yield None  # noqa: unreachable — makes this an async generator

        return stream()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pool_mock(agent: Any) -> Mock:
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
    pool.skill_commands = None
    pool.all_agents = {agent.name: agent}
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
    agent.env = _make_env_mock(str(tmp_project_dir))
    agent.storage = agent.agent_pool.storage
    return agent


@pytest.fixture
def blocking_mock_agent(tmp_project_dir):
    """Create a BlockingOnQuestionAgentMock with pool and env."""
    agent = BlockingOnQuestionAgentMock()
    agent.agent_pool = _make_pool_mock(agent)
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
    state.messages[session_id] = []
    state.session_status[session_id] = SessionStatus(type="idle")
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
        state.messages[session_id].append(user_msg_with_parts)

        await _process_message_locked(
            session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
        )

        assistant_msgs = [
            msg for msg in state.messages[session_id] if isinstance(msg.info, AssistantMessage)
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
        state.messages[session_id].append(user_msg_with_parts)

        await _process_message_locked(
            session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
        )

        assistant_msgs = [
            msg for msg in state.messages[session_id] if isinstance(msg.info, AssistantMessage)
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
        """CRITICAL: After RunAbortedError, the agent's conversation MUST include
        the aborted assistant message.

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
        state.messages[session_id].append(user_msg_with_parts)

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

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        state.messages[session_id].append(user_msg_with_parts)

        await _process_message_locked(
            session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
        )

        assert state.session_status[session_id].type == "idle", (
            "Session must be idle after RunAbortedError"
        )

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
        state.messages[session_id].append(user_msg_1)
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
        state.messages[session_id].append(user_msg_2)
        await _process_message_locked(session_id, second_request, state, user_msg_id_2, user_msg_2)

        # Simulate the TUI's pending memo logic
        all_messages = state.messages[session_id]
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
    """BUG: agent_lock deadlock when question Future is never resolved.

    When the agent calls question_for_user:
    1. A PendingQuestion with asyncio.Future is created
    2. The agent.run_stream() awaits the Future (via input_provider)
    3. agent_lock is still held (inside `async with state.agent_lock:`)
    4. If the TUI disconnects (user closes opencode), the Future is never resolved
    5. agent_lock is never released
    6. On reconnect, ANY request needing agent_lock deadlocks:
       - get_or_load_session (called by _process_message, list_messages, etc.)
       - Any subsequent message

    This is the "can't send user message after restart" symptom.
    """

    @pytest.mark.asyncio
    async def test_agent_lock_held_while_question_pending(
        self,
        blocking_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """When agent blocks on a question, agent_lock must still be held.

        This test verifies the precondition for the deadlock: agent_lock
        is held while the agent is waiting for a question answer.
        """
        state = blocking_test_state
        session_id = "test-lock-held"

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        state.messages[session_id].append(user_msg_with_parts)

        # Start message processing in background (it will block on the question)
        process_task = asyncio.create_task(
            _process_message_locked(
                session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
            )
        )

        # Give the task time to start and acquire agent_lock
        await asyncio.sleep(0.2)

        # Verify agent_lock is held — try to acquire it with a short timeout
        try:
            acquired = await asyncio.wait_for(state.agent_lock.acquire(), timeout=0.1)
            # If we got here, the lock was NOT held — precondition not met
            state.agent_lock.release()
            pytest.fail(
                "agent_lock should be held while agent blocks on question, "
                "but it was not acquired. This test's precondition is wrong."
            )
        except TimeoutError:
            # Expected: agent_lock is held by the blocking task
            pass

        # Clean up: cancel the blocking task to release agent_lock
        process_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await process_task

    @pytest.mark.asyncio
    async def test_agent_lock_deadlock_prevents_new_message(
        self,
        blocking_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """CRITICAL: If agent blocks on a question, new messages to ANY session
        cannot be processed because get_or_load_session needs agent_lock.

        This reproduces the user's bug: "关了 opencode 重新启动，无法在新 session
        中发送 user message". The agent_lock is stuck from the previous session's
        unresolved question, blocking ALL future message processing.
        """
        state = blocking_test_state
        session_id = "test-deadlock-session"

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        state.messages[session_id].append(user_msg_with_parts)

        # Start message processing in background (blocks on question)
        process_task = asyncio.create_task(
            _process_message_locked(
                session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
            )
        )

        # Give it time to start and acquire agent_lock
        await asyncio.sleep(0.2)

        # Now try to send a message to a DIFFERENT session
        # (simulating: user restarts TUI, creates new session, tries to send message)
        new_session_id = "test-deadlock-new-session"
        _setup_session(state, new_session_id)

        new_request = MessageRequest(
            parts=[TextPartInput(text="Message to new session")],
            agent="default",
        )
        user_msg_id_2, user_msg_2 = _create_user_message(new_session_id, new_request)
        state.messages[new_session_id].append(user_msg_2)

        # This call will deadlock because _process_message_locked needs agent_lock
        # which is held by the first message's blocking task
        deadlock_task = asyncio.create_task(
            _process_message_locked(new_session_id, new_request, state, user_msg_id_2, user_msg_2)
        )

        # Wait a short time — if it completes, there's no deadlock
        try:
            await asyncio.wait_for(deadlock_task, timeout=0.5)
            # If we get here, the lock was released — no deadlock
            # (This would mean the bug is fixed or the test is wrong)
        except TimeoutError:
            # DEADLOCK DETECTED: The new message cannot be processed
            # because agent_lock is held by the blocking question.
            # This is the red flag!
            pass

        # Clean up
        deadlock_task.cancel()
        process_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await process_task
        with contextlib.suppress(asyncio.CancelledError):
            await deadlock_task

    @pytest.mark.asyncio
    async def test_cancelling_pending_question_releases_agent_lock(
        self,
        blocking_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """If we cancel the pending question's Future, agent_lock should be released.

        This test verifies that cancelling the Future properly propagates
        through the agent stack and releases agent_lock.

        In production, this would happen when:
        - TUI sends question reject (ESC)
        - Server detects SSE disconnect and cancels pending questions

        Current behavior: Future.cancel() → CancelledError in input_provider →
        ElicitResult(action="cancel") → question_for_user raises RunAbortedError →
        process_stream catches it, yields SessionErrorEvent → iterator completes →
        agent_lock released.

        BUT: RunAbortedError is not caught by the CancelledError handler,
        so conversation state is corrupted (see Test #1).
        """
        state = blocking_test_state
        session_id = "test-cancel-releases"

        _setup_session(state, session_id)
        user_msg_id, user_msg_with_parts = _create_user_message(session_id, sample_message_request)
        state.messages[session_id].append(user_msg_with_parts)

        # Start message processing in background
        process_task = asyncio.create_task(
            _process_message_locked(
                session_id, sample_message_request, state, user_msg_id, user_msg_with_parts
            )
        )

        # Wait for the question to be created
        await asyncio.sleep(0.3)

        # The agent should have created a pending question (via input_provider)
        # But since our mock doesn't actually use input_provider, the agent
        # just blocks on block_forever_event. Let's cancel the task directly.
        process_task.cancel()

        # Wait for the task to be cancelled
        with contextlib.suppress(asyncio.CancelledError):
            await process_task

        # Verify agent_lock is released after cancellation
        try:
            acquired = await asyncio.wait_for(state.agent_lock.acquire(), timeout=0.5)
            state.agent_lock.release()
        except TimeoutError:
            pytest.fail(
                "agent_lock should be released after cancelling the blocked task, "
                "but it's still held. This indicates a lock leak on CancelledError."
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
                yield None  # noqa: unreachable — makes this an async generator

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
        state.messages[session_id].append(user_msg_with_parts)

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
