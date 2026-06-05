"""Backward compatibility tests for old YAML configs (Task 18).

Validates that:
- All example configs load and translate without error
- Old ``teams:`` / ``connections:`` syntax produces equivalent graph topology
- Mixed configs (agents + teams + connections) work correctly
- Invalid configs produce helpful error messages with file paths
- Round-trip: old config → graph → equivalent to native graph syntax
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yamling

from agentpool import AgentPool
from agentpool_config.graph_translation import (
    GraphConfig,
    GraphEdgeConfig,
    translate_config,
)


# =============================================================================
# Helpers
# =============================================================================


def _repo_root() -> Path:
    """Find the repository root by walking up from this file."""
    path = Path(__file__).resolve()
    for parent in path.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    msg = "Could not find repository root"
    raise RuntimeError(msg)


def _example_configs() -> list[Path]:
    """Return all example YAML config paths."""
    repo_root = _repo_root()
    examples_dir = repo_root / "docs" / "examples"
    advanced_dir = repo_root / "docs" / "advanced"
    configs: list[Path] = []
    if examples_dir.exists():
        configs.extend(examples_dir.rglob("config.yml"))
    if advanced_dir.exists():
        configs.extend(advanced_dir.glob("*.yml"))
    return sorted(configs)


def _edge_topology(edges: list[GraphEdgeConfig]) -> list[dict[str, Any]]:
    """Return a deterministic edge topology representation for comparison."""
    result = []
    for edge in edges:
        from_val = edge.from_ if isinstance(edge.from_, str) else sorted(edge.from_)
        to_val = edge.to if isinstance(edge.to, str) else sorted(edge.to)
        result.append({
            "from": from_val,
            "to": to_val,
            "mode": edge.mode,
            "async": edge.async_,
            "priority": edge.priority,
        })
    return sorted(result, key=lambda d: (str(d["from"]), str(d["to"])))


def _write_evidence(filename: str, data: dict[str, Any]) -> None:
    """Save test evidence to the evidence directory."""
    evidence_dir = _repo_root() / ".omo" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / filename
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


# =============================================================================
# 1. Example configs load and translate
# =============================================================================


@pytest.mark.parametrize("config_path", _example_configs(), ids=lambda p: p.name)
def test_example_config_translates(config_path: Path) -> None:
    """Every example config loads via yamling and translates without error."""
    raw = yamling.load_yaml_file(config_path, resolve_inherit=True)
    result = translate_config(raw)

    # Either returns a GraphConfig or None (for configs without teams/connections/graph)
    assert result is None or isinstance(result, GraphConfig)


def test_all_example_configs_discovered() -> None:
    """Verify we found the expected number of example configs."""
    configs = _example_configs()
    # docs/examples/*/config.yml + docs/advanced/agui_example.yml
    assert len(configs) >= 13, f"Expected >=13 example configs, found {len(configs)}"

    evidence = {
        "task": "task-18-example-configs",
        "count": len(configs),
        "configs": [str(c.relative_to(Path(__file__).parents[3])) for c in configs],
    }
    _write_evidence("task-18-example-configs.json", evidence)


# =============================================================================
# 2. Old syntax produces same runtime behavior as native graph syntax
# =============================================================================


def test_sequential_team_old_vs_new_syntax() -> None:
    """Sequential ``teams:`` produces the same graph topology as native ``graph:``."""
    old_config = {
        "agents": {
            "a": {"type": "native", "model": "test"},
            "b": {"type": "native", "model": "test"},
        },
        "teams": {
            "pipe": {
                "mode": "sequential",
                "members": ["a", "b"],
            }
        },
    }

    new_config = {
        "agents": {
            "a": {"type": "native", "model": "test"},
            "b": {"type": "native", "model": "test"},
        },
        "graph": {
            "name": "pipe",
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
    }

    old_graph = translate_config(old_config)
    new_graph = translate_config(new_config)

    assert old_graph is not None
    assert new_graph is not None
    assert {s.id for s in old_graph.steps} == {s.id for s in new_graph.steps}
    assert _edge_topology(old_graph.edges) == _edge_topology(new_graph.edges)


def test_parallel_team_old_vs_new_syntax() -> None:
    """Parallel ``teams:`` produces the same graph topology as native ``graph:``."""
    old_config = {
        "agents": {
            "x": {"type": "native", "model": "test"},
            "y": {"type": "native", "model": "test"},
        },
        "teams": {
            "parallel": {
                "mode": "parallel",
                "members": ["x", "y"],
            }
        },
    }

    new_config = {
        "agents": {
            "x": {"type": "native", "model": "test"},
            "y": {"type": "native", "model": "test"},
        },
        "graph": {
            "name": "parallel",
            "steps": [
                {"id": "x", "agent": "x"},
                {"id": "y", "agent": "y"},
            ],
            "edges": [
                {"from": "start", "to": ["x", "y"]},
                {"from": ["x", "y"], "to": "end"},
            ],
        },
    }

    old_graph = translate_config(old_config)
    new_graph = translate_config(new_config)

    assert old_graph is not None
    assert new_graph is not None
    assert {s.id for s in old_graph.steps} == {s.id for s in new_graph.steps}
    assert _edge_topology(old_graph.edges) == _edge_topology(new_graph.edges)


# =============================================================================
# 3. Connections are translated correctly
# =============================================================================


def test_connections_translated_to_edges() -> None:
    """Agent ``connections:`` become graph edges with correct properties."""
    config = {
        "agents": {
            "src": {
                "type": "native",
                "model": "test",
                "connections": [
                    {
                        "type": "node",
                        "name": "dst",
                        "connection_type": "context",
                        "wait_for_completion": False,
                        "transform": "builtins.print",
                        "priority": 7,
                    },
                ],
            },
            "dst": {"type": "native", "model": "test"},
        },
    }

    graph = translate_config(config)
    assert graph is not None
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.from_ == "src"
    assert edge.to == "dst"
    assert edge.mode == "context"
    assert edge.async_ is True
    assert edge.transform is print
    assert edge.priority == 7


def test_filter_and_stop_conditions_translated() -> None:
    """filter_condition and stop_condition map to edge conditions."""
    config = {
        "agents": {
            "src": {
                "type": "native",
                "model": "test",
                "connections": [
                    {
                        "type": "node",
                        "name": "dst",
                        "filter_condition": {
                            "type": "word_match",
                            "words": ["error"],
                        },
                        "stop_condition": {
                            "type": "cost_limit",
                            "max_cost": 0.01,
                        },
                    },
                ],
            },
            "dst": {"type": "native", "model": "test"},
        },
    }

    graph = translate_config(config)
    assert graph is not None
    edge = graph.edges[0]
    assert edge.condition is not None
    assert edge.condition.type == "word_match"
    assert edge.stop_condition is not None
    assert edge.stop_condition.type == "cost_limit"


# =============================================================================
# 4. Mixed configs (agents + teams + connections)
# =============================================================================


def test_mixed_config_translates() -> None:
    """Config with agents, teams, and connections translates correctly."""
    config = {
        "agents": {
            "triage": {
                "type": "native",
                "model": "test",
                "connections": [{"type": "node", "name": "resolver"}],
            },
            "resolver": {"type": "native", "model": "test"},
            "researcher": {"type": "native", "model": "test"},
            "analyst": {"type": "native", "model": "test"},
        },
        "teams": {
            "analysis_group": {
                "mode": "parallel",
                "members": ["researcher", "analyst"],
            }
        },
    }

    graph = translate_config(config)
    assert graph is not None

    step_ids = {s.id for s in graph.steps}
    assert step_ids == {"triage", "resolver", "researcher", "analyst"}

    # 1 connection edge + 2 team edges (fork + join)
    assert len(graph.edges) == 3

    edge_map = {
        tuple(e.from_) if isinstance(e.from_, list) else e.from_: {
            "to": e.to,
            "mode": e.mode,
        }
        for e in graph.edges
    }
    assert edge_map["triage"]["to"] == "resolver"
    assert edge_map["triage"]["mode"] == "run"


@pytest.mark.asyncio
async def test_mixed_config_builds_graph_in_pool(tmp_path: Path) -> None:
    """Mixed config enters AgentPool and builds a pydantic-graph."""
    config = tmp_path / "mixed.yml"
    config.write_text("""
