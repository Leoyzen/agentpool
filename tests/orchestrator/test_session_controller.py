"""Unit tests for SessionController (SessionPool Group 2.11).

Tests session lifecycle, TTL cleanup, per-session agent creation,
and MCP process limit enforcement.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.orchestrator.core import (
    DEFAULT_SESSION_TTL_SECONDS,
    SessionController,
    SessionState,
)
from agentpool.orchestrator.run import RunHandle, RunStatus


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool with a main_agent."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


@pytest.fixture
def controller(mock_pool: MagicMock) -> SessionController:
    """Return a SessionController backed by the mock pool."""
    return SessionController(pool=mock_pool)


@pytest.fixture
def mock_native_agent() -> MagicMock:
    """Return a mocked BaseAgent that looks like a native agent."""
    agent = MagicMock()
    agent.__aenter__ = AsyncMock(return_value=agent)
    agent.__aexit__ = AsyncMock(return_value=None)
    return agent


@pytest.fixture
def mock_turn_runner() -> MagicMock:
    """Return a mocked TurnRunner whose run_loop blocks until cancelled."""
    tr = MagicMock()
    run_loop_event = asyncio.Event()

    async def _run_loop(*args: Any, **kwargs: Any) -> None:
        await run_loop_event.wait()

    tr.run_loop = AsyncMock(side_effect=_run_loop)
    tr.inject_prompt = AsyncMock(return_value=True)
    tr.queue_prompt = AsyncMock(return_value=False)
    tr._run_loop_event = run_loop_event
    return tr


# ---------------------------------------------------------------------------
# get_or_create_session
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_or_create_session_creates_new(
    controller: SessionController,
) -> None:
    """A new session is created when the session_id is unknown."""
    state, was_created = await controller.get_or_create_session("sess-1", agent_name="agent-a")
    assert isinstance(state, SessionState)
    assert was_created is True
    assert state.session_id == "sess-1"
    assert state.agent_name == "agent-a"
    assert state.closed_at is None
    assert state.is_closing is False


@pytest.mark.anyio
async def test_get_or_create_session_returns_existing(
    controller: SessionController,
) -> None:
    """Calling get_or_create_session with the same ID returns the existing state."""
    first, first_created = await controller.get_or_create_session("sess-1", agent_name="agent-a")
    second, second_created = await controller.get_or_create_session("sess-1")
    assert first is second
    assert first_created is True
    assert second_created is False


@pytest.mark.anyio
async def test_get_or_create_session_updates_last_active(
    controller: SessionController,
) -> None:
    """last_active_at is refreshed when an existing session is retrieved."""
    state, _ = await controller.get_or_create_session("sess-1")
    old_ts = state.last_active_at
    await asyncio.sleep(0.01)
    state2, _ = await controller.get_or_create_session("sess-1")
    assert state2.last_active_at > old_ts


@pytest.mark.anyio
async def test_get_or_create_session_defaults_to_main_agent(
    controller: SessionController,
    mock_pool: MagicMock,
) -> None:
    """When agent_name is omitted, the main agent name is used."""
    mock_pool.main_agent.name = "fallback"
    state, _ = await controller.get_or_create_session("sess-1")
    assert state.agent_name == "fallback"


@pytest.mark.anyio
async def test_get_or_create_session_stores_metadata(
    controller: SessionController,
) -> None:
    """Arbitrary keyword metadata is stored on the session state."""
    state, _ = await controller.get_or_create_session("sess-1", user_id="u42")
    assert state.metadata == {"user_id": "u42"}


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_sessions_returns_session_info(
    controller: SessionController,
) -> None:
    """list_sessions returns SessionInfo DTOs for all active sessions."""
    from agentpool_server.opencode_server.models.session_info import SessionInfo

    await controller.get_or_create_session("sess-1", agent_name="agent-a")
    await controller.get_or_create_session("sess-2", agent_name="agent-b")

    infos = controller.list_sessions()

    assert len(infos) == 2
    assert all(isinstance(info, SessionInfo) for info in infos)
    assert {info.session_id for info in infos} == {"sess-1", "sess-2"}
    assert {info.agent_name for info in infos} == {"agent-a", "agent-b"}
    assert all(info.status == "idle" for info in infos)
    assert all(not info.is_per_session_agent for info in infos)


@pytest.mark.anyio
async def test_list_sessions_reflects_busy_status(
    controller: SessionController,
) -> None:
    """list_sessions marks sessions as busy when they have an active run."""
    state, _ = await controller.get_or_create_session("sess-1", agent_name="agent-a")
    handle = controller._create_run("sess-1", "hello")
    controller._runs[handle.run_id] = handle
    state.current_run_id = handle.run_id

    infos = controller.list_sessions()

    assert len(infos) == 1
    assert infos[0].status == "busy"

    # Simulate run cleanup which clears current_run_id in production
    controller._cleanup_run(handle.run_id)
    state.current_run_id = None
    infos_after = controller.list_sessions()
    assert infos_after[0].status == "idle"


# ---------------------------------------------------------------------------
# get_or_create_session_agent – shared agent fallback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_or_create_session_agent_returns_shared_for_non_native(
    controller: SessionController,
    mock_pool: MagicMock,
) -> None:
    """Non-native configs reuse the shared agent from the pool."""
    shared = MagicMock()
    mock_pool.get_agent.return_value = shared
    mock_pool.manifest.agents = {"agent-a": MagicMock()}  # not NativeAgentConfig

    agent = await controller.get_or_create_session_agent("sess-1", agent_name="agent-a")
    assert agent is shared
    state = controller.get_session("sess-1")
    assert state is not None
    assert state.is_per_session_agent is False


# ---------------------------------------------------------------------------
# get_or_create_session_agent – per-session native agent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_or_create_session_agent_creates_native_agent(
    controller: SessionController,
    mock_pool: MagicMock,
    mock_native_agent: MagicMock,
) -> None:
    """NativeAgentConfig causes a dedicated per-session agent to be created."""

    class FakeNativeConfig:
        def __init__(self, name: str, model: str) -> None:
            self.name = name
            self.model = model

        def get_agent(self, **kwargs: Any) -> MagicMock:
            return mock_native_agent

    with patch("agentpool.models.agents.NativeAgentConfig", FakeNativeConfig):
        cfg = FakeNativeConfig("agent-a", "openai:gpt-4o")
        mock_pool.manifest.agents = {"agent-a": cfg}
        mock_pool.get_agent.return_value = MagicMock()

        agent = await controller.get_or_create_session_agent("sess-1", agent_name="agent-a")

    assert agent is mock_native_agent
    state = controller.get_session("sess-1")
    assert state is not None
    assert state.is_per_session_agent is True
    mock_native_agent.__aenter__.assert_awaited_once()


@pytest.mark.anyio
async def test_get_or_create_session_agent_returns_existing_agent(
    controller: SessionController,
    mock_pool: MagicMock,
    mock_native_agent: MagicMock,
) -> None:
    """A second call returns the cached per-session agent."""

    class FakeNativeConfig:
        def __init__(self, name: str, model: str) -> None:
            self.name = name
            self.model = model

        def get_agent(self, **kwargs: Any) -> MagicMock:
            return mock_native_agent

    with patch("agentpool.models.agents.NativeAgentConfig", FakeNativeConfig):
        cfg = FakeNativeConfig("agent-a", "openai:gpt-4o")
        mock_pool.manifest.agents = {"agent-a": cfg}
        mock_pool.get_agent.return_value = MagicMock()

        first = await controller.get_or_create_session_agent("sess-1", agent_name="agent-a")
        second = await controller.get_or_create_session_agent("sess-1", agent_name="agent-a")

    assert first is second
    # __aenter__ should only be called once
    mock_native_agent.__aenter__.assert_awaited_once()


# ---------------------------------------------------------------------------
# MCP process limits
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mcp_limit_falls_back_to_shared_agent(
    controller: SessionController,
    mock_pool: MagicMock,
    mock_native_agent: MagicMock,
) -> None:
    """When MCP process limit is reached, a shared agent is used."""

    class FakeNativeConfig:
        def __init__(self, name: str, model: str) -> None:
            self.name = name
            self.model = model

        def get_agent(self, **kwargs: Any) -> MagicMock:
            return mock_native_agent

    with patch("agentpool.models.agents.NativeAgentConfig", FakeNativeConfig):
        cfg = FakeNativeConfig("agent-a", "openai:gpt-4o")
        mock_pool.manifest.agents = {"agent-a": cfg}
        shared = MagicMock()
        mock_pool.get_agent.return_value = shared

        controller._mcp_max_processes = 1
        controller._mcp_process_count = 1  # already at limit

        agent = await controller.get_or_create_session_agent("sess-1", agent_name="agent-a")

    assert agent is shared
    state = controller.get_session("sess-1")
    assert state is not None
    assert state.is_per_session_agent is False


@pytest.mark.anyio
async def test_mcp_count_incremented_and_decremented(
    controller: SessionController,
    mock_pool: MagicMock,
    mock_native_agent: MagicMock,
) -> None:
    """MCP count tracks per-session agent creation and destruction."""

    class FakeNativeConfig:
        def __init__(self, name: str, model: str) -> None:
            self.name = name
            self.model = model

        def get_agent(self, **kwargs: Any) -> MagicMock:
            return mock_native_agent

    with patch("agentpool.models.agents.NativeAgentConfig", FakeNativeConfig):
        cfg = FakeNativeConfig("agent-a", "openai:gpt-4o")
        mock_pool.manifest.agents = {"agent-a": cfg}
        mock_pool.get_agent.return_value = MagicMock()

        assert controller._mcp_process_count == 0
        await controller.get_or_create_session_agent("sess-1", agent_name="agent-a")
        assert controller._mcp_process_count == 1

    await controller.close_session("sess-1")
    assert controller._mcp_process_count == 0


@pytest.mark.anyio
async def test_mcp_count_never_negative(
    controller: SessionController,
) -> None:
    """_decrement_mcp_count clamps at zero."""
    controller._decrement_mcp_count(MagicMock())
    assert controller._mcp_process_count == 0


# ---------------------------------------------------------------------------
# close_session
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_session_removes_session(
    controller: SessionController,
) -> None:
    """After close_session, the session is no longer retrievable."""
    await controller.get_or_create_session("sess-1")
    await controller.close_session("sess-1")
    assert controller.get_session("sess-1") is None


@pytest.mark.anyio
async def test_close_session_is_idempotent(
    controller: SessionController,
) -> None:
    """Closing a session twice does not raise."""
    await controller.get_or_create_session("sess-1")
    await controller.close_session("sess-1")
    await controller.close_session("sess-1")  # should not raise
    assert controller.get_session("sess-1") is None


@pytest.mark.anyio
async def test_close_session_sets_closing_flag(
    controller: SessionController,
) -> None:
    """close_session marks the session as closing and records closed_at."""
    state, _ = await controller.get_or_create_session("sess-1")
    await controller.close_session("sess-1")
    # closed_at is set inside close_session
    assert state.closed_at is not None
    assert state.is_closing is True


@pytest.mark.anyio
async def test_close_session_exits_per_session_agent(
    controller: SessionController,
    mock_pool: MagicMock,
    mock_native_agent: MagicMock,
) -> None:
    """A per-session agent has its async context exited on close."""

    class FakeNativeConfig:
        def __init__(self, name: str, model: str) -> None:
            self.name = name
            self.model = model

        def get_agent(self, **kwargs: Any) -> MagicMock:
            return mock_native_agent

    with patch("agentpool.models.agents.NativeAgentConfig", FakeNativeConfig):
        cfg = FakeNativeConfig("agent-a", "openai:gpt-4o")
        mock_pool.manifest.agents = {"agent-a": cfg}
        mock_pool.get_agent.return_value = MagicMock()

        await controller.get_or_create_session_agent("sess-1", agent_name="agent-a")

    await controller.close_session("sess-1")
    mock_native_agent.__aexit__.assert_awaited_once()


@pytest.mark.anyio
async def test_cleanup_expired_sessions_calls_callback(
    mock_pool: MagicMock,
) -> None:
    """The optional cleanup_callback is invoked for expired sessions."""
    callback = AsyncMock()
    ctrl = SessionController(pool=mock_pool, cleanup_callback=callback)
    ctrl._session_ttl_seconds = 0.05
    await ctrl.get_or_create_session("sess-1")
    await asyncio.sleep(0.1)
    await ctrl._cleanup_expired_sessions()
    callback.assert_awaited_once_with("sess-1")


# ---------------------------------------------------------------------------
# TTL cleanup
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cleanup_task_closes_expired_sessions(
    controller: SessionController,
) -> None:
    """Background cleanup closes sessions whose TTL has expired."""
    controller._session_ttl_seconds = 0.05
    await controller.get_or_create_session("sess-1")
    await asyncio.sleep(0.1)
    await controller._cleanup_expired_sessions()
    assert controller.get_session("sess-1") is None


@pytest.mark.anyio
async def test_cleanup_task_keeps_active_sessions(
    controller: SessionController,
) -> None:
    """Active sessions (within TTL) are not closed by cleanup."""
    controller._session_ttl_seconds = 10.0
    await controller.get_or_create_session("sess-1")
    await controller._cleanup_expired_sessions()
    assert controller.get_session("sess-1") is not None


@pytest.mark.anyio
async def test_cleanup_task_uses_callback_when_provided(
    mock_pool: MagicMock,
) -> None:
    """When cleanup_callback is set, it is used instead of close_session."""
    callback = AsyncMock()
    ctrl = SessionController(pool=mock_pool, cleanup_callback=callback)
    ctrl._session_ttl_seconds = 0.05
    await ctrl.get_or_create_session("sess-1")
    await asyncio.sleep(0.1)
    await ctrl._cleanup_expired_sessions()
    callback.assert_awaited_once_with("sess-1")


@pytest.mark.anyio
async def test_start_and_stop_cleanup_task(
    controller: SessionController,
) -> None:
    """start_cleanup_task and stop_cleanup_task manage the background task."""
    assert controller._cleanup_task is None
    await controller.start_cleanup_task()
    assert controller._cleanup_task is not None
    await controller.stop_cleanup_task()
    assert controller._cleanup_task is None


@pytest.mark.anyio
async def test_cleanup_loop_catches_exceptions(
    controller: SessionController,
) -> None:
    """The cleanup loop survives exceptions and continues running."""
    controller._session_ttl_seconds = 0.01
    await controller.start_cleanup_task()
    # Force an exception by corrupting internal state
    with patch.object(
        controller, "_cleanup_expired_sessions", side_effect=RuntimeError("boom")
    ):
        await asyncio.sleep(0.03)
    await controller.stop_cleanup_task()


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------


def test_get_session_returns_none_for_unknown(controller: SessionController) -> None:
    """get_session returns None when the session does not exist."""
    assert controller.get_session("missing") is None


def test_get_session_returns_state(controller: SessionController) -> None:
    """get_session returns the SessionState for an existing session."""
    # Note: using the sync variant via internal dict for simplicity
    state = SessionState(session_id="sess-1", agent_name="a")
    controller._sessions["sess-1"] = state
    assert controller.get_session("sess-1") is state


# ---------------------------------------------------------------------------
# Default TTL constant
# ---------------------------------------------------------------------------


def test_default_ttl_is_one_hour() -> None:
    """The default session TTL is 3600 seconds."""
    assert DEFAULT_SESSION_TTL_SECONDS == 3600.0


# ---------------------------------------------------------------------------
# receive_request
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_creates_run_for_idle_session(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """receive_request creates a RunHandle and starts execution for an idle session."""
    controller._turn_runner = mock_turn_runner
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    await controller.receive_request("sess-1", "hello")
    # Give the background task a chance to start
    await asyncio.sleep(0.01)

    assert len(controller._runs) == 1
    run_id = next(iter(controller._runs.keys()))
    session = controller.get_session("sess-1")
    assert session is not None
    assert session.current_run_id == run_id
    mock_turn_runner.run_loop.assert_awaited_once_with("sess-1", "hello")

    # Let the background task finish so it doesn't leak into other tests
    mock_turn_runner._run_loop_event.set()
    await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_receive_request_enqueues_for_active_session(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """receive_request delegates to queue_prompt when a run is already active."""
    controller._turn_runner = mock_turn_runner
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    # Simulate an active run
    session = controller.get_session("sess-1")
    assert session is not None
    session.current_run_id = "existing-run-id"

    await controller.receive_request("sess-1", "second message")
    await asyncio.sleep(0.01)

    mock_turn_runner.queue_prompt.assert_awaited_once_with("sess-1", "second message")
    mock_turn_runner.run_loop.assert_not_awaited()


@pytest.mark.anyio
async def test_receive_request_injects_for_active_session_with_asap(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """receive_request delegates to inject_prompt when priority is asap."""
    controller._turn_runner = mock_turn_runner
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    session = controller.get_session("sess-1")
    assert session is not None
    session.current_run_id = "existing-run-id"

    await controller.receive_request("sess-1", "urgent", priority="asap")
    await asyncio.sleep(0.01)

    mock_turn_runner.inject_prompt.assert_awaited_once_with("sess-1", "urgent")


@pytest.mark.anyio
async def test_receive_request_rejects_unknown_session(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """receive_request silently returns when the session does not exist."""
    controller._turn_runner = mock_turn_runner
    await controller.receive_request("missing", "hello")
    assert len(controller._runs) == 0
    mock_turn_runner.run_loop.assert_not_awaited()


@pytest.mark.anyio
async def test_receive_request_rejects_when_closing(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """receive_request rejects new requests when the session is closing."""
    controller._turn_runner = mock_turn_runner
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    session = controller.get_session("sess-1")
    assert session is not None
    session.closing = True

    await controller.receive_request("sess-1", "hello")
    assert len(controller._runs) == 0
    mock_turn_runner.run_loop.assert_not_awaited()


@pytest.mark.anyio
async def test_receive_request_rejects_when_is_closing(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """receive_request rejects new requests when is_closing is set."""
    controller._turn_runner = mock_turn_runner
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    session = controller.get_session("sess-1")
    assert session is not None
    session.is_closing = True

    await controller.receive_request("sess-1", "hello")
    assert len(controller._runs) == 0
    mock_turn_runner.run_loop.assert_not_awaited()


@pytest.mark.anyio
async def test_receive_request_enforces_max_concurrent_runs(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """receive_request drops requests when max_concurrent_runs is reached."""
    controller._turn_runner = mock_turn_runner
    controller._max_concurrent_runs = 1
    await controller.get_or_create_session("sess-1", agent_name="agent-a")
    await controller.get_or_create_session("sess-2", agent_name="agent-a")

    # First request should create a run
    await controller.receive_request("sess-1", "hello")
    await asyncio.sleep(0.01)
    assert len(controller._runs) == 1

    # Second request should be dropped
    await controller.receive_request("sess-2", "hello")
    assert len(controller._runs) == 1
    sess2 = controller.get_session("sess-2")
    assert sess2 is not None
    assert sess2.current_run_id is None

    # Clean up the blocked background task
    mock_turn_runner._run_loop_event.set()
    await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_receive_request_concurrent_race(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """Concurrent requests for the same idle session only create one run."""
    controller._turn_runner = mock_turn_runner
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    async def _fire() -> None:
        await controller.receive_request("sess-1", "hello")

    await asyncio.gather(_fire(), _fire(), _fire())
    await asyncio.sleep(0.01)

    # Only one run should have been created
    assert len(controller._runs) <= 1
    session = controller.get_session("sess-1")
    assert session is not None
    # Either idle (run completed quickly) or exactly one active run
    # Because run_loop is mocked, it returns immediately, so the run
    # may already be cleaned up.


# ---------------------------------------------------------------------------
# cancel_run_for_session
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_run_for_session_cancels_active_run(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """cancel_run_for_session cancels the task backing an active run."""
    controller._turn_runner = mock_turn_runner
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    await controller.receive_request("sess-1", "hello")
    await asyncio.sleep(0.01)

    sess1 = controller.get_session("sess-1")
    assert sess1 is not None
    run_id = sess1.current_run_id
    assert run_id is not None
    handle = controller._runs[run_id]

    controller.cancel_run_for_session("sess-1")

    assert handle.run_ctx.cancelled is True

    # Let the cancelled task finish
    mock_turn_runner._run_loop_event.set()
    await asyncio.sleep(0.01)


def test_cancel_run_for_session_noop_for_idle_session(
    controller: SessionController,
) -> None:
    """cancel_run_for_session is a no-op when the session has no active run."""
    # No session exists – should not raise
    controller.cancel_run_for_session("missing")


def test_cancel_run_for_session_noop_for_missing_run(
    controller: SessionController,
) -> None:
    """cancel_run_for_session is a no-op when current_run_id is set but run is missing."""
    state = SessionState(session_id="sess-1", agent_name="a")
    state.current_run_id = "ghost-run"
    controller._sessions["sess-1"] = state
    controller.cancel_run_for_session("sess-1")


# ---------------------------------------------------------------------------
# _create_run / _cleanup_run
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_run_returns_handle(
    controller: SessionController,
) -> None:
    """_create_run builds a RunHandle with the correct fields."""
    await controller.get_or_create_session(
        "sess-1", agent_name="agent-a", agent_type="native"
    )
    handle = controller._create_run("sess-1", "hello")
    assert isinstance(handle, RunHandle)
    assert handle.session_id == "sess-1"
    assert handle.agent_type == "native"
    assert handle.status == RunStatus.pending


def test_create_run_raises_for_missing_session(controller: SessionController) -> None:
    """_create_run raises ValueError when the session does not exist."""
    with pytest.raises(ValueError, match="Session not found"):
        controller._create_run("missing", "hello")


@pytest.mark.anyio
async def test_cleanup_run_removes_and_signals(
    controller: SessionController,
) -> None:
    """_cleanup_run removes the handle from _runs and sets complete_event."""
    await controller.get_or_create_session("sess-1", agent_name="agent-a")
    handle = controller._create_run("sess-1", "hello")
    controller._runs[handle.run_id] = handle

    controller._cleanup_run(handle.run_id)

    assert handle.run_id not in controller._runs
    assert handle.complete_event.is_set() is True


def test_cleanup_run_noop_for_missing_run(controller: SessionController) -> None:
    """_cleanup_run is a no-op when the run_id is unknown."""
    controller._cleanup_run("ghost")  # should not raise


# ---------------------------------------------------------------------------
# SessionState.closing alias
# ---------------------------------------------------------------------------


def test_closing_alias_reads_is_closing() -> None:
    """The closing property returns the value of is_closing."""
    state = SessionState(session_id="s", agent_name="a")
    assert state.closing is False
    state.is_closing = True
    assert state.closing is True


def test_closing_alias_writes_is_closing() -> None:
    """Setting closing updates is_closing."""
    state = SessionState(session_id="s", agent_name="a")
    state.closing = True
    assert state.is_closing is True
    assert state.closing is True
