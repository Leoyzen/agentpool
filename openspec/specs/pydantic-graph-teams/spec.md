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
- **THEN** all member agents execute concurrently via `Fork`/`Join` and results are collected as `list[ChatMessage]`

#### Scenario: Parallel team output aggregation
- **WHEN** a parallel team completes execution
- **THEN** the `Join` node aggregates all member outputs into a single `ChatMessage` containing combined content from all agents

## MODIFIED Requirements

### Requirement: Teams support parallel execution
**Existing spec**: `team-execution` capability requires parallel team execution.

#### Scenario: YAML teams use graph execution
- **WHEN** a parallel team is defined in YAML
- **THEN** it uses `pydantic_graph.Fork` + `Join` instead of `asyncio.gather()`

#### Scenario: Programmatic teams keep asyncio.gather
- **WHEN** a parallel team is created programmatically
- **THEN** it continues to use `asyncio.gather()`

#### Scenario: Parallel team with native agents
- **WHEN** a YAML parallel team has `members: [native_analyst, native_reviewer]` where both are `native` agents
- **THEN** `GraphBuilder` constructs a valid graph with `Fork` and `Join` nodes

#### Scenario: Parallel team with acp agents
- **WHEN** a YAML parallel team has `members: [acp_coder]` where the agent is `acp` type
- **THEN** `GraphBuilder` constructs a valid graph and the ACP agent executes via the ACP protocol

#### Scenario: Parallel team with mixed native and acp agents
- **WHEN** a YAML parallel team has `members: [native_analyzer, acp_reviewer]` with mixed types
- **THEN** both agents execute concurrently via `Fork`/`Join` regardless of type

#### Scenario: Parallel team config validation rejects removed agent types
- **WHEN** a YAML team config references a `claude`, `agui`, or `codex` agent in `members`
- **THEN** config validation fails with a clear error indicating the agent type is unsupported

## REMOVED Requirements

### Requirement: Teams support claude, agui, and codex agents as members
**Reason**: These agent types are removed from the framework. Team execution only needs to handle native and acp agents.
**Migration**: Update team YAML configs to only reference `native` or `acp` agents.
