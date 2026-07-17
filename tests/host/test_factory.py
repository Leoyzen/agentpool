"""Unit tests for AgentFactory."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool import AgentPool
from agentpool.host.factory import AgentFactory


pytestmark = pytest.mark.unit


_DEFAULT_TOOLS_PROVIDER = MagicMock()
_DEFAULT_MCP = MagicMock()


def _make_host_context(
    *,
    pool: Any | None = None,
    config_file_path: str | None = None,
    skills_tools_provider: Any | None = _DEFAULT_TOOLS_PROVIDER,
    mcp: Any | None = _DEFAULT_MCP,
) -> Any:
    """Build a mock HostContext with common defaults."""
    ctx = MagicMock()
    ctx.config_file_path = config_file_path
    ctx.skills_tools_provider = skills_tools_provider
    ctx.mcp = mcp if mcp is not None else MagicMock()
    return ctx


def _make_native_cfg(
    *,
    name: str | None = "test_agent",
    agent: Any | None = None,
) -> Any:
    """Build a mock NativeAgentConfig instance.

    The returned mock passes ``isinstance(cfg, NativeAgentConfig)``
    because we patch the isinstance check in the factory.
    """
    cfg = MagicMock()
    cfg.name = name
    cfg.get_agent = MagicMock(return_value=agent or MagicMock())
    cfg.get_mcp_servers = MagicMock(return_value=[])
    return cfg


def _make_agent_mock() -> Any:
    """Build a mock agent with all attributes the factory touches."""
    agent = MagicMock()
    agent.__aenter__ = AsyncMock(return_value=agent)
    agent.__aexit__ = AsyncMock(return_value=None)
    agent.load_session = AsyncMock(return_value=None)
    agent.env = None
    agent._internal_fs = MagicMock()
    agent._build_pool_configs = MagicMock(return_value=())
    agent._build_agent_configs = MagicMock(return_value=())
    agent.mcp = MagicMock()
    agent.mcp.get_or_create_session = MagicMock(return_value=MagicMock())
    agent.mcp.update_session_snapshot = MagicMock(return_value=None)
    agent.mcp._session_contexts = {}
    agent.mcp._acp_mcp_manager = None
    agent.tools = MagicMock()
    agent.tools.add_provider = MagicMock()
    return agent


def _make_session(*, parent_session_id: str | None = None) -> Any:
    """Build a mock SessionState."""
    session = MagicMock()
    session.parent_session_id = parent_session_id
    return session


# ---------------------------------------------------------------------------
# compile()
# ---------------------------------------------------------------------------


def test_compile_returns_empty_registry(minimal_pool: AgentPool) -> None:
    """Given a manifest and host_context, compile() returns empty AgentRegistry."""
    factory = AgentFactory(pool=minimal_pool)
    registry = factory.compile(
        manifest=MagicMock(),
        host_context=_make_host_context(),
    )
    assert len(registry) == 0
    assert registry.list_names() == []


# ---------------------------------------------------------------------------
# create_session_agent — native main path
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="L2 migration: requires mock internals — remains L1 unit test")
@pytest.mark.asyncio
async def test_create_session_agent_native_main_calls_get_agent_with_pool(minimal_pool: AgentPool) -> None:
    """When cfg is NativeAgentConfig and no parent, get_agent is called with pool."""
    agent = _make_agent_mock()
    cfg = _make_native_cfg(agent=agent)
    pool = MagicMock()
    host_context = _make_host_context(pool=pool)

    factory = AgentFactory(pool=pool)

    with patch("agentpool.models.agents.NativeAgentConfig", (type(cfg),)):
        result = await factory.create_session_agent(
            agent_name="test_agent",
            session_id="sess-1",
            host_context=host_context,
            session=_make_session(),
            cfg=cfg,
        )

    assert result is agent
    cfg.get_agent.assert_called_once_with(
        input_provider=None,
        pool=pool,
    )


@pytest.mark.asyncio
async def test_create_session_agent_native_main_calls_aenter(minimal_pool: AgentPool) -> None:
    """When creating a native main agent, __aenter__ is called."""
    agent = _make_agent_mock()
    cfg = _make_native_cfg(agent=agent)
    host_context = _make_host_context()

    factory = AgentFactory(pool=minimal_pool)

    with patch("agentpool.models.agents.NativeAgentConfig", (type(cfg),)):
        await factory.create_session_agent(
            agent_name="test_agent",
            session_id="sess-1",
            host_context=host_context,
            session=_make_session(),
            cfg=cfg,
        )

    agent.__aenter__.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_session_agent_native_main_no_pool_providers(minimal_pool: AgentPool) -> None:
    """When creating a native main agent, no pool providers are added.

    (include_aggregating=False)
    """
    agent = _make_agent_mock()
    cfg = _make_native_cfg(agent=agent)
    skills_tools = MagicMock()
    host_context = _make_host_context(
        skills_tools_provider=skills_tools,
    )

    factory = AgentFactory(pool=minimal_pool)

    with patch("agentpool.models.agents.NativeAgentConfig", (type(cfg),)):
        await factory.create_session_agent(
            agent_name="test_agent",
            session_id="sess-1",
            host_context=host_context,
            session=_make_session(),
            cfg=cfg,
        )

    # _inject_pool_providers with include_aggregating=False adds no providers.
    agent.tools.add_provider.assert_not_called()


# ---------------------------------------------------------------------------
# create_session_agent — non-native path
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="L2 migration: requires mock internals — remains L1 unit test")
@pytest.mark.asyncio
async def test_create_session_agent_non_native_builds_snapshot_manually(minimal_pool: AgentPool) -> None:
    """When cfg is NOT NativeAgentConfig, MCP snapshot is built from pool."""
    agent = _make_agent_mock()
    # Non-native cfg: not an instance of NativeAgentConfig
    cfg = MagicMock()
    cfg.name = "acp_agent"
    cfg.get_agent = MagicMock(return_value=agent)
    cfg.get_mcp_servers = MagicMock(return_value=[])

    # Mock MCP manager with one enabled server
    mock_server = MagicMock()
    mock_server.enabled = True
    mcp = MagicMock()
    mcp.servers = [mock_server]
    mcp.get_aggregating_provider = MagicMock(return_value=MagicMock())

    pool = MagicMock()
    host_context = _make_host_context(mcp=mcp, pool=pool)

    factory = AgentFactory(pool=pool)

    # empty tuple → isinstance always False
    with patch("agentpool.models.agents.NativeAgentConfig", ()):
        result = await factory.create_session_agent(
            agent_name="acp_agent",
            session_id="sess-1",
            host_context=host_context,
            session=_make_session(),
            cfg=cfg,
        )

    assert result is agent
    cfg.get_agent.assert_called_once_with(
        input_provider=None,
        pool=pool,
    )
    agent.__aenter__.assert_awaited_once()
    agent.mcp.get_or_create_session.assert_called_once_with("sess-1")
    agent.mcp.update_session_snapshot.assert_called_once()


# ---------------------------------------------------------------------------
# create_session_agent — name fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_agent_fixes_missing_name(minimal_pool: AgentPool) -> None:
    """When cfg.name is None, model_copy is called to set the name."""
    agent = _make_agent_mock()
    cfg = _make_native_cfg(name=None, agent=agent)
    # model_copy returns a new mock that also passes isinstance
    new_cfg = MagicMock()
    new_cfg.name = "fixed_name"
    new_cfg.get_agent = MagicMock(return_value=agent)
    new_cfg.get_mcp_servers = MagicMock(return_value=[])
    cfg.model_copy = MagicMock(return_value=new_cfg)

    host_context = _make_host_context()
    factory = AgentFactory(pool=minimal_pool)

    with patch("agentpool.models.agents.NativeAgentConfig", (type(new_cfg),)):
        await factory.create_session_agent(
            agent_name="fixed_name",
            session_id="sess-1",
            host_context=host_context,
            session=_make_session(),
            cfg=cfg,
        )

    cfg.model_copy.assert_called_once_with(update={"name": "fixed_name"})
    new_cfg.get_agent.assert_called_once()


# ---------------------------------------------------------------------------
# create_session_agent — load_session called on native main
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_agent_native_main_loads_session(minimal_pool: AgentPool) -> None:
    """When creating a native main agent, load_session is called."""
    agent = _make_agent_mock()
    cfg = _make_native_cfg(agent=agent)
    host_context = _make_host_context()

    factory = AgentFactory(pool=minimal_pool)

    with patch("agentpool.models.agents.NativeAgentConfig", (type(cfg),)):
        await factory.create_session_agent(
            agent_name="test_agent",
            session_id="sess-42",
            host_context=host_context,
            session=_make_session(),
            cfg=cfg,
        )

    agent.load_session.assert_awaited_once_with("sess-42")
