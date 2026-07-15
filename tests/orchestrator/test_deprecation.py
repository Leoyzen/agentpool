"""Unit tests for Task 12: Deprecation + migration.

Tests that DeprecationWarning is emitted by:
- SessionPool.receive_request()
- SessionController.receive_request()
- RunLoopDelegationService.spawn_subagent()
- RunLoopDelegationService.get_available_agents()

And that SubagentCapability uses run_agent() when session_pool
is available, falling back to delegation when it is None.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
import warnings

import pytest

from agentpool.capabilities.runloop_delegation import RunLoopDelegationService
from agentpool.lifecycle.types import DeliveryMode
from agentpool.orchestrator.core import SessionPool
from agentpool.orchestrator.session_controller import SessionController


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool."""
    pool = MagicMock()
    pool.storage = None
    pool.main_agent_name = "default"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    pool._config_file_path = None
    pool.get_context = MagicMock(return_value=MagicMock())
    return pool


@pytest.fixture
def session_pool(mock_pool: MagicMock) -> SessionPool:
    """Return a SessionPool backed by the mock pool."""
    return SessionPool(pool=mock_pool)


@pytest.fixture
def mock_session_state() -> MagicMock:
    """Return a mocked SessionState."""
    state = MagicMock()
    state.session_id = "test-session"
    state.current_run_id = None
    state.is_closing = False
    state.closing = False
    state.last_active_at = 0.0
    return state


# ---------------------------------------------------------------------------
# 1. SessionPool.receive_request() deprecation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_deprecation_warning(session_pool: SessionPool) -> None:
    """SessionPool.receive_request() emits DeprecationWarning."""
    session_pool.send_message = AsyncMock(return_value="msg-123")  # type: ignore[method-assign]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await session_pool.receive_request("sess-1", "hello")

    assert result == "msg-123"
    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) >= 1
    assert "deprecated" in str(dep_warnings[0].message).lower()


# ---------------------------------------------------------------------------
# 2. SessionPool.receive_request() priority mapping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_priority_mapping(session_pool: SessionPool) -> None:
    """receive_request maps 'asap' → STEER, 'when_idle' → QUEUE."""
    session_pool.send_message = AsyncMock(return_value="msg-1")  # type: ignore[method-assign]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        await session_pool.receive_request("s", "x", priority="asap")

    session_pool.send_message.assert_awaited_once_with(
        "s", "x", mode=DeliveryMode.STEER, message_id=None,
    )


# ---------------------------------------------------------------------------
# 3. SessionPool.receive_request() unknown priority
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_unknown_priority(session_pool: SessionPool) -> None:
    """Unknown priority defaults to QUEUE with extra DeprecationWarning."""
    session_pool.send_message = AsyncMock(return_value="msg-2")  # type: ignore[method-assign]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await session_pool.receive_request("s", "x", priority="bogus")

    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    # Two warnings: one for deprecation, one for unknown priority.
    assert len(dep_warnings) >= 2
    session_pool.send_message.assert_awaited_once_with(
        "s", "x", mode=DeliveryMode.QUEUE, message_id=None,
    )


# ---------------------------------------------------------------------------
# 4. RunLoopDelegationService.spawn_subagent() deprecation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delegation_service_spawn_subagent_deprecation() -> None:
    """RunLoopDelegationService.spawn_subagent() emits DeprecationWarning."""
    registry = MagicMock()
    registry.exists = MagicMock(return_value=True)
    host = MagicMock()
    host.session_pool = MagicMock()
    host.session_pool.sessions = MagicMock()
    # Make receive_request return a truthy message_id so spawn proceeds.
    host.session_pool.sessions.receive_request = AsyncMock(return_value="mid")
    child_session = MagicMock()
    child_session.current_run_id = "run-1"
    host.session_pool.sessions.get_session = MagicMock(return_value=child_session)
    run_handle = MagicMock()
    # start() must return an async iterator for `async for` to work.
    async def _empty_gen() -> Any:
        return
        yield  # pragma: no cover -- makes this an async generator
    run_handle.start = MagicMock(return_value=_empty_gen())
    host.session_pool.sessions._runs = {"run-1": run_handle}

    service = RunLoopDelegationService(registry, host, "parent-sess")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        async for _event in service.spawn_subagent("agent1", "do something"):
            pass

    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) >= 1
    assert "spawn_subagent" in str(dep_warnings[0].message).lower()


