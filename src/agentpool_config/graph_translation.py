"""Translation from old YAML ``teams:`` / ``connections:`` syntax to new ``graph:`` definitions.

This module provides a mechanical translation layer that converts legacy
AgentPool configuration constructs into the new graph-based syntax.  The
translation is loss-less for all supported constructs and happens at config
load time, not at runtime.

Translation rules
-----------------

* ``team mode: sequential`` with members → chained ``Step`` edges
  (``start -> m1 -> m2 -> … -> end``).
* ``team mode: parallel`` with members → ``Fork`` + ``Join``
  (``start -> [m1, m2]``, ``[m1, m2] -> end``).
* ``connections`` list on agents → ``GraphBuilder`` edges with property
  mapping.
* ``Talk`` transforms → ``transform`` on edges.
* ``Talk`` filters → ``condition`` on edges.
* ``Talk`` stop conditions → ``stop_condition`` on edges.

A configuration that already contains a ``graph:`` section is returned
unchanged (passed through natively).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from pydantic import ConfigDict, Field, ImportString, model_serializer
from schemez import Schema

from agentpool_config.conditions import Condition
from agentpool_config.mcp_server import MCPServerConfig


# ---------------------------------------------------------------------------
# Graph configuration models
# ---------------------------------------------------------------------------


class GraphStepConfig(Schema):
    """Configuration for a single step (node) in a graph."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={"title": "Graph Step Configuration"},
    )

    id: str = Field(title="Step identifier")
    """Unique identifier for this step within the graph."""

    agent: str = Field(title="Agent name")
    """Name of the agent to execute at this step."""

    label: str | None = Field(default=None, title="Human-readable label")
    """Optional display label for this step."""

    mcp_servers: list[str | MCPServerConfig] = Field(default_factory=list)
    """MCP servers available to this step."""


class GraphJoinConfig(Schema):
    """Configuration for an explicit join node in a graph."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={"title": "Graph Join Configuration"},
    )

    id: str = Field(title="Join identifier")
    """Unique identifier for this join node."""

    inputs: list[str] = Field(title="Input step IDs")
    """Step IDs whose outputs should be joined."""

    reducer: ImportString[Callable[..., Any]] | None = Field(
        default=None, title="Reducer function"
    )
    """Optional import path to a reducer callable."""

    initial: Any = Field(default=None, title="Initial accumulator value")
    """Initial value for the join accumulator."""


class GraphEdgeConfig(Schema):
    """Configuration for an edge between steps in a graph."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={"title": "Graph Edge Configuration"},
    )

    from_: str | list[str] = Field(alias="from", title="Source step ID(s)")
    """ID of the step where this edge originates, or a list of IDs for an
    implicit ``Join``."""

    to: str | list[str] = Field(title="Target step ID(s)")
    """ID(s) of the step(s) this edge connects to.  A list creates a ``Fork``."""

    label: str | None = Field(default=None, title="Edge label")
    """Optional human-readable label."""

    condition: Condition | None = Field(default=None, title="Filter condition")
    """Condition for conditional routing (translated from ``filter_condition``)."""

    stop_condition: Condition | None = Field(default=None, title="Stop condition")
    """Condition that stops / disconnects this edge."""

    transform: ImportString[Callable[..., Any]] | None = Field(
        default=None, title="Transform function"
    )
    """Optional function to transform data flowing across this edge."""

    mode: str = Field(default="run", title="Connection mode")
    """How messages are handled.  One of ``run``, ``context``, or ``forward``."""

    async_: bool = Field(default=False, alias="async", title="Async execution")
    """Whether the edge executes asynchronously (does not wait for completion)."""

    map: bool = Field(default=False, title="Map fan-out")
    """Whether to fan out iterable outputs across parallel paths."""

    join: bool = Field(default=False, title="Join fan-in")
    """Whether to collect all results before continuing."""

    priority: int = Field(default=0, title="Priority")
    """Task priority (lower = higher priority)."""

    delay: timedelta | None = Field(default=None, title="Delay")
    """Optional delay before processing."""

    @model_serializer(mode="wrap")
    def _serialize(self, serializer: Any, info: Any) -> dict[str, Any]:
        """Serialize while preserving field aliases.

        ``schemez.Schema`` overrides the default serializer with a custom
        field-ordering implementation that does not account for aliases.
        This override ensures ``by_alias=True`` works correctly for edges.
        """
        return serializer(self)


