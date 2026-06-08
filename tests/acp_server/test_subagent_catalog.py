"""TDD tests for SubagentCatalogProvider (T13).

Tests static catalog generation, debounced dynamic updates,
cycle detection, and SupportsRunStream filtering.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from acp.schema import SubagentInfo
from acp.schema.session_updates import AvailableSubagentsUpdate
from agentpool import Agent
from agentpool.common_types import SupportsRunStream
from agentpool.delegation import AgentPool
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.subagent_catalog import SubagentCatalogProvider


@pytest.fixture
def mock_connection():
    """Create a mock ACP connection."""
    return AsyncMock()


@pytest.fixture
def mock_agent_pool_with_multiple_agents():
    """Create a mock agent pool with streaming and non-streaming agents."""

    def callback_a(message: str) -> str:
        return f"Agent A: {message}"

    def callback_b(message: str) -> str:
        return f"Agent B: {message}"

    pool = AgentPool()
    agent_a = Agent.from_callback(
        name="agent_a",
        callback=callback_a,
        agent_pool=pool,
        system_prompt="You are agent A",
    )
    agent_b = Agent.from_callback(
        name="agent_b",
        callback=callback_b,
        agent_pool=pool,
        system_prompt="You are agent B",
    )
    pool.register("agent_a", agent_a)
    pool.register("agent_b", agent_b)
    return pool, agent_a, agent_b


@pytest.fixture
def default_test_agent(mock_agent_pool_with_multiple_agents):
    """Get the first test agent from the mock pool."""
    return mock_agent_pool_with_multiple_agents[1]


@pytest.fixture
def mock_acp_agent(mock_connection, default_test_agent):
    """Create a mock ACP agent for testing."""
    return AgentPoolACPAgent(client=mock_connection, default_agent=default_test_agent)


# =============================================================================
# Static catalog tests
# =============================================================================


@pytest.mark.unit
def test_catalog_reflects_pool_agents(mock_agent_pool_with_multiple_agents) -> None:
    """get_catalog() must reflect agents registered in the pool."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool)
    catalog = provider.get_catalog()

    ids = {info.subagent_id for info in catalog}
    assert "agent_a" in ids
    assert "agent_b" in ids


@pytest.mark.unit
def test_catalog_returns_subagent_info_instances(mock_agent_pool_with_multiple_agents) -> None:
    """Catalog entries must be SubagentInfo instances."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool)
    catalog = provider.get_catalog()

    for info in catalog:
        assert isinstance(info, SubagentInfo)
        assert info.subagent_id
        assert info.name


@pytest.mark.unit
def test_catalog_includes_system_prompt_as_description(
    mock_agent_pool_with_multiple_agents,
) -> None:
    """Catalog entries should include truncated system prompt as description."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool)
    catalog = provider.get_catalog()

    agent_a_info = next((s for s in catalog if s.subagent_id == "agent_a"), None)
    assert agent_a_info is not None
    assert agent_a_info.description is not None
    assert "You are agent A" in agent_a_info.description


@pytest.mark.unit
def test_catalog_includes_capabilities(mock_agent_pool_with_multiple_agents) -> None:
    """Catalog entries should include SubagentCapabilities."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool)
    catalog = provider.get_catalog()

    for info in catalog:
        assert info.capabilities is not None
        assert info.capabilities.streaming is True
        assert info.capabilities.tools is True


# =============================================================================
# SupportsRunStream filtering tests
# =============================================================================


class FakeNonStreamingAgent:
    """Fake agent without run_stream for filtering tests."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"Description for {name}"
        self.system_prompt = "You are a fake agent"


