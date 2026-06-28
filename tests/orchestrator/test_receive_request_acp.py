"""Tests for SessionController.receive_request() ACP RunHandle path.

Covers three scenarios:
1. ACPAgent + idle -> creates RunHandle.
2. ACPAgent + busy + asap -> calls RunHandle.steer().
3. ACPAgent + busy + when_idle -> calls RunHandle.followup().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.acp_agent import ACPAgent
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
def mock_acp_agent() -> MagicMock:
    """Return a MagicMock that isinstance-checks as ACPAgent."""
    agent = MagicMock(spec=ACPAgent)
    agent.AGENT_TYPE = "acp"
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
# Test 1: ACPAgent + idle -> creates RunHandle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_acp_flag_on_idle_creates_run_handle(
    controller: SessionController,
    event_bus: EventBus,
    mock_acp_agent: MagicMock,
) -> None:
    """When session is idle, a RunHandle is created."""
    controller._event_bus = event_bus
    _setup_session(controller, "sess-1", mock_acp_agent)

    # Patch _consume_run so asyncio.create_task doesn't block
    controller._consume_run = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await controller.receive_request("sess-1", "hello")

    assert result is not None
    assert isinstance(result, RunHandle)
    assert result.agent is mock_acp_agent
    assert result.event_bus is event_bus
    assert result.session is controller.get_session("sess-1")
    assert result.run_id in controller._runs
    session = controller.get_session("sess-1")
    assert session is not None
    assert session.current_run_id == result.run_id


# ---------------------------------------------------------------------------
# Test 2: ACPAgent + busy + asap -> calls steer()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_acp_flag_on_busy_asap_calls_steer(
    controller: SessionController,
    event_bus: EventBus,
    mock_acp_agent: MagicMock,
) -> None:
    """When busy with asap, RunHandle.steer() is called."""
    controller._event_bus = event_bus
    _setup_session(controller, "sess-2", mock_acp_agent)

    existing_run = MagicMock(spec=RunHandle)
    existing_run.steer = MagicMock(return_value=True)
    existing_run.followup = MagicMock(return_value=True)
    existing_run.run_id = "existing-run-id"
    controller._runs["existing-run-id"] = existing_run
    controller.get_session("sess-2").current_run_id = "existing-run-id"  # type: ignore[union-attr]

    await controller.receive_request("sess-2", "urgent", priority="asap")

    existing_run.steer.assert_called_once_with("urgent")
    existing_run.followup.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: ACPAgent + busy + when_idle -> calls followup()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_acp_flag_on_busy_when_idle_calls_followup(
    controller: SessionController,
    event_bus: EventBus,
    mock_acp_agent: MagicMock,
) -> None:
    """When busy with when_idle, followup() is called."""
    controller._event_bus = event_bus
    _setup_session(controller, "sess-3", mock_acp_agent)

    existing_run = MagicMock(spec=RunHandle)
    existing_run.steer = MagicMock(return_value=True)
    existing_run.followup = MagicMock(return_value=True)
    existing_run.run_id = "existing-run-id"
    controller._runs["existing-run-id"] = existing_run
    controller.get_session("sess-3").current_run_id = "existing-run-id"  # type: ignore[union-attr]

    await controller.receive_request("sess-3", "later", priority="when_idle")

    existing_run.followup.assert_called_once_with("later")
    existing_run.steer.assert_not_called()
