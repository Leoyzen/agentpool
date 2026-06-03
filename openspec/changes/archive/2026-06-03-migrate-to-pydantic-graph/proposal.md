## Why

AgentPool currently implements custom orchestration abstractions for multi-agent execution: `MessageNode` as the base processing unit, `Team` for parallel execution via `asyncio.gather()`, and `TeamRun` for sequential pipelines via custom forwarding logic. These were designed before pydantic-ai had graph-based orchestration.

pydantic-ai now includes `pydantic_graph` — a graph execution engine with `GraphBuilder`, `BaseNode`, `End`, `Fork`, `Join`, `Decision`, and `GraphRun`. However, `pydantic_graph.BaseNode` is a **passive execution step** (has `run(ctx)` returning a node or `End`), fundamentally different from AgentPool's `MessageNode` which is an **active lifecycle object** (signals, connections, MCP servers, event handlers, storage).

**The key insight from architecture review**: `MessageNode` should NOT extend `BaseNode`. Instead, we create **`AgentNode(BaseNode)` wrapper nodes** that adapt agents to graph execution while keeping `MessageNode` independent. This preserves AgentPool's dynamic connection capabilities, agent lifecycles, and session management while leveraging pydantic_graph for static workflow definitions from YAML.

This change depends on `sessionpool-only-architecture` and `thin-pydantic-ai-wrappers`.

## What Changes

- **BREAKING**: `Team` parallel execution for YAML-defined teams is reimplemented using `pydantic_graph.GraphBuilder` with `Fork` + `Join`. Programmatic team construction (`agent & other`) continues to use `asyncio.gather()`.
- **BREAKING**: `TeamRun` sequential execution for YAML-defined teams is reimplemented via `GraphBuilder` sequential node chains. Programmatic pipeline construction (`agent | other`) keeps custom forwarding.
- `AgentNode` is introduced as a `pydantic_graph.BaseNode` wrapper that encapsulates an AgentPool agent. **Agent instances remain completely stateless** — session ID and all run-scoped state are passed via `AgentRunContext` and method parameters, never mutated on the agent instance.
- `MessageNode` remains unchanged — it does NOT extend `BaseNode`. `ConnectionManager` / `Talk` remain independent for dynamic runtime connections.
- YAML `teams:` configuration supports graph-based workflow definitions alongside legacy team definitions during migration.
- Graph execution uses **builder-based `GraphRun`** (not deprecated legacy API).
- **Cycles are disallowed in v1** — graph build-time cycle detection rejects cyclic workflows.
- Mermaid diagram generation is available for YAML-defined team/workflow definitions.

## Capabilities

### New Capabilities

- `agentnode-wrapper`: `AgentNode(BaseNode)` wraps AgentPool agents for graph execution without modifying agent lifecycle or MessageNode.
- `pydantic-graph-teams`: YAML-defined `Team` parallel execution uses `GraphBuilder` + `Fork` + `Join`; YAML-defined `TeamRun` sequential execution uses sequential node chains.
- `static-graph-workflows`: Complex workflows (conditional branching via `Decision`) are supported for YAML-defined static workflows.
- `graph-visualization`: YAML team and workflow definitions can generate Mermaid diagrams.

### Modified Capabilities

- `team-execution`: Requirements change — YAML-defined parallel/sequential teams use `pydantic_graph`; programmatic teams keep existing implementation.
- `message-routing`: Requirements unchanged — `ConnectionManager` / `Talk` remain independent. Graph edges are used only for static YAML team connections.

## Impact

- `agentpool/delegation/team.py`: `Team` gains graph-based path for YAML config; `asyncio.gather()` path preserved for programmatic construction.
- `agentpool/delegation/base_team.py`: `TeamRun` gains graph-based path for YAML config; custom forwarding preserved for programmatic construction.
- `agentpool/messaging/agent_node.py`: **New file** — `AgentNode` wrapper implementing `BaseNode`.
- `agentpool/messaging/messagenode.py`: **Unchanged** — no BaseNode extension.
- `agentpool/messaging/connection_manager.py`: **Unchanged** — remains independent.
- `agentpool/orchestrator/core.py`: `TurnRunner` integrates with `GraphRun` for YAML team execution.
- `agentpool/delegation/pool.py`: `AgentPool` uses `GraphBuilder` for YAML team/workflow construction.
- `agentpool_config/teams.py`: YAML team config supports graph workflow definitions.
- Tests: YAML team tests rewritten for graph primitives; programmatic team tests unchanged.
