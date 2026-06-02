## Context

After `thin-pydantic-ai-wrappers` completes, native agents use pydantic-ai capabilities directly. The next step is evaluating whether AgentPool's custom team orchestration (`Team`, `TeamRun`) can leverage pydantic-ai's `pydantic_graph` for YAML-defined static workflows.

A critical architectural insight from review: `pydantic_graph.BaseNode` is a **passive execution step** (has `run(ctx)` returning a node or `End`), while AgentPool's `MessageNode` is an **active lifecycle object** (async context managers, signals, connections, MCP servers, events, storage). These are fundamentally different abstraction levels.

**The correct pattern is composition, not inheritance**: Create `AgentNode(BaseNode)` that wraps an agent's `process()` method, while keeping `MessageNode` independent. This preserves:
- Agent lifecycles independent of graph execution
- Dynamic `ConnectionManager` connections at runtime
- Session management per agent (not per graph node)
- Protocol server compatibility

## Goals / Non-Goals

**Goals:**
- Create `AgentNode(BaseNode)` wrapper for graph execution of AgentPool agents
- Reimplement YAML-defined `Team` parallel execution using `GraphBuilder` + `Fork` + `Join`
- Reimplement YAML-defined `TeamRun` sequential execution via `GraphBuilder` sequential chains
- Support conditional branching (`Decision` nodes) for YAML workflows
- Keep programmatic team construction (`agent & other`, `agent | other`) unchanged
- Keep `MessageNode`, `ConnectionManager`, `Talk` independent
- Use builder-based `GraphRun` API (not deprecated legacy)
- Disallow cycles in v1 with build-time detection
- Generate Mermaid diagrams for YAML-defined workflows

**Non-Goals:**
- Making `MessageNode` extend `BaseNode`
- Replacing `ConnectionManager` with graph edges
- Supporting cyclic workflows in v1
- Changing programmatic team construction behavior
- Modifying non-native agent types' internal implementation (only adding wrappers)

## Decisions

### Decision: AgentNode wrapper (composition over inheritance)
**Rationale**: `MessageNode` is an active lifecycle object with signals, connections, MCP servers, and storage. `BaseNode` is a passive execution step. Making `MessageNode` extend `BaseNode` would couple agent lifecycles to graph execution, breaking dynamic connections and independent agent existence.

**Approach**: Create `AgentNode[DepsT, OutputT](BaseNode)` that wraps an agent:
```python
@dataclass
class AgentNode(BaseNode[ChatMessage, GraphDeps, ChatMessage]):
    agent: MessageNode[ChatMessage, ChatMessage]
    session_pool: SessionPool
    
    async def run(self, ctx: GraphRunContext[ChatMessage, GraphDeps]) -> End[ChatMessage]:
        # Create child session for this node execution
        from agentpool.utils.identifiers import generate_session_id
        session_id = generate_session_id()
        await self.session_pool.create_session(
            session_id=session_id,
            agent_name=self.agent.name,
            parent_session_id=ctx.state.session_id,
        )
        # Run agent (using internal execution method, not the public run() which delegates to SessionPool)
        result = await self.agent._run_stream_once(ctx.state)
        return End(result)
```

Note: `GraphDeps` is a separate dataclass carrying graph-level state (session_id, prompt, deps), distinct from `AgentContext` which is per-tool context. `BaseNode.run()` must return `End[...]` (or another `BaseNode` for branching), not the raw output type.

**Migration path**: `AgentNode` is a new class. Existing `MessageNode` hierarchy is untouched.

### Decision: YAML teams use graph, programmatic teams unchanged
**Rationale**: Programmatic team construction (`agent & other`, `agent | other`) is used dynamically at runtime with `ConnectionManager` and `Talk`. Graph edges are static. Replacing programmatic construction would break dynamic connection capabilities.

**Approach**: 
- YAML `teams:` with `mode: parallel` → `GraphBuilder` + `Fork` + `Join`
- YAML `teams:` with `mode: sequential` → `GraphBuilder` sequential chain
- Programmatic `agent & other` → keeps `asyncio.gather()` + `Talk`
- Programmatic `agent | other` → keeps custom forwarding + `Talk`

**Migration path**: Backward-compat shim during deprecation period.

### Decision: ConnectionManager remains independent
**Rationale**: `ConnectionManager` supports runtime `add_connection()` with async filter conditions, transforms, and stop/exit conditions. Graph edges are statically typed and built at construction time. These models are incompatible.

