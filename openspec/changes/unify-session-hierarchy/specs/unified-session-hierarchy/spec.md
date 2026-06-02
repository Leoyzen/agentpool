## ADDED Requirements

### Requirement: Session hierarchy managed by SessionController only
The system SHALL maintain a single in-memory source of truth for session parent-child relationships within `SessionController._children`.

#### Scenario: Child session creation updates hierarchy
- **WHEN** `SessionController._get_or_create_session_locked()` is called with `parent_session_id`
- **THEN** the child `session_id` is added to `self._children[parent_session_id]`

#### Scenario: Child session removal updates hierarchy
- **WHEN** `SessionController._close_session_unlocked()` is called for a session that has children
- **THEN** all child sessions are also closed (respecting lifecycle_policy="independent")
- **AND** the session is removed from its parent's `_children` list

### Requirement: EventBus routes events using SessionController hierarchy
The system SHALL ensure that `EventBus.publish()` with `scope="descendants"` delivers events to parent subscribers when the publisher is a child session.

#### Scenario: Subagent events reach parent subscriber
- **WHEN** a subscriber calls `event_bus.subscribe("parent-sid", scope="descendants")`
- **AND** an event is published with `session_id="child-sid"` where `child-sid` is a child of `parent-sid`
- **THEN** the subscriber queue receives the event

#### Scenario: Non-descendant events are not delivered
- **WHEN** a subscriber calls `event_bus.subscribe("parent-sid", scope="descendants")`
- **AND** an event is published with `session_id="unrelated-sid"`
- **THEN** the subscriber queue does NOT receive the event

### Requirement: SessionPool is the sole session lifecycle entry
The system SHALL ensure that `SessionPool` is the only component responsible for creating and managing sessions when enabled.

#### Scenario: AgentContext creates child session through SessionPool
- **WHEN** `AgentContext.create_child_session()` is called with `pool.session_pool` available
- **THEN** it calls `pool.session_pool.create_session()` directly
- **AND** it does NOT fall back to `pool.sessions.create_child_session()`

#### Scenario: No duplicate persistence on child creation
- **WHEN** `SessionPool.create_session()` is called with `parent_session_id`
- **THEN** it persists the relationship via `SessionController` (which writes to `SessionStore`)
- **AND** it does NOT call `pool.sessions.create_child_session()`

### Requirement: Child session inherits parent project_id and cwd
The system SHALL ensure that when a child session is created via `SessionPool.create_session()` with a `parent_session_id`, the child inherits the parent's `project_id` and `cwd` fields from `SessionData`.

#### Scenario: Child session inherits workspace context
- **WHEN** `SessionPool.create_session()` is called with `parent_session_id="parent-sid"`
- **AND** the parent's `SessionData` has `project_id="proj-1"` and `cwd="/workspace"`
- **THEN** the child's `SessionData` has `project_id="proj-1"` and `cwd="/workspace"`

#### Scenario: Root session has no parent to inherit from
- **WHEN** `SessionPool.create_session()` is called without `parent_session_id`
- **THEN** the session's `project_id` and `cwd` are determined by other means (e.g., from config or defaults)
- **AND** no parent lookup is performed

### Requirement: AgentPool.sessions provides backward-compatible alias
The system SHALL ensure `AgentPool.sessions` returns `AgentPool.session_pool` as a property alias, preserving backward compatibility for code that checks `pool.sessions is None`.

#### Scenario: Code checking pool.sessions works without changes
- **WHEN** code accesses `agent_pool.sessions`
- **THEN** it receives the same object as `agent_pool.session_pool`
- **AND** checks like `pool.sessions is None` behave identically to `pool.session_pool is None`

#### Scenario: Code accessing pool.sessions.store still works
- **WHEN** code accesses `pool.sessions.store` (e.g., `ACPSessionManager.session_store`)
- **AND** `session_pool` is enabled
- **THEN** it accesses `pool.session_pool.sessions.store` through the alias chain
