# Spec: CancelScope Nesting

## ADDED Requirements

### Requirement: Subagent inherits parent CancelScope

The system SHALL enforce hierarchical CancelScope nesting when spawning subagents to ensure proper cancellation propagation.

#### Scenario: Parent agent spawns synchronous subagent

- **WHEN** a parent agent spawns a subagent using existing delegation API
- **THEN** the subagent's CancelScope SHALL be nested under the parent agent's active CancelScope
- **THEN** cancelling the parent agent SHALL automatically cancel the child subagent
- **THEN** all child subagent tasks SHALL complete cleanly without orphaned background processes

#### Scenario: Parent agent spawns parallel subagents

- **WHEN** a parent agent spawns multiple subagents in parallel
- **THEN** each subagent's CancelScope SHALL be independently nested under the parent's scope
- **THEN** cancelling the parent SHALL cancel all children simultaneously
- **THEN** no child shall continue executing after parent cancellation

#### Scenario: Deep subagent nesting

- **WHEN** a subagent spawns its own child (grandchild)
- **THEN** the grandchild's CancelScope SHALL be nested under both the direct parent and grandparent scopes
- **THEN** cancelling the grandparent SHALL propagate cancellation through the entire hierarchy
- **THEN** cleanup SHALL occur in reverse order of scope creation (grandchild first, then child, then parent)

#### Scenario: Shielded cleanup during subagent cancellation

- **WHEN** a parent agent is cancelled and needs to clean up resources
- **THEN** the cleanup code SHALL execute within a shielded CancelScope
- **THEN** child cancellation SHALL not interrupt parent cleanup operations
- **THEN** shielded cleanup SHALL complete even if children raise cancellation errors