**Approach**: 
- `ConnectionManager` / `Talk` remain for dynamic runtime connections
- Graph edges are used ONLY for static YAML team definitions
- `filter_condition` from YAML config is evaluated at graph build time (not runtime)

**Migration path**: No change to `ConnectionManager`.

### Decision: Builder-based GraphRun API
**Rationale**: pydantic_graph has a deprecated legacy `GraphRun` and a new builder-based `GraphRun`. Using the deprecated one creates technical debt.

**Approach**: Use `pydantic_graph.graph_builder.GraphBuilder` and `pydantic_graph.graph_builder.GraphRun` exclusively.

```python
builder = GraphBuilder()
builder.add(StartNode, agent_nodes[0])
for i in range(len(agent_nodes) - 1):
    builder.add(agent_nodes[i], agent_nodes[i + 1])
builder.add(agent_nodes[-1], End)
graph = builder.build()
```

**Migration path**: N/A — new code path.

### Decision: Disallow cycles in v1
**Rationale**: Graph loops create cyclic session trees, which break AgentPool's session hierarchy assumptions. The session tree is a tree (parent/child), not a graph. Loops would create infinite session creation or ambiguous parent relationships.

**Approach**: Build-time cycle detection rejects cyclic workflow definitions:
```python
def validate_no_cycles(graph: Graph) -> None:
    # Topological sort or DFS cycle detection
    ...
```

**Migration path**: Error at YAML config load time with clear message.

### Decision: SessionPool integrates at graph run level
**Rationale**: Each graph execution is a session. Node executions within the graph create child sessions.

**Approach**: `TurnRunner` wraps `GraphRun` execution. The graph run itself is a turn. Each `AgentNode.run()` creates a child session via `SessionPool`.

```python
async def run_graph_team(self, prompt, session_id):
    # GraphRun drives execution; AgentNode.run() is called automatically by the runner
    async with self.graph.run(start_node=agent_nodes[0], deps=GraphDeps(session_id=session_id, prompt=prompt)) as run:
        result = await run.get_output()
        return result
```

**Migration path**: New integration code.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| Graph execution overhead for simple parallel teams | Benchmark `asyncio.gather()` vs `Fork`/`Join`; keep `gather()` for programmatic teams |
| Heterogeneous agent type uniformity in Fork/Join | `AgentNode` wraps all outputs to `ChatMessage` union type |
| `pydantic_graph` API churn | Pin pydantic-ai version; wrap graph primitives in AgentPool types |
| Session tree complexity | Disallow cycles in v1; each graph run is a single session |
| Programmatic/YAML behavior divergence | Document clearly; tests for both paths |

## Migration Plan

1. **Phase 3a - AgentNode prototype**
   - Implement `AgentNode` wrapping native agent
   - Test with single-agent graph execution
   - Verify streaming, events, session creation
   - Benchmark vs direct agent execution

2. **Phase 3b - Parallel team graph (YAML only)**
   - Implement `ParallelTeamGraph` with `Fork` + `Join`
   - Migrate YAML parallel team tests
   - Add backward-compat shim for `Team` API

3. **Phase 3c - Sequential team graph (YAML only)**
   - Implement `SequentialTeamGraph` with node chaining
   - Migrate YAML sequential team tests
   - Add backward-compat shim for `TeamRun` API

4. **Phase 3d - Conditional workflows**
   - Add `Decision` node support for YAML workflows
   - Implement cycle detection
   - Add Mermaid diagram generation

5. **Phase 3e - Non-native agent adapters**
   - Implement `ClaudeCodeNode`, `ACPNode`, `AGUINode` wrappers
   - Test heterogeneous teams in graphs

6. **Phase 3f - Integration & stabilization**
   - End-to-end integration tests
   - Benchmark parallel graph vs `asyncio.gather()`
   - Documentation

Rollback: Revert to pre-change commit; old `Team`/`TeamRun` implementations remain.

## Open Questions

1. What performance overhead does `GraphBuilder` + `Fork`/`Join` have vs `asyncio.gather()` for simple 2-3 agent parallel teams?
2. Should programmatic team construction eventually migrate to graph-based too, or keep both paths indefinitely?
3. How does `filter_condition` (currently async runtime function) map to static graph edge predicates for YAML teams?
