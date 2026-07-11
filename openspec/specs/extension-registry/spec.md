## ADDED Requirements

### Requirement: ExtensionRegistry SHALL provide 4-level scope storage

The `ExtensionRegistry` SHALL maintain capability storage at four scope levels: pool, session, agent, and turn. Pool-level capabilities SHALL be visible to all sessions. Session-level capabilities SHALL be visible only within their session. Agent-level capabilities SHALL be visible only to the named agent. Turn-level capabilities SHALL be visible only for the duration of one turn.

#### Scenario: Pool-level capability visible to all sessions
- **WHEN** a capability is registered at `ScopeLevel.POOL`
- **THEN** `get_visible_capabilities(scope)` SHALL return it for any scope, regardless of session_id, agent_name, or turn_id

#### Scenario: Session-level capability isolated per session
- **WHEN** a capability is registered at `ScopeLevel.SESSION` with `scope_id="ses_1"`
- **THEN** `get_visible_capabilities(scope)` with `scope.session_id="ses_1"` SHALL return it
- **AND** `get_visible_capabilities(scope)` with `scope.session_id="ses_2"` SHALL NOT return it

#### Scenario: Turn-level capability cleaned up after turn
- **WHEN** a capability is registered at `ScopeLevel.TURN` with `scope_id="turn_1"`
- **AND** the turn completes
- **THEN** subsequent calls to `get_visible_capabilities(scope)` SHALL NOT return it

### Requirement: ExtensionRegistry SHALL query capabilities by Resource Protocol type

The `ExtensionRegistry` SHALL provide typed query methods that filter visible capabilities by `isinstance()` checks against Resource Protocol interfaces. `get_skill_resources(scope)` SHALL return capabilities implementing `SkillResource`. `get_mcp_resources(scope)` SHALL return capabilities implementing `McpResource`. `get_command_resources(scope)` SHALL return capabilities implementing `CommandResource`. `get_observable_capabilities(scope)` SHALL return capabilities implementing `ChangeObservable`.

#### Scenario: Query skills from mixed capabilities
- **WHEN** the registry contains a `SkillManagerCap` (implements `SkillResource`) and a `McpServerCap` (implements `McpResource`)
- **AND** `get_skill_resources(scope)` is called
- **THEN** only `SkillManagerCap` SHALL be returned

#### Scenario: Query commands from multiple sources
- **WHEN** the registry contains a `SkillManagerCap` and a `McpServerCap`, both implementing `CommandResource`
- **AND** `get_command_resources(scope)` is called
- **THEN** both SHALL be returned

### Requirement: ExtensionRegistry SHALL resolve URIs by scheme routing

The `ExtensionRegistry` SHALL provide `resolve_uri(uri: str, scope: Scope) -> str | bytes | None` that routes by URI scheme. `skill://` URIs SHALL be resolved by querying `get_skill_resources(scope)` and calling `read_skill(uri)`. `mcp://` URIs SHALL be resolved by querying `get_mcp_resources(scope)` and calling `read_resource(uri)`. Unknown schemes SHALL return `None`.

#### Scenario: Resolve skill URI
- **WHEN** `resolve_uri("skill://ponytail/SKILL.md", scope)` is called
- **AND** a `SkillManagerCap` is visible at the given scope
- **THEN** `SkillManagerCap.read_skill("skill://ponytail/SKILL.md")` SHALL be called
- **AND** the skill content SHALL be returned

#### Scenario: Resolve unknown URI scheme
- **WHEN** `resolve_uri("unknown://foo", scope)` is called
- **THEN** `None` SHALL be returned

### Requirement: ExtensionRegistry SHALL merge change streams from observable capabilities

The `ExtensionRegistry` SHALL provide `merge_change_streams(scope) -> AsyncIterator[ChangeEvent] | None` that merges `on_change()` streams from all visible `ChangeObservable` capabilities. The merge SHALL use a sentinel-based pattern: each source stream is consumed in a separate task; when a source completes (returns `None` or raises), a sentinel is pushed; the merge completes when all sources have sent sentinels. Exceptions in individual streams SHALL be logged via `logger.warning()` and not propagated.

#### Scenario: Merge two change streams
- **WHEN** two observable capabilities each have active `on_change()` streams
- **AND** `merge_change_streams(scope)` is called
- **THEN** events from both streams SHALL be yielded as they arrive
- **AND** when one stream completes, the other continues
- **AND** when both streams complete, the merge completes

#### Scenario: Exception in one stream does not kill the merge
- **WHEN** one observable's `on_change()` raises an exception
- **THEN** the exception SHALL be logged via `logger.warning()`
- **AND** the other streams SHALL continue yielding events
- **AND** the merge SHALL not complete until all streams have completed

#### Scenario: No observable capabilities
- **WHEN** no visible capabilities implement `ChangeObservable`
- **AND** `merge_change_streams(scope)` is called
- **THEN** `None` SHALL be returned

### Requirement: ExtensionRegistry SHALL support concurrent turn-level registration

Turn-level capability registration and unregistration SHALL be guarded by an `asyncio.Lock` to prevent concurrent modification. Pool, session, and agent-level dicts SHALL NOT require locking (mutated only at startup/shutdown).

#### Scenario: Concurrent turn registration
- **WHEN** two capabilities are registered at `ScopeLevel.TURN` concurrently for the same turn
- **THEN** both registrations SHALL succeed
- **AND** no `KeyError` or data corruption SHALL occur

### Requirement: Cycle detection SHALL occur at registration time

When a capability is added as a child of another capability (`add_child()`), the registry SHALL check the ancestor chain for cycles. If the capability already appears in its own ancestor chain, a `CircularCompositionError` SHALL be raised at registration time, not at query time.

#### Scenario: Circular composition detected
- **WHEN** capability A has child B, and B attempts to add A as a child
- **THEN** a `CircularCompositionError` SHALL be raised immediately

### Requirement: Composition depth SHALL be limited

The maximum composition depth (parent → child → grandchild → ...) SHALL be configurable via `extensions.max_composition_depth` (default: 3, root-inclusive). When depth exceeds the limit, a warning SHALL be logged but registration SHALL NOT be blocked.

#### Scenario: Depth limit warning
- **WHEN** a child is added at depth 4 (root → child → grandchild → great-grandchild)
- **AND** `extensions.max_composition_depth` is 3
- **THEN** a warning SHALL be logged
- **AND** the registration SHALL succeed