@pytest.mark.unit
def test_catalog_filters_non_streaming_agents(mock_agent_pool_with_multiple_agents) -> None:
    """Catalog must exclude agents that do not support run_stream."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool)

    # Temporarily inject a non-streaming agent into pool._items
    fake_agent = FakeNonStreamingAgent("fake_no_stream")
    pool._items["fake_no_stream"] = fake_agent
    try:
        catalog = provider.get_catalog()
    finally:
        del pool._items["fake_no_stream"]

    ids = {info.subagent_id for info in catalog}
    assert "agent_a" in ids
    assert "agent_b" in ids
    assert "fake_no_stream" not in ids


@pytest.mark.unit
def test_catalog_uses_isinstance_supports_run_stream(mock_agent_pool_with_multiple_agents) -> None:
    """Catalog filtering must use isinstance(node, SupportsRunStream)."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool)

    fake_agent = FakeNonStreamingAgent("fake_no_stream")
    pool._items["fake_no_stream"] = fake_agent
    try:
        catalog = provider.get_catalog()
    finally:
        del pool._items["fake_no_stream"]

    for info in catalog:
        agent = pool.all_agents[info.subagent_id]
        assert isinstance(agent, SupportsRunStream)


# =============================================================================
# Cycle detection tests
# =============================================================================


@pytest.mark.unit
def test_catalog_filters_ancestor_agents(mock_agent_pool_with_multiple_agents) -> None:
    """get_catalog() must exclude ancestor agent IDs to prevent cycles."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool)

    catalog = provider.get_catalog(ancestor_agent_ids={"agent_a"})
    ids = {info.subagent_id for info in catalog}
    assert "agent_a" not in ids
    assert "agent_b" in ids


@pytest.mark.unit
def test_catalog_with_empty_ancestor_set_returns_all(mock_agent_pool_with_multiple_agents) -> None:
    """Empty ancestor_agent_ids should not filter anything."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool)

    catalog = provider.get_catalog(ancestor_agent_ids=set())
    ids = {info.subagent_id for info in catalog}
    assert "agent_a" in ids
    assert "agent_b" in ids


@pytest.mark.unit
def test_catalog_with_none_ancestor_returns_all(mock_agent_pool_with_multiple_agents) -> None:
    """None ancestor_agent_ids should not filter anything."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool)

    catalog = provider.get_catalog(ancestor_agent_ids=None)
    ids = {info.subagent_id for info in catalog}
    assert "agent_a" in ids
    assert "agent_b" in ids


# =============================================================================
# Debounced update tests
# =============================================================================


@pytest.mark.unit
async def test_notify_update_debounces_at_500ms(mock_agent_pool_with_multiple_agents) -> None:
    """Multiple rapid notify_update calls should only emit once after debounce."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool, debounce_ms=100)

    callbacks: list[list[SubagentInfo]] = []

    def capture_callback(catalog: list[SubagentInfo]) -> None:
        callbacks.append(catalog)

    provider.register_update_callback(capture_callback)

    # Fire multiple rapid updates
    await provider.notify_update()
    await provider.notify_update()
    await provider.notify_update()

    # Should not have emitted yet (within debounce window)
    assert len(callbacks) == 0

    # Wait for debounce to complete
    await asyncio.sleep(0.15)

    # Should have exactly one emission
    assert len(callbacks) == 1
    assert len(callbacks[0]) == 2


@pytest.mark.unit
async def test_notify_update_cancels_pending_task(mock_agent_pool_with_multiple_agents) -> None:
    """A new notify_update should cancel the previous pending task."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool, debounce_ms=200)

    callbacks: list[list[SubagentInfo]] = []

    def capture_callback(catalog: list[SubagentInfo]) -> None:
        callbacks.append(catalog)

    provider.register_update_callback(capture_callback)

    await provider.notify_update()
    await asyncio.sleep(0.05)
    await provider.notify_update()  # Cancels first, starts second
    await asyncio.sleep(0.05)
    await provider.notify_update()  # Cancels second, starts third

    # Should not have emitted yet
    assert len(callbacks) == 0

    # Wait for final debounce
    await asyncio.sleep(0.25)

    # Exactly one emission from the final task
    assert len(callbacks) == 1


@pytest.mark.unit
async def test_notify_update_uses_asyncio_task(mock_agent_pool_with_multiple_agents) -> None:
    """notify_update must use asyncio.create_task for debounce cancellation."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool, debounce_ms=100)

    await provider.notify_update()
    assert provider._pending_update is not None
    assert isinstance(provider._pending_update, asyncio.Task)

    # Clean up
    provider._pending_update.cancel()
    with pytest.raises(asyncio.CancelledError):
        await provider._pending_update


