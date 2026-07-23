"""Tests for load_skill/list_skills tool propagation.

Verifies that:
- 5.2: ``_inject_pool_providers()`` with a non-None ``skills_tools_provider``
  injects it into ``agent._external_capabilities``.
- 5.3: ``_inject_pool_providers()`` with ``skills_tools_provider=None`` does
  not inject and does not error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from agentpool.host.factory import _inject_pool_providers


if TYPE_CHECKING:
    from agentpool import AgentPool


pytestmark = pytest.mark.unit


class FakeAgent:
    """Minimal agent stub for testing _inject_pool_providers."""

    def __init__(self) -> None:
        self._external_capabilities: list[Any] = []


class FakeHostContext:
    """Minimal host context stub for testing."""

    def __init__(
        self,
        skills_tools_provider: Any | None = None,
        mcp_aggregating_provider: Any | None = None,
    ) -> None:
        self.skills_tools_provider = skills_tools_provider
        self._mcp_aggregating = mcp_aggregating_provider

        class FakeMcp:
            def get_aggregating_provider(self) -> Any:
                return mcp_aggregating_provider

        self.mcp = FakeMcp()


def test_inject_pool_providers_with_skills_tools(minimal_pool: AgentPool) -> None:
    """Provider is injected when skills_tools_provider is set.

    Given a host context with skills_tools_provider set, When
    _inject_pool_providers is called, Then the provider is appended to
    agent._external_capabilities.
    """
    provider = MagicMock(name="skills_tools_provider")
    agent = FakeAgent()
    host_context = FakeHostContext(skills_tools_provider=provider)
    pool = minimal_pool

    _inject_pool_providers(agent, host_context, pool, include_aggregating=False)

    assert provider in agent._external_capabilities


def test_inject_pool_providers_without_skills_tools(minimal_pool: AgentPool) -> None:
    """No injection when skills_tools_provider is None.

    Given a host context with skills_tools_provider=None, When
    _inject_pool_providers is called, Then no skills provider is injected
    and no error occurs.
    """
    agent = FakeAgent()
    host_context = FakeHostContext(skills_tools_provider=None)
    pool = minimal_pool

    _inject_pool_providers(agent, host_context, pool, include_aggregating=False)

    # No skills provider injected.
    assert len(agent._external_capabilities) == 0


def test_inject_pool_providers_pool_none_returns_early() -> None:
    """No injection when pool is None.

    Given pool=None, When _inject_pool_providers is called, Then nothing is
    injected and no error occurs.
    """
    provider = MagicMock(name="skills_tools_provider")
    agent = FakeAgent()
    host_context = FakeHostContext(skills_tools_provider=provider)

    _inject_pool_providers(agent, host_context, None, include_aggregating=False)

    assert len(agent._external_capabilities) == 0


def test_inject_pool_providers_includes_both_skills_and_mcp(minimal_pool: AgentPool) -> None:
    """Both skills and MCP providers injected when configured.

    Given both skills_tools_provider and include_aggregating=True, When
    _inject_pool_providers is called, Then both providers are appended to
    _external_capabilities.
    """
    skills_provider = MagicMock(name="skills_tools_provider")
    mcp_provider = MagicMock(name="mcp_aggregating_provider")
    agent = FakeAgent()
    host_context = FakeHostContext(
        skills_tools_provider=skills_provider,
        mcp_aggregating_provider=mcp_provider,
    )
    pool = minimal_pool

    _inject_pool_providers(agent, host_context, pool, include_aggregating=True)

    assert skills_provider in agent._external_capabilities
    assert mcp_provider in agent._external_capabilities
    # Skills provider should be injected before MCP provider.
    assert agent._external_capabilities.index(skills_provider) < (
        agent._external_capabilities.index(mcp_provider)
    )
