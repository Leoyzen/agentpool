## ADDED Requirements

### Requirement: Cross-provider session lifecycle SHALL work without pool-level agents

Child session creation, parent ID propagation, and depth tracking SHALL function correctly when pool-level agent registration is removed. The `create_child_session()` flow SHALL use the `RuntimeAgentRegistry` to resolve agent configs, and depth/parent_id SHALL be propagated through `SessionState` independent of pool-level storage.

- `create_child_session()` SHALL resolve agent config via runtime registry or manifest (not pool-level cache)
- Child sessions SHALL have `parent_id` set to the parent session's ID
- Child sessions SHALL have `depth` set to `parent.depth + 1`
- Child session IDs SHALL be unique across all providers (use `uuid4()` prefix)
- `SpawnSessionStart` events SHALL include the correct `parent_session_id` and `depth`

#### Scenario: Subagent creates child session with correct parent_id
- **WHEN** a subagent tool creates a child session from a parent session
- **THEN** the child session's `parent_id` SHALL equal the parent session's ID
- **AND** the child session's `depth` SHALL equal `parent.depth + 1`
- **AND** the child session ID SHALL be unique (not reused from another provider)

#### Scenario: Multiple providers create child sessions
- **WHEN** child sessions are created from different providers (native, ACP)
- **THEN** all child session IDs SHALL be unique
- **AND** each child session SHALL have the correct `parent_id` from its respective parent
- **AND** depth SHALL increment correctly across provider boundaries

#### Scenario: SpawnSessionStart event includes lineage
- **WHEN** a child session is spawned
- **THEN** `SpawnSessionStart` event SHALL include `parent_session_id` matching the parent
- **AND** `SpawnSessionStart` event SHALL include `depth` matching the child's depth
- **AND** the event SHALL be published to EventBus before the child run starts
