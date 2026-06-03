"""Tests for MetricsCollector and SessionPoolMetrics.

Covers active_runs integration and active_runs_by_agent_type breakdown.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentpool.agents.events import StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import SessionPool
from agentpool.orchestrator.metrics import MetricsCollector, SessionPoolMetrics
from agentpool.orchestrator.run import RunHandle, RunStatus


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool with a main_agent."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


class TestMetricsCollectorActiveRuns:
    """Tests for MetricsCollector.active_turns and active_runs_by_agent_type."""

    @pytest.mark.anyio
    async def test_get_metrics_returns_zero_initially(self, mock_pool: MagicMock) -> None:
        """MetricsCollector should use SessionPool.active_runs for active_turns."""
        session_pool = SessionPool(mock_pool)
        collector = MetricsCollector(session_pool)

        # No active runs initially
        metrics = await collector.get_metrics()
        assert metrics.active_turns == 0
        assert metrics.active_runs_by_agent_type == {}

    @pytest.mark.anyio
    async def test_get_metrics_counts_native_vs_non_native(self, mock_pool: MagicMock) -> None:
        """active_runs_by_agent_type should count native and non-native runs."""
        session_pool = SessionPool(mock_pool)
        collector = MetricsCollector(session_pool)

        # Create two sessions: one native (per-session), one non-native
        state_native = await session_pool.sessions.get_or_create_session("sess-native")
        state_native.metadata["agent_type"] = "native"
        handle_native = RunHandle(
            run_id="run-1",
            session_id="sess-native",
            agent_type="native",
            status=RunStatus.running,
        )
        session_pool.sessions._runs["run-1"] = handle_native

        state_non_native = await session_pool.sessions.get_or_create_session("sess-non-native")
        state_non_native.metadata["agent_type"] = "non-native"
        handle_non_native = RunHandle(
            run_id="run-2",
            session_id="sess-non-native",
            agent_type="non-native",
            status=RunStatus.running,
        )
        session_pool.sessions._runs["run-2"] = handle_non_native

        metrics = await collector.get_metrics()
        assert metrics.active_turns == 2
        assert metrics.active_runs_by_agent_type.get("native") == 1
        assert metrics.active_runs_by_agent_type.get("non-native") == 1

        # Cleanup
        session_pool.sessions._runs.clear()
        await session_pool.close_session("sess-native")
        await session_pool.close_session("sess-non-native")
