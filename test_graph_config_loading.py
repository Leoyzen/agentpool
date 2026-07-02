"""Test script for graph config loading in AgentPool.

Validates that:
- Old YAML configs (teams:/connections:) load and translate correctly
- New graph: YAML configs load and build correctly
- Config validation errors include file location
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentpool_config.graph_translation import GraphConfig
import pytest

from agentpool import AgentPool


if TYPE_CHECKING:
    from pathlib import Path


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def old_config_path(tmp_path: Path) -> Path:
    """Create an old-style config with connections."""
    config = tmp_path / "old_config.yml"
    config.write_text("""
agents:
  agent_a:
    type: native
    model: test
    system_prompt: "You are agent A"
    connections:
      - type: node
        name: agent_b

  agent_b:
    type: native
    model: test
    system_prompt: "You are agent B"
""")
    return config


@pytest.fixture
def old_teams_config_path(tmp_path: Path) -> Path:
    """Create an old-style config with teams."""
    config = tmp_path / "old_teams_config.yml"
    config.write_text("""
agents:
  analyzer:
    type: native
    model: test
    system_prompt: "You are analyzer"

  reviewer:
    type: native
    model: test
    system_prompt: "You are reviewer"

teams:
  review_pipeline:
    mode: sequential
    members: [analyzer, reviewer]
""")
    return config


@pytest.fixture
def new_graph_config_path(tmp_path: Path) -> Path:
    """Create a new-style config with graph: section."""
    config = tmp_path / "new_graph_config.yml"
    config.write_text("""
agents:
  step_a:
    type: native
    model: test
    system_prompt: "You are step A"

  step_b:
    type: native
    model: test
    system_prompt: "You are step B"

graph:
  name: test_workflow
  steps:
    - id: step_a
      agent: step_a
    - id: step_b
      agent: step_b
  edges:
    - from: start
      to: step_a
    - from: step_a
      to: step_b
    - from: step_b
      to: end
""")
    return config


@pytest.fixture
def invalid_graph_config_path(tmp_path: Path) -> Path:
    """Create a config with an invalid graph reference."""
    config = tmp_path / "invalid_graph_config.yml"
    config.write_text("""
agents:
  step_a:
    type: native
    model: test

graph:
  steps:
    - id: step_a
      agent: step_a
    - id: bad_step
      agent: nonexistent_agent
  edges:
    - from: start
      to: step_a
    - from: step_a
      to: bad_step
""")
    return config


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.asyncio
async def test_old_connections_config_loads(old_config_path: Path) -> None:
    """Old configs with connections: should load without error."""
    pool = AgentPool(old_config_path)
    # Graph config should be translated from connections
    assert pool._graph_config is not None
    assert isinstance(pool._graph_config, GraphConfig)
    assert len(pool._graph_config.steps) == 2  # noqa: PLR2004
    assert len(pool._graph_config.edges) == 1

    async with pool:
        # Graph should be built from config
        assert pool._graph is not None
        assert pool.graph is not None


@pytest.mark.asyncio
async def test_old_teams_config_loads(old_teams_config_path: Path) -> None:
    """Old configs with teams: should load and translate correctly."""
    pool = AgentPool(old_teams_config_path)
    # Graph config should be translated from teams
    assert pool._graph_config is not None
    assert isinstance(pool._graph_config, GraphConfig)
    assert len(pool._graph_config.steps) == 2  # noqa: PLR2004
    # Sequential team: start -> analyzer -> reviewer -> end = 3 edges
    assert len(pool._graph_config.edges) == 3  # noqa: PLR2004

    async with pool:
        assert pool._graph is not None
        assert pool.graph is not None


@pytest.mark.asyncio
async def test_new_graph_config_loads(new_graph_config_path: Path) -> None:
    """New configs with graph: section should load natively."""
    pool = AgentPool(new_graph_config_path)
    assert pool._graph_config is not None
    assert isinstance(pool._graph_config, GraphConfig)
    assert pool._graph_config.name == "test_workflow"
    assert len(pool._graph_config.steps) == 2  # noqa: PLR2004
    assert len(pool._graph_config.edges) == 3  # noqa: PLR2004

    async with pool:
        assert pool._graph is not None
        assert pool.graph is not None


@pytest.mark.asyncio
async def test_invalid_graph_config_error(invalid_graph_config_path: Path) -> None:
    """Invalid graph configs should raise with config location in message."""
    pool = AgentPool(invalid_graph_config_path)
    # Config loading succeeds (translation works)
    assert pool._graph_config is not None

    with pytest.raises(RuntimeError) as exc_info:
        async with pool:
            pass

    # The top-level exception is wrapped by the outer __aenter__ try block.
    # Walk the cause chain to find the specific graph build error.
    exc = exc_info.value
    full_msg = ""
    while exc is not None:
        full_msg += str(exc) + "\n"
        exc = exc.__cause__

    # Error should mention the config file path somewhere in the chain
    assert str(invalid_graph_config_path) in full_msg
    # Error should mention the unknown agent
    assert "unknown agent" in full_msg.lower() or "bad_step" in full_msg.lower()


@pytest.mark.asyncio
async def test_empty_config_loads(tmp_path: Path) -> None:
    """Configs with no teams/connections/graph should have no graph config."""
    config = tmp_path / "empty_config.yml"
    config.write_text("""
agents:
  solo:
    type: native
    model: test
""")
    pool = AgentPool(config)
    assert pool._graph_config is None

    async with pool:
        # No graph config means no built graph
        assert pool.graph is None


@pytest.mark.asyncio
async def test_programmatic_manifest_with_graph() -> None:
    """Programmatic manifests with graph in model_extra should work."""
    from agentpool.models.agents import NativeAgentConfig
    from agentpool.models.manifest import AgentsManifest

    manifest = AgentsManifest(
        agents={
            "a": NativeAgentConfig(name="a", model="test"),
            "b": NativeAgentConfig(name="b", model="test"),
        },
        # extra="allow" stores unknown fields in model_extra
        graph={
            "name": "prog_graph",
            "steps": [
                {"id": "a", "agent": "a"},
                {"id": "b", "agent": "b"},
            ],
            "edges": [
                {"from": "start", "to": "a"},
                {"from": "a", "to": "b"},
                {"from": "b", "to": "end"},
            ],
        },
    )

    pool = AgentPool(manifest)
    assert pool._graph_config is not None
    assert pool._graph_config.name == "prog_graph"

    async with pool:
        assert pool._graph is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
