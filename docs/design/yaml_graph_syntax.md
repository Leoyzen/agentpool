# YAML Graph Syntax Design

## Overview

This document defines the new `graph:` section for AgentPool YAML configs that maps directly to pydantic-ai's `GraphBuilder` API. The design preserves the existing `teams:` and `connections:` config models (additive only) while providing a first-class graph syntax for declarative workflow definition.

## Design Goals

1. **Direct GraphBuilder mapping**: Every construct in the YAML has a 1:1 equivalent in `GraphBuilder`.
2. **Mechanical translation**: All existing `teams:` + `connections:` configs can be translated to `graph:` without loss of information.
3. **Familiar patterns**: Reuse concepts from existing AgentPool configs (agents as nodes, connections as edges).
4. **Extensibility**: Support for advanced GraphBuilder features (Fork, Join, Decision, map) from the start.

## New `graph:` Syntax

### Top-Level Structure

```yaml
graph:
  name: support_pipeline          # Optional, defaults to key name
  steps:
    - id: triage                  # Required, unique within graph
      agent: triage_agent         # References agent defined in agents:
      label: Triage Step          # Optional, human-readable label
    - id: resolve
      agent: resolver_agent
  edges:
    - from: triage
      to: resolve
      label: Escalate             # Optional edge label
```

### Implicit Start and End Nodes

Every graph implicitly includes `start` and `end` nodes. You do not declare them in `steps:` but may reference them in `edges:`.

```yaml
graph:
  steps:
    - id: analyzer
      agent: analyzer_agent
  edges:
    - from: start
      to: analyzer
    - from: analyzer
      to: end
```

If `edges:` is omitted, a default linear pipeline is inferred from `steps` declaration order:

```yaml
graph:
  steps:
    - id: step_a
      agent: agent_a
    - id: step_b
      agent: agent_b
    - id: step_c
      agent: agent_c
  # Implicit edges: start -> step_a -> step_b -> step_c -> end
```

### Parallel Execution: Fork + Join

Parallel branches are declared by listing multiple targets in `to:`. This maps to GraphBuilder's `Fork` (broadcast) + `Join`.

```yaml
graph:
  name: parallel_analysis
  steps:
    - id: splitter
      agent: input_agent
    - id: researcher
      agent: research_agent
    - id: analyst
      agent: analysis_agent
    - id: summarizer
      agent: summary_agent
  edges:
    - from: start
      to: splitter
    - from: splitter
      to: [researcher, analyst]    # Broadcast/Fork: same input to both
    - from: [researcher, analyst]  # Join: wait for both before continuing
      to: summarizer
    - from: summarizer
      to: end
```

The `to: [researcher, analyst]` syntax creates a **Fork** node that broadcasts the same input to both branches. The `from: [researcher, analyst]` syntax creates an implicit **Join** node that waits for all listed sources to complete before forwarding their combined outputs.

For explicit Join configuration (custom reducer, initial state):

```yaml
graph:
  steps:
    - id: researcher
      agent: research_agent
    - id: analyst
      agent: analysis_agent
  joins:
    - id: merge_results
      inputs: [researcher, analyst]
      reducer: mymodule.merge_outputs   # Import path to reducer callable
      initial: {}                       # Initial accumulator value
  edges:
    - from: start
      to: [researcher, analyst]
    - from: merge_results
      to: end
```

### Conditional Branching: Decision

Conditional edges map to GraphBuilder's `Decision` nodes.

```yaml
graph:
  steps:
    - id: classifier
      agent: classifier_agent
    - id: handle_error
      agent: error_agent
    - id: handle_success
      agent: success_agent
  edges:
    - from: start
      to: classifier
    - from: classifier
      to: handle_error
      condition:
        type: match
        field: sentiment
        value: negative
    - from: classifier
      to: handle_success
      condition:
        type: match
        field: sentiment
        value: positive
    - from: handle_error
      to: end
    - from: handle_success
      to: end
```

### Map (Iterable Fan-Out)

Map edges spread iterable outputs across parallel paths, one per item.

```yaml
graph:
  steps:
    - id: url_fetcher
      agent: fetch_agent            # Returns list[str] of URLs
    - id: page_processor
      agent: process_agent          # Processes a single URL
    - id: result_aggregator
      agent: aggregate_agent
  edges:
    - from: start
      to: url_fetcher
    - from: url_fetcher
      to: page_processor
      map: true                     # Fan-out: one edge per URL in the list
    - from: page_processor
      to: result_aggregator
      join: true                    # Fan-in: collect all results
    - from: result_aggregator
      to: end
```

