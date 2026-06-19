## MODIFIED Requirements

### Requirement: Protocol handlers SHALL NOT conditionally bypass SessionPool
The system SHALL NOT use feature flags, canary flags, or conditional logic in protocol handlers to bypass SessionPool and route execution to legacy non-session-pool paths. When SessionPool is active, all protocol-level prompt processing MUST route through `SessionPool.receive_request()` or equivalent SessionPool APIs. The `OpenCodeConfig` feature flags (`use_session_pool_for_commands`, `use_session_pool_for_skills`, `use_session_pool_for_init`, `use_session_pool_for_summarize`, `use_session_pool_for_mcp`, `use_session_pool_for_messages`, `use_session_pool_for_status`) SHALL be removed from the codebase.

#### Scenario: OpenCode command execution without category flags
- **WHEN** an OpenCode client executes a command, skill, init, summarize, or MCP operation
- **THEN** the OpenCode protocol handler invokes `SessionPool.receive_request()` for the operation
- **AND** the handler does NOT check per-category feature flags to decide whether to use SessionPool
- **AND** the handler does NOT fall back to direct agent invocation or other legacy paths
- **AND** no `OpenCodeConfig` feature flags exist to gate SessionPool usage

#### Scenario: OpenCode message storage without flag
- **WHEN** the OpenCode server reads message history for a session
- **THEN** it uses SessionPool's message API (`get_messages`) exclusively
- **AND** it does NOT fall back to `state.messages` dict as an authoritative source
- **AND** no `use_session_pool_for_messages` flag exists to gate this behavior

#### Scenario: OpenCode session status without flag
- **WHEN** the OpenCode server reads or writes session status
- **THEN** it uses `OpenCodeSessionPoolIntegration.get_session_status()` and `SessionStatusBridge` exclusively
- **AND** it does NOT fall back to `getattr(state, "session_status", None)` dynamic attribute
- **AND** no `use_session_pool_for_status` flag exists to gate this behavior

#### Scenario: Summarize preserves compaction pipeline
- **WHEN** a summarize operation is routed through `SessionPool.receive_request()`
- **THEN** after SessionPool-based summarization completes, the compaction pipeline (`compact_conversation()` + `set_messages_for_session()`) runs to reset the UI message list
- **AND** the compaction pipeline is NOT lost when the feature flag is removed

## REMOVED Requirements

### Requirement: OpenCodeConfig per-category feature flags
**Reason**: The feature flags (`use_session_pool_for_commands`, `use_session_pool_for_skills`, `use_session_pool_for_init`, `use_session_pool_for_summarize`, `use_session_pool_for_mcp`, `use_session_pool_for_messages`, `use_session_pool_for_status`) were migration safety nets for the SessionPool transition. The SessionPool path is now stable and in production. These flags create maintenance burden and confusion about which path is authoritative.
**Migration**: Remove all `use_session_pool_for_*` fields from `OpenCodeConfig`. Remove `should_use_session_pool_for()` method. Remove all conditional branching in `session_pool_integration.py` and `session_routes.py` that checks these flags. Route all paths through SessionPool unconditionally. Preserve the summarize compaction pipeline in the always-executed path.