class GraphConfig(Schema):
    """Top-level graph configuration."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={"title": "Graph Configuration"},
    )

    name: str | None = Field(default=None, title="Graph name")
    """Optional name for this graph."""

    steps: list[GraphStepConfig] = Field(default_factory=list)
    """Steps (nodes) in the graph."""

    edges: list[GraphEdgeConfig] = Field(default_factory=list)
    """Edges connecting steps in the graph."""

    joins: list[GraphJoinConfig] = Field(default_factory=list)
    """Explicit join configurations."""

    @model_serializer(mode="wrap")
    def _serialize(self, serializer: Any, info: Any) -> dict[str, Any]:
        """Serialize while preserving field aliases on nested models."""
        return serializer(self)


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------


def _ensure_step(
    step_id: str,
    agent_name: str,
    steps: list[GraphStepConfig],
    step_ids: set[str],
) -> None:
    """Add a step if it has not already been registered."""
    if step_id not in step_ids:
        steps.append(GraphStepConfig(id=step_id, agent=agent_name))
        step_ids.add(step_id)


def _translate_teams(
    config: dict[str, Any],
    steps: list[GraphStepConfig],
    edges: list[GraphEdgeConfig],
    joins: list[GraphJoinConfig],
    step_ids: set[str],
) -> None:
    """Translate ``teams:`` entries to graph steps and edges."""
    teams = config.get("teams", {})
    for team_name, team in teams.items():
        if not isinstance(team, dict):
            continue

        members = team.get("members", [])
        if not members:
            continue

        mode = team.get("mode", "sequential")

        # Ensure a step exists for every member.
        for member in members:
            _ensure_step(member, member, steps, step_ids)

        match mode:
            case "sequential":
                # Chain: start -> m1 -> m2 -> ... -> end
                prev = "start"
                for member in members:
                    edges.append(GraphEdgeConfig(**{"from": prev, "to": member}))
                    prev = member
                edges.append(GraphEdgeConfig(**{"from": prev, "to": "end"}))

            case "parallel":
                # Fork: start -> [m1, m2, …]
                edges.append(
                    GraphEdgeConfig(**{"from": "start", "to": list(members)})
                )
                # Join: [m1, m2, …] -> end
                edges.append(
                    GraphEdgeConfig(**{"from": list(members), "to": "end"})
                )

                # If the team has a shared_prompt, create an explicit join so
                # the prompt can be attached later when the graph is wired.
                if team.get("shared_prompt"):
                    join_id = f"join_{team_name}"
                    joins.append(
                        GraphJoinConfig(
                            id=join_id,
                            inputs=list(members),
                        )
                    )


def _translate_connections(
    config: dict[str, Any],
    steps: list[GraphStepConfig],
    edges: list[GraphEdgeConfig],
    joins: list[GraphJoinConfig],
    step_ids: set[str],
) -> None:
    """Translate agent ``connections:`` entries to graph edges."""
    agents = config.get("agents", {})
    for agent_name, agent in agents.items():
        if not isinstance(agent, dict):
            continue

        connections = agent.get("connections", [])
        for conn in connections:
            if not isinstance(conn, dict):
                continue

            match conn.get("type"):
                case "node":
                    _translate_node_connection(
                        agent_name, conn, steps, edges, step_ids
                    )
                case "file":
                    _translate_file_connection(
                        agent_name, conn, steps, edges, step_ids
                    )
                case "callable":
                    _translate_callable_connection(
                        agent_name, conn, steps, edges, step_ids
                    )


def _translate_node_connection(
    source: str,
    conn: dict[str, Any],
    steps: list[GraphStepConfig],
    edges: list[GraphEdgeConfig],
    step_ids: set[str],
) -> None:
    """Translate a single ``NodeConnectionConfig`` to a graph edge."""
    target = conn.get("name")
    if not target:
        return

    _ensure_step(source, source, steps, step_ids)
    _ensure_step(target, target, steps, step_ids)

    connection_type = conn.get("connection_type", "run")
    wait_for_completion = conn.get("wait_for_completion", True)
    filter_condition = conn.get("filter_condition")
    stop_condition = conn.get("stop_condition")
    transform = conn.get("transform")
    priority = conn.get("priority", 0)
    delay = conn.get("delay")

    edges.append(
        GraphEdgeConfig(
            **{
                "from": source,
                "to": target,
                "mode": connection_type,
                "async": not wait_for_completion,
                "condition": filter_condition,
                "stop_condition": stop_condition,
                "transform": transform,
                "priority": priority,
                "delay": delay,
            }
        )
    )


def _translate_file_connection(
    source: str,
    conn: dict[str, Any],
    steps: list[GraphStepConfig],
    edges: list[GraphEdgeConfig],
    step_ids: set[str],
) -> None:
    """Translate a ``FileConnectionConfig`` to a synthetic step + edge."""
    path = conn.get("path", "unknown")
    target_id = f"file_writer_{path}"

    _ensure_step(source, source, steps, step_ids)
    _ensure_step(target_id, target_id, steps, step_ids)

    edges.append(
        GraphEdgeConfig(
            **{
                "from": source,
                "to": target_id,
                "mode": "run",
                "transform": conn.get("transform"),
                "priority": conn.get("priority", 0),
                "delay": conn.get("delay"),
            }
        )
    )


def _translate_callable_connection(
    source: str,
    conn: dict[str, Any],
    steps: list[GraphStepConfig],
    edges: list[GraphEdgeConfig],
    step_ids: set[str],
) -> None:
    """Translate a ``CallableConnectionConfig`` to a synthetic step + edge."""
    callable_path = conn.get("callable", "unknown")
    target_id = f"callable_{callable_path}"

    _ensure_step(source, source, steps, step_ids)
    _ensure_step(target_id, target_id, steps, step_ids)

    edges.append(
        GraphEdgeConfig(
            **{
                "from": source,
                "to": target_id,
                "mode": "run",
                "transform": conn.get("transform"),
                "priority": conn.get("priority", 0),
                "delay": conn.get("delay"),
            }
        )
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def translate_config(config: dict[str, Any]) -> GraphConfig | None:
    """Translate old ``teams:`` / ``connections:`` syntax to ``graph:`` definitions.

    If the configuration already contains a ``graph`` key, it is returned
    unchanged so that native graph syntax is passed through.

    Args:
        config: Raw configuration dictionary (before Pydantic validation).

    Returns:
        A ``GraphConfig`` instance if translation produces steps or edges,
        otherwise ``None``.
    """
    # Native graph syntax takes precedence.
    if "graph" in config:
        existing = config["graph"]
        if isinstance(existing, dict):
            return GraphConfig.model_validate(existing)
        if isinstance(existing, GraphConfig):
            return existing
        return None

    steps: list[GraphStepConfig] = []
    edges: list[GraphEdgeConfig] = []
    joins: list[GraphJoinConfig] = []
    step_ids: set[str] = set()

    _translate_teams(config, steps, edges, joins, step_ids)
    _translate_connections(config, steps, edges, joins, step_ids)

    if not steps and not edges:
        return None

    return GraphConfig(steps=steps, edges=edges, joins=joins)
