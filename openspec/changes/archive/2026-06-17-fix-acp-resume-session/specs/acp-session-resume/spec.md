## ADDED Requirements

### Requirement: Session resume restores state from storage

The ACP agent's `session/resume` handler SHALL restore session state from persistent storage without overwriting existing session data.

#### Scenario: Resume existing session from store
- **WHEN** a client sends `session/resume` with a `sessionId` that exists in the session store
- **AND** the session is not currently active in memory
- **THEN** the agent SHALL load `SessionData` from the store
- **AND** the agent SHALL create an `ACPSession` wrapper with the restored data
- **AND** the agent SHALL call `load_session()` to restore conversation history
- **AND** the stored `SessionData` SHALL NOT be overwritten with fresh data

#### Scenario: Resume non-existent session
- **WHEN** a client sends `session/resume` with a `sessionId` that does NOT exist in the session store
- **THEN** the agent SHALL return an empty `ResumeSessionResponse`
- **AND** the agent SHALL NOT create a new session

#### Scenario: Resume already-active session
- **WHEN** a client sends `session/resume` with a `sessionId` that is already active in memory
- **THEN** the agent SHALL reuse the existing active session without reloading

### Requirement: Session load restores state from storage

The ACP agent's `session/load` handler SHALL restore session state from persistent storage without overwriting existing session data.

#### Scenario: Load existing session from store
- **WHEN** a client sends `session/load` with a `sessionId` that exists in the session store
- **AND** the session is not currently active in memory
- **THEN** the agent SHALL load `SessionData` from the store
- **AND** the agent SHALL create an `ACPSession` wrapper with the restored data
- **AND** the agent SHALL call `load_session()` to restore conversation history
- **AND** the stored `SessionData` SHALL NOT be overwritten with fresh data
- **AND** the agent SHALL replay conversation history to the client via `session/update` notifications

### Requirement: MCP servers are re-initialized on resume

When resuming a session, MCP servers provided in the request SHALL be passed through to the `ACPSession` constructor.

#### Scenario: Resume with MCP servers
- **WHEN** a client sends `session/resume` with `mcpServers` in the request
- **THEN** the restored `ACPSession` SHALL receive those MCP server configurations
- **AND** MCP connections SHALL be initialized for the restored session

### Requirement: Session load restores state without overwriting stored data

The ACP agent's `session/load` handler SHALL use the same restore-from-storage path as `session/resume`, without overwriting stored `SessionData`.

#### Scenario: Load existing session preserves stored data
- **WHEN** a client sends `session/load` with a `sessionId` that exists in the session store
- **AND** the session is not currently active in memory
- **THEN** the agent SHALL call `session_manager.resume_session()` (not `create_session()`)
- **AND** the stored `SessionData` SHALL NOT be overwritten with fresh data

### Requirement: Regression test coverage prevents recurrence

The fix SHALL include integration-level tests that catch the exact class of bug (calling `create_session` when `resume_session` should be used).

#### Scenario: Integration test verifies SessionData integrity on resume
- **WHEN** a session is created with known conversation history
- **AND** the session is persisted to the store
- **AND** the session is subsequently resumed via `session/resume`
- **THEN** the `SessionData` in the store SHALL retain its original `created_at` timestamp
- **AND** the `SessionData` SHALL retain its original `status` field
- **AND** the conversation history SHALL be restored from storage

#### Scenario: Integration test verifies SessionData integrity on load
- **WHEN** a session is created with known conversation history
- **AND** the session is persisted to the store
- **AND** the session is subsequently loaded via `session/load`
- **THEN** the `SessionData` in the store SHALL retain its original fields
- **AND** the conversation history SHALL be restored from storage

#### Scenario: Unit test verifies resume_session() never calls create_session()
- **WHEN** `AgentPoolACPAgent.resume_session()` processes any valid request
- **THEN** `session_manager.create_session()` SHALL NOT be called in any code path
- **AND** `session_manager.resume_session()` SHALL be called when the session is not active

#### Scenario: Unit test verifies load_session() never calls create_session()
- **WHEN** `AgentPoolACPAgent.load_session()` processes any valid request
- **THEN** `session_manager.create_session()` SHALL NOT be called in any code path
- **AND** `session_manager.resume_session()` SHALL be called when the session is not active
