"""Tests for the graph translation module.

Validates that old ``teams:`` / ``connections:`` syntax is correctly
converted to the new ``graph:`` definition format.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from agentpool_config.graph_translation import (
    GraphConfig,
    GraphEdgeConfig,
    GraphJoinConfig,
    GraphStepConfig,
    translate_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _edge_dict(edge: GraphEdgeConfig) -> dict[str, Any]:
    """Return a minimal dict for comparing edge topology."""
    return {"from": edge.from_, "to": edge.to}


# ---------------------------------------------------------------------------
# Empty / passthrough
# ---------------------------------------------------------------------------


def test_translate_empty_config() -> None:
    """Empty config with no teams or connections returns None."""
    result = translate_config({})
    assert result is None


def test_translate_config_with_no_teams_or_connections() -> None:
    """Config with only agents but no connections returns None."""
    config = {
        "agents": {
            "assistant": {"type": "native", "model": "openai:gpt-4"},
        }
    }
    result = translate_config(config)
    assert result is None


def test_translate_passthrough_existing_graph() -> None:
    """Config that already has a ``graph`` key is passed through unchanged."""
    existing = {
        "name": "my_graph",
        "steps": [{"id": "step1", "agent": "agent1"}],
        "edges": [{"from": "start", "to": "step1"}],
    }
    config = {"graph": existing}
    result = translate_config(config)
    assert isinstance(result, GraphConfig)
    assert result.name == "my_graph"
    assert len(result.steps) == 1
    assert len(result.edges) == 1


# ---------------------------------------------------------------------------
# Team translation
# ---------------------------------------------------------------------------


def test_translate_sequential_team() -> None:
    """Sequential team becomes a linear chain of edges."""
    config = {
        "teams": {
            "review_pipeline": {
                "mode": "sequential",
                "members": ["analyzer", "reviewer", "formatter"],
            }
        }
    }
    result = translate_config(config)
    assert result is not None

    # Three steps created
    step_ids = {s.id for s in result.steps}
    assert step_ids == {"analyzer", "reviewer", "formatter"}

    # Chain: start -> analyzer -> reviewer -> formatter -> end
    edges = result.edges
    assert len(edges) == 4
    assert _edge_dict(edges[0]) == {"from": "start", "to": "analyzer"}
    assert _edge_dict(edges[1]) == {"from": "analyzer", "to": "reviewer"}
    assert _edge_dict(edges[2]) == {"from": "reviewer", "to": "formatter"}
    assert _edge_dict(edges[3]) == {"from": "formatter", "to": "end"}


def test_translate_parallel_team() -> None:
    """Parallel team becomes Fork + Join edges."""
    config = {
        "teams": {
            "parallel_coders": {
                "mode": "parallel",
                "members": ["claude", "goose"],
            }
        }
    }
    result = translate_config(config)
    assert result is not None

    step_ids = {s.id for s in result.steps}
    assert step_ids == {"claude", "goose"}

    # Fork: start -> [claude, goose]
    # Join: [claude, goose] -> end
    edges = result.edges
    assert len(edges) == 2
    assert _edge_dict(edges[0]) == {"from": "start", "to": ["claude", "goose"]}
    assert _edge_dict(edges[1]) == {"from": ["claude", "goose"], "to": "end"}


def test_translate_parallel_team_with_shared_prompt_creates_join() -> None:
    """Parallel team with shared_prompt creates an explicit join config."""
    config = {
        "teams": {
            "hybrid": {
                "mode": "parallel",
                "members": ["a", "b"],
                "shared_prompt": "Work together",
            }
        }
    }
    result = translate_config(config)
    assert result is not None
    assert len(result.joins) == 1
    join = result.joins[0]
    assert join.id == "join_hybrid"
    assert join.inputs == ["a", "b"]


# ---------------------------------------------------------------------------
# Connection translation
# ---------------------------------------------------------------------------


def test_translate_simple_node_connection() -> None:
    """A simple node connection becomes a single edge."""
    config = {
        "agents": {
            "picker": {
                "type": "native",
                "model": "openai:gpt-4",
                "connections": [
                    {"type": "node", "name": "analyzer"},
                ],
            },
            "analyzer": {
                "type": "native",
                "model": "openai:gpt-4",
            },
        }
    }
    result = translate_config(config)
    assert result is not None

    # Steps for both agents
    step_ids = {s.id for s in result.steps}
    assert step_ids == {"picker", "analyzer"}

    # One edge
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.from_ == "picker"
    assert edge.to == "analyzer"
    assert edge.mode == "run"
    assert edge.async_ is False


def test_translate_connection_properties() -> None:
    """All connection properties map correctly to edge properties."""
    config = {
        "agents": {
            "src": {
                "type": "native",
                "model": "openai:gpt-4",
                "connections": [
                    {
                        "type": "node",
                        "name": "dst",
                        "connection_type": "context",
                        "wait_for_completion": False,
                        "transform": "builtins.print",
                        "priority": 5,
                        "delay": "00:00:05",
                    },
                ],
            },
        }
    }
    result = translate_config(config)
    assert result is not None
    edge = result.edges[0]

    assert edge.mode == "context"
    assert edge.async_ is True  # wait_for_completion=False -> async=True
    assert edge.transform is print
    assert edge.priority == 5
    assert edge.delay == timedelta(seconds=5)


def test_translate_filter_condition() -> None:
    """filter_condition maps to edge condition."""
    config = {
        "agents": {
            "src": {
                "type": "native",
                "model": "openai:gpt-4",
                "connections": [
                    {
                        "type": "node",
                        "name": "dst",
                        "filter_condition": {
                            "type": "word_match",
                            "words": ["error"],
                        },
                    },
                ],
            },
        }
    }
    result = translate_config(config)
    assert result is not None
    edge = result.edges[0]
    assert edge.condition is not None
    assert edge.condition.type == "word_match"
    # type checker cannot narrow the union, but at runtime this is a WordMatchCondition
    words = getattr(edge.condition, "words", None)
    assert words == ["error"]


def test_translate_stop_condition() -> None:
    """stop_condition maps to edge stop_condition."""
    config = {
        "agents": {
            "src": {
                "type": "native",
                "model": "openai:gpt-4",
                "connections": [
                    {
                        "type": "node",
                        "name": "dst",
                        "stop_condition": {
                            "type": "cost_limit",
                            "max_cost": 0.01,
                        },
                    },
                ],
            },
        }
    }
    result = translate_config(config)
    assert result is not None
    edge = result.edges[0]
    assert edge.stop_condition is not None
    assert edge.stop_condition.type == "cost_limit"
    max_cost = getattr(edge.stop_condition, "max_cost", None)
    assert max_cost == 0.01


# ---------------------------------------------------------------------------
# Complex / integration
# ---------------------------------------------------------------------------


def test_translate_round_robin() -> None:
    """Cyclic connections translate to cyclic edges."""
    config = {
        "agents": {
            "player1": {
                "type": "native",
                "model": "openai:gpt-4",
                "connections": [
                    {"type": "node", "name": "player2"},
                ],
            },
            "player2": {
                "type": "native",
                "model": "openai:gpt-4",
                "connections": [
                    {
                        "type": "node",
                        "name": "player3",
                        "stop_condition": {
                            "type": "cost_limit",
                            "max_cost": 0.01,
                        },
                    },
                ],
            },
            "player3": {
                "type": "native",
                "model": "openai:gpt-4",
                "connections": [
                    {"type": "node", "name": "player1"},
                ],
            },
        }
    }
    result = translate_config(config)
    assert result is not None

    assert len(result.steps) == 3
    assert len(result.edges) == 3

    edge_map = {
        e.from_: {
            "to": e.to,
            "stop": e.stop_condition.type if e.stop_condition else None,
        }
        for e in result.edges
    }
    assert edge_map["player1"]["to"] == "player2"
    assert edge_map["player2"]["to"] == "player3"
    assert edge_map["player2"]["stop"] == "cost_limit"
    assert edge_map["player3"]["to"] == "player1"


def test_translate_file_connection() -> None:
    """File connection creates a synthetic step + edge."""
    config = {
        "agents": {
            "src": {
                "type": "native",
                "model": "openai:gpt-4",
                "connections": [
                    {
                        "type": "file",
                        "path": "logs/output.txt",
                        "priority": 3,
                    },
                ],
            },
        }
    }
    result = translate_config(config)
    assert result is not None

    step_ids = {s.id for s in result.steps}
    assert "file_writer_logs/output.txt" in step_ids

    edge = result.edges[0]
    assert edge.from_ == "src"
    assert edge.to == "file_writer_logs/output.txt"
    assert edge.priority == 3


def test_translate_callable_connection() -> None:
    """Callable connection creates a synthetic step + edge."""
    config = {
        "agents": {
            "src": {
                "type": "native",
                "model": "openai:gpt-4",
                "connections": [
                    {
                        "type": "callable",
                        "callable": "mymodule.process_msg",
                    },
                ],
            },
        }
    }
    result = translate_config(config)
    assert result is not None

    step_ids = {s.id for s in result.steps}
    assert "callable_mymodule.process_msg" in step_ids

    edge = result.edges[0]
    assert edge.from_ == "src"
    assert edge.to == "callable_mymodule.process_msg"


def test_translate_teams_and_connections_combined() -> None:
    """Teams and connections are both translated in a single graph."""
    config = {
        "agents": {
            "triage": {
                "type": "native",
                "model": "openai:gpt-4",
                "connections": [{"type": "node", "name": "resolver"}],
            },
            "resolver": {"type": "native", "model": "openai:gpt-4"},
        },
        "teams": {
            "analysis_group": {
                "mode": "parallel",
                "members": ["researcher", "analyst"],
            }
        },
    }
    result = translate_config(config)
    assert result is not None

    step_ids = {s.id for s in result.steps}
    assert step_ids == {"triage", "resolver", "researcher", "analyst"}

    # Should have edges from both teams and connections
    assert len(result.edges) == 3


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_graph_config_serializes_with_aliases() -> None:
    """model_dump(by_alias=True) produces the expected YAML keys."""
    edge = GraphEdgeConfig(**{"from": "a", "to": "b", "async": True})
    data = edge.model_dump(by_alias=True)
    assert "from" in data
    assert "async" in data
    assert "from_" not in data
    assert "async_" not in data
    assert data["from"] == "a"
    assert data["async"] is True


def test_graph_config_round_trip() -> None:
    """A translated graph can be validated from its own dump."""
    config = {
        "teams": {
            "pipe": {
                "mode": "sequential",
                "members": ["step1", "step2"],
            }
        }
    }
    result = translate_config(config)
    assert result is not None

    dumped = result.model_dump(by_alias=True)
    restored = GraphConfig.model_validate(dumped)
    assert len(restored.steps) == len(result.steps)
    assert len(restored.edges) == len(result.edges)
