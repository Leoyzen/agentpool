## ADDED Requirements

### Requirement: YAML parallel teams use GraphBuilder Fork and Join
AgentPool SHALL implement YAML-defined parallel team execution using `pydantic_graph.GraphBuilder` with `Fork` branching to member agents and `Join` collecting results.

#### Scenario: YAML parallel team graph construction
- **WHEN** a YAML team config has `mode: parallel`
- **THEN** `GraphBuilder` constructs a graph with `Fork` branching to all member `AgentNode`s, followed by `Join`

#### Scenario: Programmatic parallel teams unchanged
- **WHEN** a team is created programmatically via `agent & other`
- **THEN** it continues to use `asyncio.gather()` and `Talk`, not graph execution

#### Scenario: Parallel execution result collection
- **WHEN** a YAML parallel team runs
- **THEN** all member agents execute concurrently via `Fork`/`Join` and results are collected

### Requirement: YAML sequential teams use GraphBuilder node chains
AgentPool SHALL implement YAML-defined sequential team execution using `GraphBuilder` sequential node chains.

#### Scenario: YAML sequential team graph construction
- **WHEN** a YAML team config has `mode: sequential`
- **THEN** `GraphBuilder` constructs a chain where each `AgentNode`'s output feeds the next

#### Scenario: Programmatic sequential teams unchanged
- **WHEN** a team is created programmatically via `agent | other`
- **THEN** it continues to use custom forwarding and `Talk`, not graph execution

## MODIFIED Requirements

### Requirement: Teams support parallel execution
**Existing spec**: `team-execution` capability requires parallel team execution.

#### Scenario: YAML teams use graph execution
- **WHEN** a parallel team is defined in YAML
- **THEN** it uses `pydantic_graph.Fork` + `Join` instead of `asyncio.gather()`

#### Scenario: Programmatic teams keep asyncio.gather
- **WHEN** a parallel team is created programmatically
- **THEN** it continues to use `asyncio.gather()`