# ---------------------------------------------------------------------------
# 5. RunLoopDelegationService.get_available_agents() deprecation
# ---------------------------------------------------------------------------


def test_delegation_service_get_available_agents_deprecation() -> None:
    """RunLoopDelegationService.get_available_agents() emits DeprecationWarning."""
    registry = MagicMock()
    registry.list_names = MagicMock(return_value=["agent1", "agent2"])
    host = MagicMock()
    service = RunLoopDelegationService(registry, host, "sess-1")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = service.get_available_agents()

    assert result == ["agent1", "agent2"]
    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) >= 1
    assert "get_available_agents" in str(dep_warnings[0].message).lower()


# ---------------------------------------------------------------------------
# 6. SessionController.receive_request() deprecation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_session_controller_receive_request_deprecation(
    mock_pool: MagicMock,
) -> None:
    """SessionController.receive_request() emits DeprecationWarning."""
    controller = SessionController(mock_pool)
    controller.get_session = MagicMock(return_value=None)  # type: ignore[method-assign]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await controller.receive_request("sess-1", "hello")

    assert result is None
    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) >= 1
    assert "deprecated" in str(dep_warnings[0].message).lower()


# ---------------------------------------------------------------------------
# 7. SubagentCapability uses run_agent() when session_pool available
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subagent_capability_uses_run_agent() -> None:
    """SubagentCapability.spawn_subagent calls run_agent() when session_pool is available."""
    from agentpool.capabilities.agent_context import AgentContext
    from agentpool.capabilities.subagent_capability import SubagentCapability

    # Build a mock AgentContext with a non-None session_pool.
    session_pool = MagicMock()
    session_pool.run_agent = AsyncMock(return_value="subagent result")

    agent_registry = MagicMock()
    agent_registry.list_names = MagicMock(return_value=["a", "b"])

    host = MagicMock()
    host.session_pool = session_pool

    session = MagicMock()
    session.session_id = "parent-session"

    delegation = MagicMock()

    agent_ctx = AgentContext(
        agent_registry=agent_registry,
        delegation=delegation,
        session=session,
        scope=MagicMock(),
        host=host,
    )

    # Build a mock RunContext.
    ctx = MagicMock()
    ctx.deps = agent_ctx

    result = await SubagentCapability.spawn_subagent(ctx, "worker", "do task")

    assert result == "subagent result"
    session_pool.run_agent.assert_awaited_once_with(
        "worker", "do task", parent_session_id="parent-session",
    )
    # DelegationService should NOT be called.
    delegation.spawn_subagent.assert_not_called()


# ---------------------------------------------------------------------------
# 8. SubagentCapability falls back to delegation when session_pool is None
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subagent_capability_fallback() -> None:
    """SubagentCapability falls back to delegation when session_pool is None."""

    async def _mock_stream(name: str, prompt: str) -> Any:
        yield "chunk1"
        yield "chunk2"

    from agentpool.capabilities.agent_context import AgentContext
    from agentpool.capabilities.subagent_capability import SubagentCapability

    host = MagicMock()
    host.session_pool = None

    session = MagicMock()
    session.session_id = "parent-session"

    delegation = MagicMock()
    delegation.spawn_subagent = _mock_stream

    agent_registry = MagicMock()
    agent_registry.list_names = MagicMock(return_value=["a"])

    agent_ctx = AgentContext(
        agent_registry=agent_registry,
        delegation=delegation,
        session=session,
        scope=MagicMock(),
        host=host,
    )

    ctx = MagicMock()
    ctx.deps = agent_ctx

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await SubagentCapability.spawn_subagent(ctx, "worker", "do task")

    assert result == "chunk1\nchunk2"
    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) >= 1
    assert "fell back" in str(dep_warnings[0].message).lower()
