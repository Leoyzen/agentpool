"""Tests for TurnRunner.steer() and TurnRunner.followup() agent-type-aware routing.

Covers all 6 routing scenarios:
- Native steer active → enqueue(asap)
- Native followup active → enqueue(when_idle)
- Native steer idle → receive_request(priority="steer")
- Native followup idle → receive_request(priority="followup")
- Non-native steer → injection_manager.inject()
- Non-native followup → injection_manager.queue()
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.orchestrator.core import SessionController, TurnRunner
from agentpool.orchestrator.run import RunHandle, RunStatus


pytestmark = [pytest.mark.unit, pytest.mark.deprecated]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


@pytest.fixture
def controller(mock_pool: MagicMock) -> SessionController:
    """Return a real SessionController backed by the mock pool."""
    return SessionController(pool=mock_pool)


@pytest.fixture
def turn_runner(controller: SessionController) -> TurnRunner:
    """Return a TurnRunner with auto-resume enabled."""
    return TurnRunner(session_controller=controller, enable_auto_resume=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_native_agent() -> MagicMock:
    """Return a mocked native agent with AGENT_TYPE = 'native'."""
    agent = MagicMock()
    agent.AGENT_TYPE = "native"
    return agent


def _make_acp_agent() -> MagicMock:
    """Return a mocked ACP agent with AGENT_TYPE = 'acp'."""
    agent = MagicMock()
    agent.AGENT_TYPE = "acp"
    return agent


async def _setup_session_with_agent(
    controller: SessionController,
    session_id: str,
    agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Create a session and attach the mock agent."""
    state, _ = await controller.get_or_create_session(session_id)
    state.agent = agent
    controller._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent


def _make_run_handle(
    session_id: str,
    agent_type: str,
    run_ctx: AgentRunContext | None = None,
) -> RunHandle:
    """Create a RunHandle and register it in the controller's _runs."""
    handle = RunHandle(
        run_id=f"run-{session_id}",
        session_id=session_id,
        agent_type=agent_type,
    )
    if run_ctx is not None:
        handle.run_ctx = run_ctx
    return handle


