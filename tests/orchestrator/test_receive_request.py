"""Tests for SessionController.receive_request() RunHandle path.

Covers five scenarios:
1. Flag ON + idle session -> creates RunHandle, registers in _runs.
2. Flag ON + busy session + asap -> calls RunHandle.steer().
3. Flag ON + busy session + when_idle -> calls RunHandle.followup().
4. Session not found -> returns None.
5. Session closing -> returns None.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.orchestrator.core import EventBus, SessionController
from agentpool.orchestrator.run import RunHandle


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
def event_bus() -> EventBus:
    """Return a real EventBus for testing."""
    return EventBus()


@pytest.fixture
def mock_agent() -> MagicMock:
    """Return a MagicMock simulating a native Agent (AGENT_TYPE = 'native')."""
    agent = MagicMock()
    agent.AGENT_TYPE = "native"
    return agent


def _setup_session(
    controller: SessionController,
    session_id: str,
    agent: MagicMock,
) -> None:
    """Create a session and register an agent for it."""
    import asyncio

    controller._sessions[session_id] = MagicMock()
    controller._sessions[session_id].session_id = session_id
    controller._sessions[session_id].current_run_id = None
    controller._sessions[session_id].closing = False
    controller._sessions[session_id].is_closing = False
    controller._sessions[session_id]._request_lock = asyncio.Lock()
    controller._sessions[session_id].turn_lock = asyncio.Lock()
    controller._sessions[session_id].input_provider = None
    controller._session_agents[session_id] = agent


# ---------------------------------------------------------------------------
# Test 1: Flag ON + idle -> creates RunHandle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_on_idle_creates_run_handle(
    controller: SessionController,
    event_bus: EventBus,
    mock_agent: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is ON and session is idle, a RunHandle is created and registered."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    controller._event_bus = event_bus
    _setup_session(controller, "sess-1", mock_agent)

    # Patch _use_run_turn to return True (bypass isinstance check)
    controller._use_run_turn = lambda _agent: True  # type: ignore[method-assign]

    # Patch _consume_run so asyncio.create_task doesn't block
    controller._consume_run = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await controller.receive_request("sess-1", "hello")

    assert result is not None
    assert isinstance(result, RunHandle)
    assert result.agent is mock_agent
    assert result.event_bus is event_bus
    assert result.session is controller.get_session("sess-1")
    assert result.run_id in controller._runs
    session = controller.get_session("sess-1")
    assert session is not None
    assert session.current_run_id == result.run_id


# ---------------------------------------------------------------------------
# Test 2: Flag ON + busy + asap -> calls steer()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_on_busy_asap_calls_steer(
    controller: SessionController,
    event_bus: EventBus,
    mock_agent: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is ON and session is busy with asap, RunHandle.steer() is called."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    controller._event_bus = event_bus
    _setup_session(controller, "sess-2", mock_agent)

    # Simulate an active run
    existing_run = MagicMock(spec=RunHandle)
    existing_run.steer = MagicMock(return_value=True)
    existing_run.followup = MagicMock(return_value=True)
    existing_run.run_id = "existing-run-id"
    controller._runs["existing-run-id"] = existing_run
    controller.get_session("sess-2").current_run_id = "existing-run-id"  # type: ignore[union-attr]

    controller._use_run_turn = lambda _agent: True  # type: ignore[method-assign]

    await controller.receive_request("sess-2", "urgent", priority="asap")

    existing_run.steer.assert_called_once_with("urgent")
    existing_run.followup.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Flag ON + busy + when_idle -> calls followup()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_on_busy_when_idle_calls_followup(
    controller: SessionController,
    event_bus: EventBus,
    mock_agent: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is ON and session is busy with when_idle, RunHandle.followup() is called."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    controller._event_bus = event_bus
    _setup_session(controller, "sess-3", mock_agent)

    existing_run = MagicMock(spec=RunHandle)
    existing_run.steer = MagicMock(return_value=True)
    existing_run.followup = MagicMock(return_value=True)
    existing_run.run_id = "existing-run-id"
    controller._runs["existing-run-id"] = existing_run
    controller.get_session("sess-3").current_run_id = "existing-run-id"  # type: ignore[union-attr]

    controller._use_run_turn = lambda _agent: True  # type: ignore[method-assign]

    await controller.receive_request("sess-3", "later", priority="when_idle")

    existing_run.followup.assert_called_once_with("later")
    existing_run.steer.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: Session not found -> returns None
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_session_not_found_returns_none(
    controller: SessionController,
    event_bus: EventBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the session does not exist, receive_request returns None."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    controller._event_bus = event_bus
    controller._use_run_turn = lambda _agent: True  # type: ignore[method-assign]

    result = await controller.receive_request("nonexistent-session", "hello")

    assert result is None


# ---------------------------------------------------------------------------
# Test 5: Session closing -> returns None
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_session_closing_returns_none(
    controller: SessionController,
    event_bus: EventBus,
    mock_agent: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the session is closing, receive_request returns None."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    controller._event_bus = event_bus
    _setup_session(controller, "sess-closing", mock_agent)
    controller._use_run_turn = lambda _agent: True  # type: ignore[method-assign]

    # Mark session as closing closing
    controller._sessions["sess-closing"].closing = True

    result = await controller.receive_request("sess-closing", "hello")

    assert result is None


# ---------------------------------------------------------------------------
# Tests from PR #64 review (receive_request behavior)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receive_request_uses_get_or_create_session_agent() -> None:
    """receive_request should use get_or_create_session_agent, not .get().

    When agent is not yet cached (new top-level sessions), .get()
    returns None and receive_request silently does nothing.
    """
    from agentpool.orchestrator.core import SessionController

    mock_pool = MagicMock()
    mock_pool.main_agent = MagicMock()
    mock_pool.main_agent.name = "main-agent"
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}

    controller = SessionController(pool=mock_pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    mock_agent = MagicMock()
    mock_agent.AGENT_TYPE = "native"

    session_id = "sess-lazy"
    controller._sessions[session_id] = MagicMock()
    controller._sessions[session_id].session_id = session_id
    controller._sessions[session_id].current_run_id = None
    controller._sessions[session_id].closing = False
    controller._sessions[session_id].is_closing = False
    controller._sessions[session_id]._request_lock = asyncio.Lock()
    controller._sessions[session_id].turn_lock = asyncio.Lock()
    controller._sessions[session_id].input_provider = None
    # Deliberately do NOT pre-register agent in _session_agents

    # Mock get_or_create_session_agent to return the agent
    controller.get_or_create_session_agent = AsyncMock(return_value=mock_agent)  # type: ignore[method-assign]
    controller._consume_run = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await controller.receive_request(session_id, "hello")

    # get_or_create_session_agent should have been called
    controller.get_or_create_session_agent.assert_called_once_with(session_id, input_provider=None)
    assert result is not None, (
        "receive_request returned None because agent was not in _session_agents cache"
    )


@pytest.mark.asyncio
async def test_receive_request_list_content_joins_elements() -> None:
    """receive_request must join list elements, not str(["hello"]).

    str(["hello"]) produces "['hello']" which is not what the model
    should receive. Lists should be joined with spaces.
    """
    from agentpool.orchestrator.core import SessionController

    mock_pool = MagicMock()
    mock_pool.main_agent = MagicMock()
    mock_pool.main_agent.name = "main-agent"
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}

    controller = SessionController(pool=mock_pool)
    controller._event_bus = EventBus()

    mock_agent = MagicMock()
    mock_agent.AGENT_TYPE = "native"

    session_id = "sess-list-content"
    controller._sessions[session_id] = MagicMock()
    controller._sessions[session_id].session_id = session_id
    controller._sessions[session_id].current_run_id = None
    controller._sessions[session_id].closing = False
    controller._sessions[session_id].is_closing = False
    controller._sessions[session_id]._request_lock = asyncio.Lock()
    controller._sessions[session_id].turn_lock = asyncio.Lock()
    controller._sessions[session_id].input_provider = None
    controller._session_agents[session_id] = mock_agent

    captured_content: list[str] = []

    async def _capture(run_handle: Any, initial_prompt: str) -> None:
        captured_content.append(initial_prompt)

    controller._consume_run = _capture  # type: ignore[method-assign]
    controller.get_or_create_session_agent = AsyncMock(return_value=mock_agent)  # type: ignore[method-assign]

    # Pass a list with actual content
    await controller.receive_request(session_id, ["hello", "world"])

    await asyncio.sleep(0.1)

    assert len(captured_content) > 0
    assert captured_content[0] == "hello world", (
        f"Expected 'hello world', got {captured_content[0]!r} — list was not properly joined"
    )
    assert "['hello'" not in captured_content[0], (
        "List was stringified with repr() instead of joined"
    )
