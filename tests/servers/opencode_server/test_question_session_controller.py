"""Tests for question routing via SessionController."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import Mock

import pytest

from agentpool.orchestrator.core import SessionController, SessionState
from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
from agentpool_server.opencode_server.models import (
    QuestionReply,
)
from agentpool_server.opencode_server.routes.question_routes import (
    list_questions,
    reject_question,
    reply_to_question,
)
from agentpool_server.opencode_server.state import PendingQuestion, ServerState


@pytest.fixture
def mock_pool():
    """Create a mock AgentPool."""
    pool = Mock()
    pool.main_agent = Mock()
    pool.main_agent.name = "test_agent"
    pool.manifest = Mock()
    pool.manifest.agents = {}
    pool.mcp = Mock()
    pool.mcp.get_aggregating_provider = Mock(return_value=Mock())
    pool.skills_tools_provider = Mock()
    pool._config_file_path = None
    return pool


@pytest.fixture
def session_controller(mock_pool):
    """Create a SessionController with a mock pool."""
    return SessionController(pool=mock_pool)


async def _create_session_state(
    controller: SessionController,
    session_id: str,
) -> SessionState:
    """Helper to create a session in the controller."""
    session, _was_created = await controller.get_or_create_session(session_id)
    return session


def _make_pending_question(
    session_id: str,
    question_id: str,
    future: asyncio.Future[list[list[str]]] | None = None,
) -> PendingQuestion:
    """Create a PendingQuestion for testing."""
    from agentpool_server.opencode_server.models.question import QuestionInfo, QuestionOption

    if future is None:
        future = asyncio.get_event_loop().create_future()
    return PendingQuestion(
        session_id=session_id,
        questions=[
            QuestionInfo(
                question="Test question?",
                header="Test",
                options=[QuestionOption(label="yes", description="")],
            )
        ],
        future=future,
    )


class TestSessionControllerPendingQuestions:
    """Tests for SessionController question management."""

    @pytest.mark.asyncio
    async def test_list_pending_questions_aggregates_across_sessions(
        self,
        session_controller: SessionController,
    ) -> None:
        """list_pending_questions should aggregate from all sessions."""
        session_a = await _create_session_state(session_controller, "session_a")
        session_b = await _create_session_state(session_controller, "session_b")

        future_a = asyncio.get_event_loop().create_future()
        future_b = asyncio.get_event_loop().create_future()
        session_a.pending_questions["q1"] = _make_pending_question("session_a", "q1", future_a)
        session_b.pending_questions["q2"] = _make_pending_question("session_b", "q2", future_b)

        result = session_controller.list_pending_questions()

        assert len(result) == 2
        ids = {getattr(q, "session_id", None) for q in result}
        assert ids == {"session_a", "session_b"}

    @pytest.mark.asyncio
    async def test_list_pending_questions_returns_empty_when_none(
        self,
        session_controller: SessionController,
    ) -> None:
        """list_pending_questions should return empty list when no questions."""
        result = session_controller.list_pending_questions()
        assert result == []

    @pytest.mark.asyncio
    async def test_cancel_all_pending_questions_cancels_across_sessions(
        self,
        session_controller: SessionController,
    ) -> None:
        """cancel_all_pending_questions should cancel all pending question futures."""
        session_a = await _create_session_state(session_controller, "session_a")
        session_b = await _create_session_state(session_controller, "session_b")

        future_a = asyncio.get_event_loop().create_future()
        future_b = asyncio.get_event_loop().create_future()
        session_a.pending_questions["q1"] = _make_pending_question("session_a", "q1", future_a)
        session_b.pending_questions["q2"] = _make_pending_question("session_b", "q2", future_b)

        cancelled = session_controller.cancel_all_pending_questions()

        assert sorted(cancelled) == ["q1", "q2"]
        assert future_a.cancelled()
        assert future_b.cancelled()

    @pytest.mark.asyncio
    async def test_cancel_all_pending_questions_skips_done_futures(
        self,
        session_controller: SessionController,
    ) -> None:
        """cancel_all_pending_questions should skip futures that are already done."""
        session_a = await _create_session_state(session_controller, "session_a")

        future_done = asyncio.get_event_loop().create_future()
        future_done.set_result([["yes"]])
        future_pending = asyncio.get_event_loop().create_future()

        session_a.pending_questions["q_done"] = _make_pending_question(
            "session_a", "q_done", future_done
        )
        session_a.pending_questions["q_pending"] = _make_pending_question(
            "session_a", "q_pending", future_pending
        )

        cancelled = session_controller.cancel_all_pending_questions()

        assert cancelled == ["q_pending"]
        assert not future_done.cancelled()
        assert future_pending.cancelled()

    @pytest.mark.asyncio
    async def test_cancel_session_pending_questions_targets_one_session(
        self,
        session_controller: SessionController,
    ) -> None:
        """cancel_session_pending_questions should only cancel for the specified session."""
        session_a = await _create_session_state(session_controller, "session_a")
        session_b = await _create_session_state(session_controller, "session_b")

        future_a = asyncio.get_event_loop().create_future()
        future_b = asyncio.get_event_loop().create_future()
        session_a.pending_questions["q1"] = _make_pending_question("session_a", "q1", future_a)
        session_b.pending_questions["q2"] = _make_pending_question("session_b", "q2", future_b)

        cancelled = session_controller.cancel_session_pending_questions("session_a")

        assert cancelled == ["q1"]
        assert future_a.cancelled()
        assert not future_b.cancelled()

    @pytest.mark.asyncio
    async def test_cancel_session_pending_questions_returns_empty_for_missing_session(
        self,
        session_controller: SessionController,
    ) -> None:
        """cancel_session_pending_questions should return empty for unknown session."""
        cancelled = session_controller.cancel_session_pending_questions("nonexistent")
        assert cancelled == []


class TestQuestionRoutesViaSessionController:
    """Tests for question routes reading from SessionState via SessionController."""

    @pytest.mark.asyncio
    async def test_list_questions_via_session_controller(
        self,
        session_controller: SessionController,
    ) -> None:
        """list_questions should read from SessionState when session_controller is set."""
        session = await _create_session_state(session_controller, "test_session")
        future = asyncio.get_event_loop().create_future()
        session.pending_questions["q1"] = _make_pending_question("test_session", "q1", future)

        mock_agent = Mock()
        mock_agent.agent_pool = None
        state = ServerState(working_dir="/tmp", agent=mock_agent)
        state.session_controller = session_controller

        result = await list_questions(state)

        assert len(result) == 1
        assert result[0].id == "q1"
        assert result[0].session_id == "test_session"

    @pytest.mark.asyncio
    async def test_list_questions_no_session_controller_returns_empty(
        self,
        session_controller: SessionController,
    ) -> None:
        """list_questions should return empty list when no session_controller."""
        mock_agent = Mock()
        mock_agent.agent_pool = None
        state = ServerState(working_dir="/tmp", agent=mock_agent)
        # No session_controller set, so list_questions should return empty

        result = await list_questions(state)

        assert result == []

    @pytest.mark.asyncio
    async def test_reply_to_question_via_session_controller(
        self,
        session_controller: SessionController,
    ) -> None:
        """reply_to_question should resolve questions stored on SessionState."""
        session = await _create_session_state(session_controller, "test_session")
        future = asyncio.get_event_loop().create_future()
        session.pending_questions["q1"] = _make_pending_question("test_session", "q1", future)

        mock_agent = Mock()
        mock_agent.agent_pool = None
        state = ServerState(working_dir="/tmp", agent=mock_agent)
        state.session_controller = session_controller
        session.input_provider = OpenCodeInputProvider(state, "test_session")
        broadcast_calls = []

        async def _mock_broadcast(event):
            broadcast_calls.append(event)

        state.broadcast_event = _mock_broadcast

        reply = QuestionReply(answers=[["yes"]])
        result = await reply_to_question("q1", reply, state)

        assert result is True
        assert future.done()
        assert future.result() == [["yes"]]

    @pytest.mark.asyncio
    async def test_reject_question_via_session_controller(
        self,
        session_controller: SessionController,
    ) -> None:
        """reject_question should cancel questions stored on SessionState."""
        session = await _create_session_state(session_controller, "test_session")
        future = asyncio.get_event_loop().create_future()
        session.pending_questions["q1"] = _make_pending_question("test_session", "q1", future)

        mock_agent = Mock()
        mock_agent.agent_pool = None
        state = ServerState(working_dir="/tmp", agent=mock_agent)
        state.session_controller = session_controller
        broadcast_calls = []

        async def _mock_broadcast(event):
            broadcast_calls.append(event)

        state.broadcast_event = _mock_broadcast

        result = await reject_question("q1", state)

        assert result is True
        assert future.cancelled()
        assert "q1" not in session.pending_questions


class TestInputProviderStoresQuestionsOnSessionState:
    """Tests that OpenCodeInputProvider stores questions on SessionState."""

    @pytest.mark.asyncio
    async def test_input_provider_stores_question_on_session_state(
        self,
        session_controller: SessionController,
    ) -> None:
        """When session_controller is available, questions go to SessionState."""
        await _create_session_state(session_controller, "test_session")

        mock_agent = Mock()
        mock_agent.agent_pool = None
        state = ServerState(working_dir="/tmp", agent=mock_agent)
        state.session_controller = session_controller
        broadcast_calls = []

        async def _mock_broadcast(event):
            broadcast_calls.append(event)

        state.broadcast_event = _mock_broadcast

        provider = OpenCodeInputProvider(state, "test_session")

        from mcp import types

        schema = {"type": "string", "enum": ["a", "b"]}
        params = types.ElicitRequestFormParams(message="Pick one?", requestedSchema=schema)

        task = asyncio.create_task(provider.get_elicitation(params))
        await asyncio.sleep(0.1)

        # Question should be on SessionState
        session = session_controller.get_session("test_session")
        assert session is not None
        assert len(session.pending_questions) == 1

        # Clean up
        question_id = next(iter(session.pending_questions.keys()))
        provider.resolve_question(question_id, [["a"]])
        await task

    @pytest.mark.asyncio
    async def test_input_provider_no_fallback_to_server_state(
        self,
        session_controller: SessionController,
    ) -> None:
        """When no session_controller, questions are not stored on ServerState."""
        mock_agent = Mock()
        mock_agent.agent_pool = None
        state = ServerState(working_dir="/tmp", agent=mock_agent)
        broadcast_calls = []

        async def _mock_broadcast(event):
            broadcast_calls.append(event)

        state.broadcast_event = _mock_broadcast

        provider = OpenCodeInputProvider(state, "test_session")

        from mcp import types

        schema = {"type": "string", "enum": ["a", "b"]}
        params = types.ElicitRequestFormParams(message="Pick one?", requestedSchema=schema)

        task = asyncio.create_task(provider.get_elicitation(params))
        await asyncio.sleep(0.1)

        # Question should NOT be visible via provider; provider uses empty dict fallback
        assert len(provider.get_pending_questions()) == 0

        # Cancel the task since there's no question to resolve
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class TestSSEDisconnectViaSessionController:
    """Tests that SSE disconnect cancels questions via SessionController."""

    @pytest.mark.asyncio
    async def test_cancel_all_pending_questions_delegates_to_session_controller(
        self,
        session_controller: SessionController,
    ) -> None:
        """ServerState.cancel_all_pending_questions delegates to SessionController."""
        session = await _create_session_state(session_controller, "test_session")
        future = asyncio.get_event_loop().create_future()
        session.pending_questions["q1"] = _make_pending_question("test_session", "q1", future)

        mock_agent = Mock()
        mock_agent.agent_pool = None
        state = ServerState(working_dir="/tmp", agent=mock_agent)
        state.session_controller = session_controller

        cancelled = state.cancel_all_pending_questions()

        assert cancelled == ["q1"]
        assert future.cancelled()

    @pytest.mark.asyncio
    async def test_cancel_session_pending_questions_delegates_to_session_controller(
        self,
        session_controller: SessionController,
    ) -> None:
        """ServerState.cancel_session_pending_questions delegates to SessionController."""
        session = await _create_session_state(session_controller, "test_session")
        future = asyncio.get_event_loop().create_future()
        session.pending_questions["q1"] = _make_pending_question("test_session", "q1", future)

        mock_agent = Mock()
        mock_agent.agent_pool = None
        state = ServerState(working_dir="/tmp", agent=mock_agent)
        state.session_controller = session_controller

        cancelled = state.cancel_session_pending_questions("test_session")

        assert cancelled == ["q1"]
        assert future.cancelled()
