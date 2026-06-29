## ADDED Requirements

### Requirement: SessionLifecyclePolicy controls child session behavior
The system SHALL support a configurable `SessionLifecyclePolicy` per session that determines how child sessions behave when the parent session closes or reaches TTL.

#### Scenario: Independent policy allows child to outlive parent
- **GIVEN** session `s1` has `lifecycle_policy=independent`
- **AND** `s1` has a child session `s1.1`
- **WHEN** `s1` is closed due to TTL expiration
- **THEN** `s1.1` remains active
- **AND** `s1.1` continues to process events and turns

#### Scenario: Cascade policy closes children with parent
- **GIVEN** session `s1` has `lifecycle_policy=cascade`
- **AND** `s1` has child sessions `s1.1` and `s1.2`
- **WHEN** `session_pool.close_session("s1")` is called
- **THEN** `s1.1` and `s1.2` are closed in reverse creation order
- **AND** `s1` is closed after all children are cleaned up

#### Scenario: Bound policy ties child lifetime to parent
- **GIVEN** session `s1` has `lifecycle_policy=bound`
- **AND** `s1` has a child session `s1.1`
- **WHEN** `s1` is closed
- **THEN** `s1.1` is closed immediately without waiting for TTL
- **AND** `s1.1` does not have an independent TTL timer

### Requirement: Default lifecycle policy is cascade
The system SHALL use `cascade` as the default `SessionLifecyclePolicy` when none is specified.

#### Scenario: Unspecified policy defaults to cascade
- **WHEN** `session_pool.create_session("s1")` is called without a `lifecycle_policy`
- **THEN** the created session has `lifecycle_policy=cascade`
- **AND** closing `s1` will close all its children

### Requirement: Child sessions inherit parent policy by default
The system SHALL propagate the parent's `lifecycle_policy` to child sessions unless explicitly overridden.

#### Scenario: Child inherits parent policy
- **GIVEN** session `s1` has `lifecycle_policy=independent`
- **WHEN** `session_pool.create_session(parent_session_id="s1")` is called without specifying `lifecycle_policy`
- **THEN** the child session has `lifecycle_policy=independent`

#### Scenario: Child overrides parent policy
- **GIVEN** session `s1` has `lifecycle_policy=cascade`
- **WHEN** `session_pool.create_session(parent_session_id="s1", lifecycle_policy="independent")` is called
- **THEN** the child session has `lifecycle_policy=independent`
- **AND** closing `s1` does NOT close this child

### Requirement: Lifecycle policy affects cleanup task behavior
The system SHALL respect `lifecycle_policy` during automatic TTL-based cleanup.

#### Scenario: Cleanup task respects cascade policy
- **GIVEN** session `s1` has `lifecycle_policy=cascade` and has exceeded TTL
- **AND** `s1` has child `s1.1`
- **WHEN** the cleanup task runs
- **THEN** `s1.1` is closed before `s1` is removed

#### Scenario: Cleanup task respects independent policy
- **GIVEN** session `s1` has `lifecycle_policy=independent` and has exceeded TTL
- **AND** `s1` has child `s1.1`
- **WHEN** the cleanup task runs
- **THEN** `s1` is removed
- **AND** `s1.1` remains active with its own TTL
## ADDED Requirements

### Requirement: Lifecycle policy enforcement via CancelScope nesting
The system SHALL use nested `anyio.CancelScope` to enforce `SessionLifecyclePolicy.cascade` and `bound`. Child sessions with these policies SHALL have their `CancelScope` nested under the parent session's scope. The `independent` policy SHALL create child scopes without nesting.

#### Scenario: Cascade policy creates nested CancelScope
- **GIVEN** session `s1` has `lifecycle_policy=cascade`
- **WHEN** `s1` spawns a child session `s1.1`
- **THEN** `s1.1`'s `CancelScope` is nested under `s1`'s scope
- **AND** cancelling `s1`'s scope automatically cancels `s1.1`'s scope

#### Scenario: Bound policy creates nested CancelScope
- **GIVEN** session `s1` has `lifecycle_policy=bound`
- **WHEN** `s1` spawns a child session `s1.1`
- **THEN** `s1.1`'s `CancelScope` is nested under `s1`'s scope
- **AND** `s1.1`'s scope is cancelled when `s1` closes

#### Scenario: Independent policy creates independent CancelScope
- **GIVEN** session `s1` has `lifecycle_policy=independent`
- **WHEN** `s1` spawns a child session `s1.1`
- **THEN** `s1.1`'s `CancelScope` is NOT nested under `s1`'s scope
- **AND** cancelling `s1`'s scope does NOT cancel `s1.1`'s scope