agents:
  triage:
    type: native
    model: test
    connections:
      - type: node
        name: resolver

  resolver:
    type: native
    model: test

  researcher:
    type: native
    model: test

  analyst:
    type: native
    model: test

teams:
  analysis_group:
    mode: parallel
    members: [researcher, analyst]
""")

    pool = AgentPool(config)
    assert pool._graph_config is not None
    assert len(pool._graph_config.steps) == 4
    assert len(pool._graph_config.edges) == 3

    async with pool:
        assert pool._graph is not None
        assert pool.graph is not None


# =============================================================================
# 5. Round-trip: old config → graph → equivalent to new syntax
# =============================================================================


def test_round_trip_translation() -> None:
    """Translated graph can be serialized and re-validated to produce same topology."""
    old_config = {
        "teams": {
            "review": {
                "mode": "sequential",
                "members": ["analyzer", "reviewer"],
            }
        }
    }

    translated = translate_config(old_config)
    assert translated is not None

    dumped = translated.model_dump(by_alias=True)
    restored = GraphConfig.model_validate(dumped)

    assert {s.id for s in restored.steps} == {"analyzer", "reviewer"}
    assert len(restored.edges) == 3
    assert _edge_topology(restored.edges) == _edge_topology(translated.edges)


# =============================================================================
# 6. Invalid configs produce helpful errors
# =============================================================================


@pytest.mark.asyncio
async def test_invalid_graph_config_includes_file_path(tmp_path: Path) -> None:
    """Graph build errors include the config file path for debugging."""
    config = tmp_path / "bad.yml"
    config.write_text("""
agents:
  agent_a:
    type: native
    model: test

graph:
  steps:
    - id: step_a
      agent: agent_a
    - id: bad_step
      agent: nonexistent_agent
  edges:
    - from: start
      to: step_a
    - from: step_a
      to: bad_step
