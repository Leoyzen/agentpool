"""Unit tests for AgentPool.get_context() and _factory property."""

from __future__ import annotations

import pytest

from agentpool.delegation.pool import AgentPool
from agentpool.host.context import HostContext
from agentpool.host.factory import AgentFactory


@pytest.fixture
def pool() -> AgentPool[None]:
    """Create a minimal AgentPool with empty manifest."""
    return AgentPool()  # type: ignore[type-arg]


@pytest.mark.unit
def test_get_context_returns_host_context(pool: AgentPool[None]) -> None:
    """get_context() returns a HostContext instance with correct field mappings."""
    ctx = pool.get_context()

    assert isinstance(ctx, HostContext)
    assert ctx.manifest is pool.manifest
    assert ctx.storage is pool.storage
    assert ctx.vfs_registry is pool.vfs_registry
    assert ctx.connection_registry is pool.connection_registry
    assert ctx.mcp is pool.mcp
    assert ctx.skills_registry is pool.skills
    assert ctx.skills_instruction_provider is pool.skills_instruction_provider
    assert ctx.skills_tools_provider is pool.skills_tools_provider
    assert ctx.prompt_manager is pool.prompt_manager
    assert ctx.process_manager is pool.process_manager
    assert ctx.file_ops is pool.file_ops
    assert ctx.todos is pool.todos
    assert ctx.config_file_path is pool._config_file_path


@pytest.mark.unit
def test_get_context_is_cached(pool: AgentPool[None]) -> None:
    """get_context() returns the same HostContext on second call (identity)."""
    ctx1 = pool.get_context()
    ctx2 = pool.get_context()

    assert ctx1 is ctx2


@pytest.mark.unit
def test_get_context_pool_back_reference(pool: AgentPool[None]) -> None:
    """HostContext.pool back-reference points to the originating pool."""
    ctx = pool.get_context()

    assert ctx.pool is pool


@pytest.mark.unit
def test_factory_returns_agent_factory(pool: AgentPool[None]) -> None:
    """_factory property returns an AgentFactory instance."""
    factory = pool._factory

    assert isinstance(factory, AgentFactory)
    assert factory.pool is pool


@pytest.mark.unit
def test_factory_is_cached(pool: AgentPool[None]) -> None:
    """_factory property returns the same AgentFactory on second access (identity)."""
    factory1 = pool._factory
    factory2 = pool._factory

    assert factory1 is factory2


@pytest.mark.unit
def test_get_context_session_pool_is_none_before_enter(pool: AgentPool[None]) -> None:
    """HostContext.session_pool is None before __aenter__ is called."""
    ctx = pool.get_context()

    assert ctx.session_pool is None
    assert ctx.session_pool is pool._session_pool