# ---------------------------------------------------------------------------
# Test 1: Native steer active → enqueue(asap)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_native_steer_active_enqueues_asap(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """When steer is called on a native agent with an active AgentRun,
    it enqueues the message with priority='asap'."""
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-1", agent, mock_pool)

    # Create a RunHandle with active_agent_run set (mocked AgentRun)
    mock_agent_run = MagicMock()
    mock_agent_run.enqueue = MagicMock()
    run_handle = _make_run_handle("sess-1", "native")
    run_handle.active_agent_run = mock_agent_run
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-1")
    assert session is not None
    session.current_run_id = run_handle.run_id

    # RED: steer() does not exist yet → AttributeError
    await turn_runner.steer("sess-1", "steer message")

    mock_agent_run.enqueue.assert_called_once_with("steer message", priority="asap")


# ---------------------------------------------------------------------------
# Test 2: Native followup active → enqueue(when_idle)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_native_followup_active_enqueues_when_idle(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """When followup is called on a native agent with an active AgentRun,
    it enqueues the message with priority='when_idle'."""
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-2", agent, mock_pool)

    mock_agent_run = MagicMock()
    mock_agent_run.enqueue = MagicMock()
    run_handle = _make_run_handle("sess-2", "native")
    run_handle.active_agent_run = mock_agent_run
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-2")
    assert session is not None
    session.current_run_id = run_handle.run_id

    # RED: followup() does not exist yet → AttributeError
    await turn_runner.followup("sess-2", "followup message")

    mock_agent_run.enqueue.assert_called_once_with("followup message", priority="when_idle")


# ---------------------------------------------------------------------------
# Test 3: Native steer idle → receive_request(priority="steer")
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_native_steer_idle_delegates_to_receive_request(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """When steer is called on a native agent with no active AgentRun,
    it delegates to receive_request with priority='steer'."""
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-3", agent, mock_pool)

    # No active agent_run (idle session)
    # receive_request is a real method, we spy on it
    controller.receive_request = AsyncMock(return_value=None)  # type: ignore[method-assign]

    # RED: steer() does not exist yet → AttributeError
    await turn_runner.steer("sess-3", "steer idle message")

    controller.receive_request.assert_called_once_with(
        "sess-3", "steer idle message", priority="steer"
    )


# ---------------------------------------------------------------------------
# Test 4: Native followup idle → receive_request(priority="followup")
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_native_followup_idle_delegates_to_receive_request(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """When followup is called on a native agent with no active AgentRun,
    it delegates to receive_request with priority='followup'."""
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-4", agent, mock_pool)

    controller.receive_request = AsyncMock(return_value=None)  # type: ignore[method-assign]

    # RED: followup() does not exist yet → AttributeError
    await turn_runner.followup("sess-4", "followup idle message")

    controller.receive_request.assert_called_once_with(
        "sess-4", "followup idle message", priority="followup"
    )


# ---------------------------------------------------------------------------
# Test 5: Non-native steer → injection_manager.inject()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_non_native_steer_injects_via_injection_manager(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """When steer is called on a non-native agent with an active run,
    it injects via run_handle.run_ctx.injection_manager.inject()."""
    agent = _make_acp_agent()
    await _setup_session_with_agent(controller, "sess-5", agent, mock_pool)

    # Create a run_ctx with a mocked injection_manager
    run_ctx = AgentRunContext()
    run_ctx.injection_manager.inject = MagicMock()
    run_ctx.injection_manager.queue = MagicMock()

    run_handle = _make_run_handle("sess-5", "acp", run_ctx=run_ctx)
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-5")
    assert session is not None
    session.current_run_id = run_handle.run_id

    # RED: steer() does not exist yet → AttributeError
    await turn_runner.steer("sess-5", "acp steer message")

    run_ctx.injection_manager.inject.assert_called_once_with("acp steer message")
    run_ctx.injection_manager.queue.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6: Non-native followup → injection_manager.queue()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_non_native_followup_queues_via_injection_manager(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """When followup is called on a non-native agent with an active run,
    it queues via run_handle.run_ctx.injection_manager.queue()."""
    agent = _make_acp_agent()
    await _setup_session_with_agent(controller, "sess-6", agent, mock_pool)

    run_ctx = AgentRunContext()
    run_ctx.injection_manager.inject = MagicMock()
    run_ctx.injection_manager.queue = MagicMock()

    run_handle = _make_run_handle("sess-6", "acp", run_ctx=run_ctx)
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-6")
    assert session is not None
    session.current_run_id = run_handle.run_id

    # RED: followup() does not exist yet → AttributeError
    await turn_runner.followup("sess-6", "acp followup message")

    run_ctx.injection_manager.queue.assert_called_once_with("acp followup message")
    run_ctx.injection_manager.inject.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7: Native steer active → enqueue(asap), queued_steer_messages untouched
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_native_steer_active_does_not_touch_queued_steer_messages(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """When steer is called on a native agent with an active AgentRun,
    it enqueues via agent_run.enqueue(asap) and does NOT touch
    queued_steer_messages — mid-turn injection, no re-iteration."""
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-7", agent, mock_pool)

    mock_agent_run = MagicMock()
    mock_agent_run.enqueue = MagicMock()
    run_ctx = AgentRunContext()
    run_handle = _make_run_handle("sess-7", "native", run_ctx=run_ctx)
    run_handle.active_agent_run = mock_agent_run
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-7")
    assert session is not None
    session.current_run_id = run_handle.run_id

    result = await turn_runner.steer("sess-7", "mid-turn steer")

    assert result is True
    mock_agent_run.enqueue.assert_called_once_with("mid-turn steer", priority="asap")
    assert run_ctx.queued_steer_messages == []


# ---------------------------------------------------------------------------
# Test 8: Native steer — agent_run is None, run not completed → queued_steer_messages
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_native_steer_post_iteration_queue(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """When steer is called and agent_run is None (RunExecutor in wait loop)
    but run_ctx.completed is False, the message is appended to
    queued_steer_messages and returns False."""
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-8", agent, mock_pool)

    run_ctx = AgentRunContext()
    run_ctx.completed = False
    run_handle = _make_run_handle("sess-8", "native", run_ctx=run_ctx)
    run_handle.active_agent_run = None  # RunExecutor between iterations
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-8")
    assert session is not None
    session.current_run_id = run_handle.run_id

    result = await turn_runner.steer("sess-8", "post-iteration steer")

    assert result is False
    assert run_ctx.queued_steer_messages == ["post-iteration steer"]
    # _post_turn_injections should NOT be used in this path
    assert "sess-8" not in turn_runner._post_turn_injections


# ---------------------------------------------------------------------------
# Test 9: Native steer — agent_run is None, run completed → _post_turn_injections fallback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_native_steer_completed_run_fallback(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """When steer is called and agent_run is None AND run_ctx.completed is True,
    the message falls through to _post_turn_injections (existing behavior)."""
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-9", agent, mock_pool)

    run_ctx = AgentRunContext()
    run_ctx.completed = True
    run_handle = _make_run_handle("sess-9", "native", run_ctx=run_ctx)
    run_handle.active_agent_run = None
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-9")
    assert session is not None
    session.current_run_id = run_handle.run_id

    # Mock auto-resume to avoid side effects
    turn_runner._safe_auto_resume = AsyncMock()  # type: ignore[method-assign]

    result = await turn_runner.steer("sess-9", "completed-run steer")

    assert result is False
    assert turn_runner._post_turn_injections["sess-9"] == ["completed-run steer"]
    # queued_steer_messages should NOT be used when this path
    assert run_ctx.queued_steer_messages == []