### Edge Transforms

Transform data as it flows across an edge.

```yaml
graph:
  steps:
    - id: extractor
      agent: extract_agent
    - id: formatter
      agent: format_agent
  edges:
    - from: extractor
      to: formatter
      transform: mymodule.prepare_input   # Import path to sync callable
```

## Translation Rules from `teams:` + `connections:`

### Rule 1: `team mode: sequential` -> Chained Steps

**Before:**
```yaml
teams:
  review_pipeline:
    mode: sequential
    members: [analyzer, reviewer, formatter]
```

**After:**
```yaml
graph:
  name: review_pipeline
  steps:
    - id: analyzer
      agent: analyzer
    - id: reviewer
      agent: reviewer
    - id: formatter
      agent: formatter
  # Implicit edges: start -> analyzer -> reviewer -> formatter -> end
```

Translation: `members` list order becomes step declaration order; edges are auto-inferred as a linear chain from `start` through each member to `end`.

### Rule 2: `team mode: parallel` -> Fork + Join

**Before:**
```yaml
teams:
  parallel_coders:
    mode: parallel
    members: [claude, goose]
```

**After:**
```yaml
graph:
  name: parallel_coders
  steps:
    - id: claude
      agent: claude
    - id: goose
      agent: goose
  edges:
    - from: start
      to: [claude, goose]     # Fork (broadcast)
    - from: [claude, goose]   # Join
      to: end
```

Translation: A parallel team with N members becomes a graph with a Fork broadcasting to all N steps, followed by an implicit Join that waits for all N steps before reaching `end`.

### Rule 3: Agent `connections:` -> Edges

**Before:**
```yaml
agents:
  picker:
    type: native
    model: openai:gpt-5-nano
    connections:
      - type: node
        name: analyzer
        connection_type: run
        wait_for_completion: true

  analyzer:
    type: native
    model: openai:gpt-5-nano
```

**After:**
```yaml
agents:
  picker:
    type: native
    model: openai:gpt-5-nano

  analyzer:
    type: native
    model: openai:gpt-5-nano

graph:
  steps:
    - id: picker
      agent: picker
    - id: analyzer
      agent: analyzer
  edges:
    - from: picker
      to: analyzer
```

Translation: Each `NodeConnectionConfig` in `connections:` becomes an `edges` entry. The `connection_type`, `wait_for_completion`, `filter_condition`, `stop_condition`, and `transform` fields from the old config map to edge-level properties (see Rule 4).

### Rule 4: Connection Properties -> Edge Properties

| Old Property               | New Property                |
|----------------------------|-----------------------------|
| `connection_type: run`     | `run: true` (default)       |
| `connection_type: context` | `mode: context`             |
| `connection_type: forward` | `mode: forward`             |
| `wait_for_completion: true`| `async: false` (default)    |
| `wait_for_completion: false`| `async: true`              |
| `filter_condition`         | `condition`                 |
| `stop_condition`           | `stop_condition`            |
| `transform`                | `transform`                 |
| `priority`                 | `priority`                  |
| `delay`                    | `delay`                     |

### Rule 5: Round-Robin / Cyclic Connections

**Before:**
```yaml
agents:
  player1:
    connections:
      - type: node
        name: player2
        connection_type: run
        stop_condition:
          type: cost_limit
          max_cost: 0.01

  player2:
    connections:
      - type: node
        name: player3
        connection_type: run

  player3:
    connections:
      - type: node
        name: player1
        connection_type: run
```

**After:**
```yaml
graph:
  steps:
    - id: player1
      agent: player1
    - id: player2
      agent: player2
    - id: player3
      agent: player3
  edges:
    - from: player1
      to: player2
      stop_condition:
        type: cost_limit
        max_cost: 0.01
    - from: player2
      to: player3
    - from: player3
      to: player1
```

Translation: Cyclic connections translate directly to cyclic edges in the graph. The `stop_condition` moves from the connection config to the edge config.

## Complete Before/After Example

### Before (teams + connections)

```yaml
agents:
  triage:
    type: native
    model: openai:gpt-5-nano
    connections:
      - type: node
        name: resolver

  resolver:
    type: native
    model: openai:gpt-5-nano

teams:
  analysis_group:
    mode: parallel
    members: [researcher, analyst]

  review_pipeline:
    mode: sequential
    members: [analyzer, reviewer, formatter]

agents:
  researcher:
    type: native
    model: openai:gpt-5-nano

  analyst:
    type: native
    model: openai:gpt-5-nano

  analyzer:
    type: native
    model: openai:gpt-5-nano

  reviewer:
    type: native
    model: openai:gpt-5-nano

  formatter:
    type: native
    model: openai:gpt-5-nano
```

