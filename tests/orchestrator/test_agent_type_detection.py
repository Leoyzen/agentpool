"""Tests for agent type detection using AGENT_TYPE ClassVar instead of metadata.

The agent_type on RunHandle should come from ``agent.AGENT_TYPE`` (the ClassVar)
rather than ``session.metadata["agent_type"]`` which may be missing or stale.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentpool.orchestrator.core import SessionController


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
