"""Tests for agent type detection using AGENT_TYPE ClassVar instead of metadata.

The agent_type on RunHandle should come from ``agent.AGENT_TYPE`` (the ClassVar)
rather than ``session.metadata["agent_type"]`` which may be missing or stale.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentpool.orchestrator.core import SessionController
from agentpool.orchestrator.run import RunHandle, RunStatus


pytestmark = pytest.mark.unit


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
    """Return a SessionController backed by the mock pool."""
    return SessionController(pool=mock_pool)


@pytest.mark.anyio
async def test_create_run_uses_agent_type_classvar(controller: SessionController) -> None:
    """_create_run uses agent.AGENT_TYPE when agent is provided as optional parameter.

    Session is created WITHOUT agent_type in metadata, so the old path
    (session.metadata.get("agent_type", "unknown")) would return "unknown".
    After the fix, passing ``agent`` with ``AGENT_TYPE = "native"`` causes
    the RunHandle to carry ``agent_type = "native"``.
    """
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    # Mock agent that exposes AGENT_TYPE ClassVar
    mock_agent = MagicMock()
    mock_agent.AGENT_TYPE = "native"

    # RED: This will fail because _create_run() does not accept agent= yet.
    handle = controller._create_run("sess-1", "hello", agent=mock_agent)

    assert handle.agent_type == "native"
    assert isinstance(handle, RunHandle)
    assert handle.session_id == "sess-1"
    assert handle.status == RunStatus.pending
