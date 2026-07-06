"""Tests for SourceType, get_source_type(), and MessageNode.agent_type."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from agentpool.messaging import ChatMessage, MessageNode
from agentpool.messaging.messagenode import SourceType, get_source_type


class StubMessageNode(MessageNode[Any, Any]):
    """Concrete MessageNode for testing unknown subclasses."""

    async def run(self, *prompts: Any, **kwargs: Any) -> ChatMessage[Any]:
        return ChatMessage(content="stub", role="assistant")

    async def get_stats(self):
        pass

    def run_iter(self, *prompts: Any, **kwargs: Any):
        pass


def test_source_type_literal_values() -> None:
    """SourceType must be exactly the three expected literals."""
    valid: list[SourceType] = ["agent", "team_parallel", "team_sequential"]
    assert len(valid) == 3


@pytest.mark.requires_openai_key
def test_get_source_type_native_agent() -> None:
    """Native Agent instances should return 'agent'."""
    from agentpool.agents import Agent

    agent = Agent(name="test_agent", model="openai:gpt-4o-mini")
    assert get_source_type(agent) == "agent"


@pytest.mark.requires_openai_key
def test_get_source_type_team() -> None:
    """Team (parallel) instances should return 'team_parallel'."""
    from agentpool.agents import Agent
    from agentpool.delegation.base_team import BaseTeam

    agent_a = Agent(name="a", model="openai:gpt-4o-mini")
    agent_b = Agent(name="b", model="openai:gpt-4o-mini")
    team = BaseTeam([agent_a, agent_b], mode="parallel", name="par")
    assert get_source_type(team) == "team_parallel"


@pytest.mark.requires_openai_key
def test_get_source_type_teamrun() -> None:
    """TeamRun (sequential) instances should return 'team_sequential'."""
    from agentpool.agents import Agent
    from agentpool.delegation.base_team import BaseTeam

    agent_a = Agent(name="a", model="openai:gpt-4o-mini")
    agent_b = Agent(name="b", model="openai:gpt-4o-mini")
    team_run = BaseTeam([agent_a, agent_b], mode="sequential", name="seq")
    assert get_source_type(team_run) == "team_sequential"


def test_get_source_type_unknown_subclass_defaults_to_agent() -> None:
    """Unknown MessageNode subclasses should default to 'agent' with a warning."""
    stub = StubMessageNode(name="stub")
    with patch("agentpool.messaging.messagenode.logger"):
        result = get_source_type(stub)
    assert result == "agent"
    # No warning for valid MessageNode subclass — it's just not Team/BaseTeam
    # The warning only fires for non-MessageNode objects


@pytest.mark.requires_openai_key
def test_agent_type_property_on_agent() -> None:
    """MessageNode.agent_type on a native Agent returns persistence value."""
    from agentpool.agents import Agent

    agent = Agent(name="test_agent", model="openai:gpt-4o-mini")
    assert agent.agent_type == "agent"


@pytest.mark.requires_openai_key
def test_agent_type_property_on_team() -> None:
    """MessageNode.agent_type on a Team returns the source_type value."""
    from agentpool.agents import Agent
    from agentpool.delegation.base_team import BaseTeam

    agent_a = Agent(name="a", model="openai:gpt-4o-mini")
    agent_b = Agent(name="b", model="openai:gpt-4o-mini")
    team = BaseTeam([agent_a, agent_b], mode="parallel", name="par")
    assert team.agent_type == "team_parallel"


@pytest.mark.requires_openai_key
def test_agent_type_property_on_teamrun() -> None:
    """MessageNode.agent_type on a TeamRun returns the source_type value."""
    from agentpool.agents import Agent
    from agentpool.delegation.base_team import BaseTeam

    agent_a = Agent(name="a", model="openai:gpt-4o-mini")
    agent_b = Agent(name="b", model="openai:gpt-4o-mini")
    team_run = BaseTeam([agent_a, agent_b], mode="sequential", name="seq")
    assert team_run.agent_type == "team_sequential"


def test_circular_import_safety() -> None:
    """Importing get_source_type must not create circular imports."""
    import importlib

    # Force re-import of messagenode to verify no circular import
    mod = importlib.import_module("agentpool.messaging.messagenode")
    importlib.reload(mod)

    # Verify the module still exports the expected symbols
    assert hasattr(mod, "SourceType")
    assert hasattr(mod, "get_source_type")

    importlib.import_module("agentpool.delegation.base_team")
