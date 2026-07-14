## MODIFIED Requirements

### Requirement: ACP prompt processing SHALL use SessionPool exclusively

The ACP server SHALL route all prompt processing through `SessionPool.run_stream()`. There SHALL be no fallback path that calls `agent.run_stream()` directly. If `SessionPool` is unavailable, the system SHALL raise an error rather than silently falling back. With the proxy chain refactor, `SessionPool.run_stream()` SHALL create an `ACPTurn` (via `agent.create_turn()`) and execute it through `TurnRunner`, which is now functional for ACP agents thanks to `ACPClientAdapter`.

#### Scenario: SessionPool available with ACP agent
- **WHEN** `SessionPool.run_stream()` is called with an ACP agent
- **THEN** the system SHALL create an `ACPTurn` via `agent.create_turn()` 
- **AND** the `ACPTurn` SHALL use `ACPClientAdapter` to interface with the ACP subprocess
- **AND** SHALL execute the turn through `TurnRunner`
- **AND** SHALL NOT fall back to direct `agent.run_stream()` invocation

#### Scenario: SessionPool unavailable
- **WHEN** ACP prompt processing is called and `SessionPool` is NOT available
- **THEN** the system SHALL raise a clear error indicating that SessionPool is required for ACP prompt processing

### Requirement: Legacy acp_agent.prompt() dead code SHALL be removed

The dead code path in `acp_agent.py` that calls `session.process_prompt()` when `_protocol_handler.handle_prompt()` returns `None` SHALL be removed. `handle_prompt()` always returns a `PromptResponse`, making this path unreachable. Additionally, the `_stream_events()` inline bypass in `ACPAgent` SHALL be removed — all streaming SHALL go through `ACPTurn.execute()`.

#### Scenario: Prompt routing
- **WHEN** `acp_agent.prompt()` receives a prompt
- **THEN** it SHALL route exclusively through `_protocol_handler.handle_prompt()` and NOT fall through to the legacy `session.process_prompt()` path

#### Scenario: Streaming uses ACPTurn
- **WHEN** `ACPAgent.run_stream()` is called
- **THEN** it SHALL use `ACPTurn.execute()` for streaming
- **AND** SHALL NOT use `_stream_events()` inline bypass
- **AND** SHALL NOT use `poll_acp_events()` polling

### Requirement: ACPSessionManager SHALL separate lifecycle from protocol state

`ACPSessionManager._active` SHALL be renamed to `_acp_sessions: dict[str, ACPSession]`. Session lifecycle queries (existence, agent name, run status) SHALL be delegated to `SessionController.get_session()`. The `_acp_sessions` dict SHALL only store `ACPSession` runtime objects with protocol-specific state.

#### Scenario: Session lookup
- **WHEN** `ACPSessionManager.get_session(session_id)` is called
- **THEN** the system SHALL first check `SessionController.get_session(session_id)` for lifecycle state, then look up the `ACPSession` from `_acp_sessions` if the session is alive

#### Scenario: Pool swap cleanup
- **WHEN** a pool swap occurs
- **THEN** `_acp_sessions` SHALL be iterated for `ACPSession` cleanup, while lifecycle clearing SHALL be delegated to `SessionController`