@pytest.mark.unit
async def test_default_debounce_is_500ms(mock_agent_pool_with_multiple_agents) -> None:
    """Default debounce_ms should be 500."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool)
    assert provider.debounce_ms == 500


# =============================================================================
# Integration with AgentPoolACPAgent tests
# =============================================================================


@pytest.mark.unit
def test_acp_agent_has_catalog_provider(mock_acp_agent) -> None:
    """AgentPoolACPAgent must have a _catalog_provider attribute."""
    assert hasattr(mock_acp_agent, "_catalog_provider")
    assert isinstance(mock_acp_agent._catalog_provider, SubagentCatalogProvider)


@pytest.mark.unit
def test_acp_agent_exposes_get_catalog(mock_acp_agent) -> None:
    """AgentPoolACPAgent must expose get_subagent_catalog method."""
    assert hasattr(mock_acp_agent, "get_subagent_catalog")
    catalog = mock_acp_agent.get_subagent_catalog()
    assert len(catalog) == 2
    ids = {info.subagent_id for info in catalog}
    assert "agent_a" in ids
    assert "agent_b" in ids


@pytest.mark.unit
def test_acp_agent_get_catalog_delegates_to_provider(mock_acp_agent) -> None:
    """get_subagent_catalog must delegate to SubagentCatalogProvider."""
    with patch.object(mock_acp_agent._catalog_provider, "get_catalog", return_value=[]) as mock_get:
        mock_acp_agent.get_subagent_catalog()
        mock_get.assert_called_once()


@pytest.mark.unit
def test_acp_agent_get_catalog_with_ancestors(mock_acp_agent) -> None:
    """get_subagent_catalog must pass ancestor_agent_ids to provider."""
    with patch.object(
        mock_acp_agent._catalog_provider,
        "get_catalog",
        return_value=[],
    ) as mock_get:
        mock_acp_agent.get_subagent_catalog(ancestor_agent_ids={"agent_a"})
        mock_get.assert_called_once_with(ancestor_agent_ids={"agent_a"})


# =============================================================================
# Schema tests
# =============================================================================


@pytest.mark.unit
async def test_notification_emitted_after_debounce(mock_agent_pool_with_multiple_agents) -> None:
    """AvailableSubagentsUpdate notification must be emitted after debounce."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool, debounce_ms=100)

    mock_channel = AsyncMock()
    provider.register_notification_channel(mock_channel)

    await provider.notify_update()

    # Should not have emitted yet (within debounce window)
    mock_channel.send_update.assert_not_called()

    # Wait for debounce to complete
    await asyncio.sleep(0.15)

    # Should have exactly one emission
    mock_channel.send_update.assert_called_once()
    update = mock_channel.send_update.call_args[0][0]
    assert isinstance(update, AvailableSubagentsUpdate)


@pytest.mark.unit
async def test_notification_contains_updated_catalog(mock_agent_pool_with_multiple_agents) -> None:
    """Notification must contain the current catalog entries."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool, debounce_ms=100)

    mock_channel = AsyncMock()
    provider.register_notification_channel(mock_channel)

    await provider.notify_update()
    await asyncio.sleep(0.15)

    update = mock_channel.send_update.call_args[0][0]
    assert isinstance(update, AvailableSubagentsUpdate)
    ids = {info.subagent_id for info in update.available_subagents}
    assert "agent_a" in ids
    assert "agent_b" in ids


@pytest.mark.unit
async def test_no_notification_for_empty_catalog(mock_agent_pool_with_multiple_agents) -> None:
    """Empty catalog must not emit a notification."""
    pool, _, _ = mock_agent_pool_with_multiple_agents
    provider = SubagentCatalogProvider(pool=pool, debounce_ms=100)

    # Empty the pool so catalog is empty
    pool._items.clear()

    mock_channel = AsyncMock()
    provider.register_notification_channel(mock_channel)

    await provider.notify_update()
    await asyncio.sleep(0.15)

    mock_channel.send_update.assert_not_called()
