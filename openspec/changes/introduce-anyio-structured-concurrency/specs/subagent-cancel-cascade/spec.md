## ADDED Requirements

### Requirement: Subagent runs use nested CancelScope
The system SHALL create a nested `anyio.CancelScope` for each subagent session that is a child of the parent agent's run scope. Cancelling the parent agent's run scope SHALL automatically cancel the subagent's scope, propagating cancellation to all tasks within the subagent run.

#### Scenario: Subagent scope is child of parent scope
- **WHEN** a parent agent spawns a subagent via `SessionController.spawn_session()`
- **THEN** the subagent session gets a `CancelScope` that is nested under the parent agent's run scope
- **AND** the parent scope has a cancel callback that cancels the child scope

#### Scenario: Parent cancellation cascades to subagent
- **WHEN** the parent agent's run scope is cancelled
- **THEN** the subagent's nested scope is automatically cancelled
- **AND** the subagent's iteration task receives `CancelledError`
- **AND** the subagent's tool executions are cancelled
- **AND** the subagent's consumer tasks are stopped

#### Scenario: Subagent cancellation does not affect parent
- **WHEN** a subagent's scope is cancelled (e.g., subagent run completes)
- **THEN** the parent agent's scope remains active
- **AND** the parent agent can continue execution

#### Scenario: Deeply nested subagents cascade correctly
- **WHEN** a parent agent spawns subagent A, which spawns subagent B
- **THEN** subagent B's scope is nested under subagent A's scope
- **AND** cancelling the parent agent's scope cascades to both A and B
- **AND** cancelling subagent A's scope cascades to B but not to the parent

### Requirement: Subagent CancelScope integrates with SessionLifecyclePolicy
The system SHALL use nested `CancelScope` to implement `SessionLifecyclePolicy.cascade` and `bound` for subagent sessions. The `independent` policy SHALL create the subagent scope without nesting under the parent scope.

#### Scenario: Cascade policy uses nested scope
- **WHEN** a session with `lifecycle_policy=cascade` spawns a child session
- **THEN** the child's `CancelScope` is nested under the parent's scope
- **AND** cancelling the parent scope cancels the child

#### Scenario: Bound policy uses nested scope
- **WHEN** a session with `lifecycle_policy=bound` spawns a child session
- **THEN** the child's `CancelScope` is nested under the parent's scope
- **AND** the child scope is cancelled immediately when the parent closes

#### Scenario: Independent policy does not nest scope
- **WHEN** a session with `lifecycle_policy=independent` spawns a child session
- **THEN** the child's `CancelScope` is NOT nested under the parent's scope
- **AND** cancelling the parent scope does NOT cancel the child

### Requirement: Subagent MCP connections inherit lifecycle
The system SHALL scope subagent MCP connections within the subagent's `CancelScope`, ensuring they are closed when the subagent run is cancelled or completes.

#### Scenario: Subagent MCP connections close on scope exit
- **WHEN** a subagent's scope is cancelled
- **THEN** the subagent's per-session MCP connections are closed
- **AND** pool-level MCP connections (shared across sessions) remain open
