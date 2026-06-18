## ADDED Requirements

### Requirement: ProviderCurrentConfig SHALL include headers attribute
The `ProviderCurrentConfig` model SHALL provide a `headers` field, or `provider_router.py` SHALL safely handle its absence.

#### Scenario: Router reads config headers
- **WHEN** `ProviderRouter.get_provider()` is called after a `set_provider_override()`
- **THEN** it SHALL NOT raise `AttributeError: 'ProviderCurrentConfig' object has no attribute 'headers'`

### Requirement: FakeManifest SHALL expose acp attribute
The `FakeManifest` test mock SHALL have an `acp` attribute (can be `None`) to avoid `AttributeError` during agent creation.

#### Scenario: ACP agent created from FakeManifest
- **WHEN** an ACP agent is created with a manifest that uses a mock
- **THEN** `manifest.acp` SHALL be accessible without error

### Requirement: Notification tests SHALL skip when apprise is not installed
`apprise` is an optional dependency. Tests in `test_notifications.py` SHALL use `@pytest.mark.skipif` when `apprise` is not importable.

#### Scenario: apprise module missing
- **WHEN** `pytest` collects tests with `apprise` not installed
- **THEN** all `test_notifications.py` tests SHALL be skipped with a clear reason

### Requirement: Skills prefix `skill:` SHALL NOT be enforced in tests
Tests that assert the `skill:` prefix on command names SHALL be removed.

#### Scenario: Skills registered without prefix
- **WHEN** a skill is registered as `test-skill` (without `skill:` prefix)
- **THEN** the test SHALL NOT assert `skill:test-skill` — the bare name is correct

### Requirement: SSE GlobalEvent envelope SHALL include directory for all events
`_serialize_event()` SHALL produce envelope fields (`directory`, `project`, `payload`) even for server-level events (heartbeat, connected) that lack a session context.

#### Scenario: Server heartbeat serialized to global event
- **WHEN** a `ServerHeartbeatEvent` is serialized with `wrap_payload=True`
- **THEN** the output SHALL contain `directory` and `project` keys

#### Scenario: PartDeltaEvent maintains sessionId in payload
- **WHEN** a `PartDeltaEvent` is serialized with `wrap_payload=True`
- **THEN** the `sessionId` SHALL appear inside the `payload` object

### Requirement: RunStartedEvent SHALL propagate parent_session_id
When `parent_session_id` is passed to `run_stream()`, the emitted `RunStartedEvent` SHALL contain that value in its `parent_session_id` field.

#### Scenario: Child session tracks parent
- **WHEN** `child.run_stream("hello", parent_session_id="parent-123")` is called
- **THEN** the `RunStartedEvent` in the stream SHALL have `parent_session_id == "parent-123"`

### Requirement: EventProcessor SHALL expose _child_contexts or equivalent
The `EventProcessor` SHALL provide the attribute accessed by tests (whether named `_child_contexts` or a new name).

#### Scenario: EventProcessor stores child context
- **WHEN** a subagent event is processed
- **THEN** `EventProcessor._child_contexts` (or the renamed attribute) SHALL contain the child context

### Requirement: AcpMcpConnection SHALL provide register_pending_request (or equivalent)
The `AcpMcpConnection` class SHALL expose the method used to register pending requests, whether named `register_pending_request` or a new name.

#### Scenario: Pending request registered
- **WHEN** an ACP MCP request is initiated
- **THEN** `AcpMcpConnection` SHALL support registering pending requests for response matching

### Requirement: MCP provider SHALL export or document UPath location
Test files SHALL import `UPath` from a source that still exports it.

#### Scenario: Tests import UPath
- **WHEN** a test imports `UPath` for MCP provider tests
- **THEN** the import SHALL resolve without `AttributeError`

### Requirement: skills_config tests SHALL match current ConfigPath behavior
Tests for the deprecated `get_effective_paths()` SHALL be updated to match the new `ConfigPath` resolution behavior, or the tests SHALL be removed.

#### Scenario: Effective paths without config file
- **WHEN** `SkillsConfig.get_effective_paths()` is called with no config file path
- **THEN** paths SHALL be resolved according to current `ConfigPath` behavior (not necessarily absolute)

### Requirement: Exception message patterns in tests SHALL match actual runtime messages
Tests using `pytest.raises(RuntimeError, match=...)` SHALL use patterns that match the actual error message raised by the code.

#### Scenario: Hook test expects "Run blocked" pattern
- **WHEN** a hook denies a run
- **THEN** the error message SHALL match the pattern in the test assertion