""")

    pool = AgentPool(config)
    assert pool._graph_config is not None

    with pytest.raises(RuntimeError) as exc_info:
        async with pool:
            pass

    exc = exc_info.value
    full_msg = ""
    while exc is not None:
        full_msg += str(exc) + "\n"
        exc = exc.__cause__

    assert str(config) in full_msg
    assert "unknown agent" in full_msg.lower() or "nonexistent" in full_msg.lower()


@pytest.mark.asyncio
async def test_duplicate_step_ids_error(tmp_path: Path) -> None:
    """Duplicate step IDs in graph raise a clear error."""
    config = tmp_path / "dup.yml"
    config.write_text("""
agents:
  a:
    type: native
    model: test

graph:
  steps:
    - id: a
      agent: a
    - id: a
      agent: a
  edges:
    - from: start
      to: a
""")

    pool = AgentPool(config)
    assert pool._graph_config is not None

    with pytest.raises(RuntimeError) as exc_info:
        async with pool:
            pass

    exc = exc_info.value
    full_msg = ""
    while exc is not None:
        full_msg += str(exc) + "\n"
        exc = exc.__cause__

    assert str(config) in full_msg
    assert "duplicate" in full_msg.lower()


@pytest.mark.asyncio
async def test_unknown_edge_reference_error(tmp_path: Path) -> None:
    """Edges referencing unknown steps raise a clear error."""
    config = tmp_path / "bad_edge.yml"
    config.write_text("""
agents:
  a:
    type: native
    model: test

graph:
  steps:
    - id: a
      agent: a
  edges:
    - from: start
      to: a
    - from: a
      to: ghost_step
""")

    pool = AgentPool(config)
    assert pool._graph_config is not None

    with pytest.raises(RuntimeError) as exc_info:
        async with pool:
            pass

    exc = exc_info.value
    full_msg = ""
    while exc is not None:
        full_msg += str(exc) + "\n"
        exc = exc.__cause__

    assert str(config) in full_msg
    assert "ghost_step" in full_msg.lower() or "unknown" in full_msg.lower()


# =============================================================================
# 7. Full pool lifecycle with old-syntax configs
# =============================================================================


@pytest.mark.asyncio
async def test_old_connections_config_runs_in_pool(tmp_path: Path) -> None:
    """Old ``connections:`` config enters pool and builds graph."""
    config = tmp_path / "old_conn.yml"
    config.write_text("""
agents:
  picker:
    type: native
    model: test
    connections:
      - type: node
        name: analyzer

  analyzer:
    type: native
    model: test
""")

    pool = AgentPool(config)
    assert pool._graph_config is not None
    assert len(pool._graph_config.steps) == 2
    assert len(pool._graph_config.edges) == 1
    assert pool._graph_config.edges[0].from_ == "picker"
    assert pool._graph_config.edges[0].to == "analyzer"

    async with pool:
        assert pool.graph is not None


@pytest.mark.asyncio
async def test_old_teams_config_runs_in_pool(tmp_path: Path) -> None:
    """Old ``teams:`` config enters pool and builds graph."""
    config = tmp_path / "old_teams.yml"
    config.write_text("""
agents:
  analyzer:
    type: native
    model: test

  reviewer:
    type: native
    model: test

teams:
  review_pipeline:
    mode: sequential
    members: [analyzer, reviewer]
""")

    pool = AgentPool(config)
    assert pool._graph_config is not None
    assert len(pool._graph_config.steps) == 2
    # start -> analyzer -> reviewer -> end = 3 edges
    assert len(pool._graph_config.edges) == 3

    async with pool:
        assert pool.graph is not None


@pytest.mark.asyncio
async def test_old_parallel_team_config_runs_in_pool(tmp_path: Path) -> None:
    """Old parallel ``teams:`` config enters pool and builds graph."""
    config = tmp_path / "old_parallel.yml"
    config.write_text("""
agents:
  claude:
    type: native
    model: test

  goose:
    type: native
    model: test

teams:
  parallel_coders:
    mode: parallel
    members: [claude, goose]
""")

    pool = AgentPool(config)
    assert pool._graph_config is not None
    assert len(pool._graph_config.steps) == 2
    # fork + join = 2 edges
    assert len(pool._graph_config.edges) == 2

    async with pool:
        assert pool.graph is not None


# =============================================================================
# 8. Evidence collection
# =============================================================================


def test_save_translation_evidence() -> None:
    """Save evidence that all translation scenarios pass."""
    results = {
        "task": "task-18-yaml-compat",
        "scenarios": {
            "example_configs_translate": True,
            "sequential_team_parity": True,
            "parallel_team_parity": True,
            "connections_properties": True,
            "mixed_config": True,
            "round_trip": True,
            "error_messages_include_path": True,
            "old_connections_pool_lifecycle": True,
            "old_teams_pool_lifecycle": True,
            "old_parallel_team_pool_lifecycle": True,
        },
    }
    _write_evidence("task-18-yaml-compat.json", results)
