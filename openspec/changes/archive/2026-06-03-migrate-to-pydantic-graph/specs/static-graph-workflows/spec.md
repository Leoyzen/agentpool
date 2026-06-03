## ADDED Requirements

### Requirement: YAML workflows support conditional branching via Decision
AgentPool SHALL support conditional branching in YAML-defined workflows via `pydantic_graph.Decision` nodes.

#### Scenario: Conditional routing in YAML workflow
- **WHEN** a YAML workflow includes a `decision` step
- **THEN** `Decision` node evaluates the condition and routes to the appropriate subsequent node

### Requirement: Cycles are disallowed in v1
AgentPool SHALL reject cyclic YAML workflow definitions at build time.

#### Scenario: Cycle detection at build time
- **WHEN** a YAML workflow definition contains a cycle
- **THEN** graph construction fails with a clear error message indicating the cycle

#### Scenario: Acyclic workflows accepted
- **WHEN** a YAML workflow definition is acyclic
- **THEN** graph construction succeeds

### Requirement: ConnectionManager remains independent
AgentPool SHALL keep `ConnectionManager` and `Talk` independent of graph execution for dynamic runtime connections.

#### Scenario: Dynamic connections unchanged
- **WHEN** `create_connection()` is called at runtime
- **THEN** `ConnectionManager` handles it as before, independent of any graph execution

#### Scenario: Static YAML connections use graph edges
- **WHEN** a team is defined in YAML with member connections
- **THEN** those connections are represented as graph edges