### After (graph)

```yaml
agents:
  triage:
    type: native
    model: openai:gpt-5-nano

  resolver:
    type: native
    model: openai:gpt-5-nano

  researcher:
    type: native
    model: openai:gpt-5-nano

  analyst:
    type: native
    model: openai:gpt-5-nano

  analyzer:
    type: native
    model: openai:gpt-5-nano

  reviewer:
    type: native
    model: openai:gpt-5-nano

  formatter:
    type: native
    model: openai:gpt-5-nano

graph:
  name: full_workflow
  steps:
    - id: triage
      agent: triage
    - id: resolver
      agent: resolver
    - id: researcher
      agent: researcher
    - id: analyst
      agent: analyst
    - id: analyzer
      agent: analyzer
    - id: reviewer
      agent: reviewer
    - id: formatter
      agent: formatter
  edges:
    # Direct agent connections
    - from: triage
      to: resolver

    # Parallel team (analysis_group)
    - from: start
      to: [researcher, analyst]     # Fork: broadcast to parallel branches
    - from: [researcher, analyst]   # Join: collect both results
      to: end

    # Sequential team (review_pipeline)
    - from: start
      to: analyzer
    - from: analyzer
      to: reviewer
    - from: reviewer
      to: formatter
    - from: formatter
      to: end
```

## GraphBuilder API Mapping

| YAML Construct            | GraphBuilder API Call                                    |
|---------------------------|----------------------------------------------------------|
| `steps`                   | `builder.step(call=..., node_id=...)`                    |
| `edges: - from: a to: b`  | `builder.add_edge(step_a, step_b)`                       |
| `to: [a, b]`              | `builder.add(builder.edge_from(src).to(step_a, step_b))` |
| `to: [a, b]` (Fork)       | Creates implicit `Fork(is_map=False)` via BroadcastMarker|
| `from: [a, b]` (Join)     | Creates implicit `Join` node with default reducer        |
| `joins:` explicit         | `builder.join(reducer=..., initial=..., node_id=...)`    |
| `condition:`              | `builder.decision()` + `builder.match(...).to(...)`      |
| `map: true`               | `builder.add_mapping_edge(...)` or `edge.map(...)`       |
| `transform:`              | `builder.edge_from(src).transform(...).to(dst)`          |
| `start` reference         | `builder.start_node`                                     |
| `end` reference           | `builder.end_node`                                       |

## Verification: All Existing Configs Are Translatable

### `docs/examples/mcp_servers_yaml/config.yml`

Contains `connections:` only. Translation: add a `graph` with two steps and one edge.

### `docs/examples/round_robin/config.yml`

Contains cyclic `connections:` with `stop_condition`. Translation: add a `graph` with three steps and three cyclic edges; `stop_condition` moves to edge level.

### `docs/examples/crewai_flow/config.yml`

No teams or connections. Agents are standalone. Translation: optional `graph` with no edges (or `steps` only, relying on implicit start/end without connections).

### `docs/examples/structured_response/config.yml`

No teams or connections. Standalone agents. Translation: same as above.

### `docs/examples/human_interaction/config.yml`

No teams or connections. Standalone agents. Translation: same as above.

### `docs/examples/model_comparison/config.yml`

No teams or connections. Standalone agent. Translation: same as above.

### `docs/examples/mcp_skills/config.yml`

No teams or connections. Standalone agents. Translation: same as above.

### `docs/examples/download_agents/config.yml`

No teams or connections. Standalone agents coordinated via tool use. Translation: optional `graph` with no edges, or agents remain standalone.

### `docs/examples/download_workers/config.yml`

No teams or connections. Standalone agents coordinated via tool use. Translation: same as above.

### `docs/examples/create_docs/config.yml`

No teams or connections. Standalone agents. Translation: optional `graph` with no edges.

### `docs/examples/pytest_style/config.yml`

Empty agents. Translation: empty `graph` or omitted.

**Conclusion**: Every existing config can be mechanically translated. Configs without `teams` or `connections` simply omit `graph` or declare standalone `steps`. Configs with `teams` translate via Rules 1 and 2. Configs with `connections` translate via Rules 3 and 4.

