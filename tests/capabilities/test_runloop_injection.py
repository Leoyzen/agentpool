"""Tests for AgentContext runtime injection into RunLoop (task group 15).

Verifies that RunHandle._inject_agent_context() constructs an AgentContext
per turn and sets it as run_ctx.deps, making it available to capabilities
like SubagentCapability via pydantic-ai's RunContext.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.capabilities.agent_context import AgentContext
from agentpool.capabilities.delegation import DelegationService
from agentpool.capabilities.runloop_delegation import RunLoopDelegationService
from agentpool.host.context import HostContext, RunScope
from agentpool.host.registry import AgentRegistry
from agentpool.orchestrator.run import RunHandle


def _make_host_context() -> HostContext:
    """Build a HostContext with minimal stubs for testing."""
    return HostContext(
        manifest=MagicMock(),
        storage=MagicMock(),
        vfs_registry=MagicMock(),
        connection_registry=MagicMock(),
        mcp=MagicMock(),
        skills_registry=MagicMock(),
        skills_tools_provider=None,
        prompt_manager=MagicMock(),
        process_manager=MagicMock(),
        file_ops=MagicMock(),
        todos=MagicMock(),
        session_pool=None,
        config_file_path=None,
    )


def _make_run_handle(
    host_context: HostContext | None = None,
    agent_registry: AgentRegistry | None = None,
) -> RunHandle:
    """Build a RunHandle with minimal fields for testing injection."""
    run_ctx = AgentRunContext(session_id="test-session")
    return RunHandle(
        run_id="test-run",
        session_id="test-session",
        agent_type="native",
        run_ctx=run_ctx,
        _host_context=host_context,
        _agent_registry=agent_registry,
    )


def test_inject_agent_context_sets_deps() -> None:
    """Test that _inject_agent_context sets run_ctx.deps to an AgentContext."""
    host = _make_host_context()
    registry = AgentRegistry()
    handle = _make_run_handle(host_context=host, agent_registry=registry)

    assert handle.run_ctx.deps is None
    handle._inject_agent_context()
    assert isinstance(handle.run_ctx.deps, AgentContext)


def test_inject_agent_context_noop_without_host() -> None:
    """Test that injection is a no-op without host_context."""
    handle = _make_run_handle(host_context=None, agent_registry=None)
    handle._inject_agent_context()
    assert handle.run_ctx.deps is None


def test_inject_agent_context_noop_without_registry() -> None:
    """Test that injection is a no-op without agent_registry."""
    host = _make_host_context()
    handle = _make_run_handle(host_context=host, agent_registry=None)
    handle._inject_agent_context()
    assert handle.run_ctx.deps is None


def test_injected_context_has_correct_scope() -> None:
    """Test that the injected AgentContext has the correct session_id scope."""
    host = _make_host_context()
    registry = AgentRegistry()
    handle = _make_run_handle(host_context=host, agent_registry=registry)
    handle._inject_agent_context()

    ctx: AgentContext = handle.run_ctx.deps
    assert isinstance(ctx.scope, RunScope)
    assert ctx.scope.session_id == "test-session"


def test_injected_context_has_delegation_service() -> None:
    """Test that the injected AgentContext has a RunLoopDelegationService."""
    host = _make_host_context()
    registry = AgentRegistry()
    handle = _make_run_handle(host_context=host, agent_registry=registry)
    handle._inject_agent_context()

    ctx: AgentContext = handle.run_ctx.deps
    assert isinstance(ctx.delegation, RunLoopDelegationService)
    assert isinstance(ctx.delegation, DelegationService)


def test_injected_context_has_host() -> None:
    """Test that the injected AgentContext.host is the same HostContext."""
    host = _make_host_context()
    registry = AgentRegistry()
    handle = _make_run_handle(host_context=host, agent_registry=registry)
    handle._inject_agent_context()

    ctx: AgentContext = handle.run_ctx.deps
    assert ctx.host is host


def test_injected_context_has_registry() -> None:
    """Test that the injected AgentContext.agent_registry is the same registry."""
    host = _make_host_context()
    registry = AgentRegistry()
    handle = _make_run_handle(host_context=host, agent_registry=registry)
    handle._inject_agent_context()

    ctx: AgentContext = handle.run_ctx.deps
    assert ctx.agent_registry is registry


def test_inject_creates_fresh_context_per_call() -> None:
    """Test that each injection call creates a new AgentContext instance."""
    host = _make_host_context()
    registry = AgentRegistry()
    handle = _make_run_handle(host_context=host, agent_registry=registry)

    handle._inject_agent_context()
    ctx1: AgentContext = handle.run_ctx.deps

    handle._inject_agent_context()
    ctx2: AgentContext = handle.run_ctx.deps

    assert ctx1 is not ctx2


def test_delegation_service_lists_agents() -> None:
    """Test that RunLoopDelegationService lists agents from the registry."""
    host = _make_host_context()
    registry = AgentRegistry()
    registry.add("zebra", MagicMock())  # type: ignore[arg-type]
    registry.add("alpha", MagicMock())  # type: ignore[arg-type]

    service = RunLoopDelegationService(
        registry=registry,
        host=host,
        session_id="test-session",
    )

    agents = service.get_available_agents()
    assert agents == ["alpha", "zebra"]


def test_delegation_service_raises_for_unknown_agent() -> None:
    """Test that RunLoopDelegationService raises for unknown agents."""
    from agentpool.capabilities.delegation import AgentNotFoundError

    host = _make_host_context()
    registry = AgentRegistry()
    service = RunLoopDelegationService(
        registry=registry,
        host=host,
        session_id="test-session",
    )

    import asyncio

    async def _run() -> None:
        async for _ in service.spawn_subagent("nonexistent", "prompt"):
            pass

    with pytest.raises(AgentNotFoundError):
        asyncio.run(_run())
