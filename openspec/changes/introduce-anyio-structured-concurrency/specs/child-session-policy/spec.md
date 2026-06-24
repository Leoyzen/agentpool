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