## Open Questions

1. **Shared prompt on teams**: The old `TeamConfig.shared_prompt` does not have a direct GraphBuilder equivalent. Options:
   - Add a `prompt` field on `graph` that injects context into all steps.
   - Ignore for now (lossy translation) and document it.

2. **MCP servers on teams**: `TeamConfig` allowed `mcp_servers`. In the graph model, MCP servers are agent-level. Translation: move team-level MCP servers to each agent in the graph, or add `mcp_servers` to `steps` entries.

3. **Backwards compatibility**: Should `teams:` and `connections:` be deprecated in favor of `graph:`, or should both coexist indefinitely? The current design assumes coexistence.

4. **Nested teams**: Old config allowed teams as members of other teams. Graph translation would flatten the nested team into its constituent steps and edges within the parent graph.

## Appendix: Pydantic Model Sketch

```python
class GraphStepConfig(Schema):
    id: str
    agent: str
    label: str | None = None
    mcp_servers: list[str | MCPServerConfig] = Field(default_factory=list)

class GraphJoinConfig(Schema):
    id: str
    inputs: list[str]
    reducer: ImportString[Callable[..., Any]] | None = None
    initial: Any = None

class GraphEdgeCondition(Schema):
    type: str
    # Discriminated union based on type

class GraphEdgeConfig(Schema):
    from_: str = Field(alias="from")
    to: str | list[str]
    label: str | None = None
    condition: GraphEdgeCondition | None = None
    stop_condition: Condition | None = None
    transform: ImportString[Callable[..., Any]] | None = None
    mode: Literal["run", "context", "forward"] = "run"
    async_: bool = Field(default=False, alias="async")
    map: bool = False
    join: bool = False
    priority: int = 0
    delay: timedelta | None = None

class GraphConfig(Schema):
    name: str | None = None
    steps: list[GraphStepConfig] = Field(default_factory=list)
    edges: list[GraphEdgeConfig] = Field(default_factory=list)
    joins: list[GraphJoinConfig] = Field(default_factory=list)
```

## Appendix: Mechanical Translation Algorithm

```python
def translate_to_graph(manifest: AgentsManifest) -> GraphConfig | None:
    steps: list[GraphStepConfig] = []
    edges: list[GraphEdgeConfig] = []
    joins: list[GraphJoinConfig] = []
    step_ids: set[str] = set()

    # 1. Translate teams
    for team_name, team in manifest.teams.items():
        if team.mode == "sequential":
            # Add steps for each member (if not already added as agent steps)
            for member in team.members:
                if member not in step_ids:
                    steps.append(GraphStepConfig(id=member, agent=member))
                    step_ids.add(member)
            # Chain edges: start -> m1 -> m2 -> ... -> end
            prev = "start"
            for member in team.members:
                edges.append(GraphEdgeConfig(from_=prev, to=member))
                prev = member
            edges.append(GraphEdgeConfig(from_=prev, to="end"))

        elif team.mode == "parallel":
            for member in team.members:
                if member not in step_ids:
                    steps.append(GraphStepConfig(id=member, agent=member))
                    step_ids.add(member)
            # Fork: start -> [m1, m2, ...]
            edges.append(GraphEdgeConfig(from_="start", to=list(team.members)))
            # Join: [m1, m2, ...] -> end
            edges.append(GraphEdgeConfig(from_=list(team.members), to="end"))

    # 2. Translate agent connections
    for agent_name, agent in manifest.agents.items():
        for conn in agent.connections:
            match conn:
                case NodeConnectionConfig(name=target):
                    if agent_name not in step_ids:
                        steps.append(GraphStepConfig(id=agent_name, agent=agent_name))
                        step_ids.add(agent_name)
                    if target not in step_ids:
                        steps.append(GraphStepConfig(id=target, agent=target))
                        step_ids.add(target)
                    edges.append(GraphEdgeConfig(
                        from_=agent_name,
                        to=target,
                        mode=conn.connection_type,
                        async_=not conn.wait_for_completion,
                        condition=conn.filter_condition,
                        stop_condition=conn.stop_condition,
                        transform=conn.transform,
                        priority=conn.priority,
                        delay=conn.delay,
                    ))
                case FileConnectionConfig() | CallableConnectionConfig():
                    # These create synthetic nodes; add as steps with special handling
                    pass

    if not steps and not edges:
        return None

    return GraphConfig(steps=steps, edges=edges, joins=joins)
```
