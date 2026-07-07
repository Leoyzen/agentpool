## ADDED Requirements

### Requirement: WebSocket disconnect SHALL trigger session lifecycle cleanup via SessionController

When an ACP WebSocket connection drops, the system SHALL trigger full session lifecycle cleanup for all sessions associated with that connection. The `on_disconnect` callback SHALL be invoked in the `ConnectionClosed` handler, which delegates to `ACPSessionManager.close_all_sessions_for_connection()`. Each session SHALL be closed through `SessionController.close_session()` (which handles RunHandle lifecycle with timeout + cancel) to ensure active runs are safely cancelled before MCP resource cleanup.

#### Scenario: WebSocket disconnect triggers session close
- **WHEN** `_handle_websocket_client()` in `acp/transports.py` catches `ConnectionClosed`
- **THEN** the `on_disconnect` callback is called with the `AgentSideConnection`
- **AND** `ACPSessionManager.close_all_sessions_for_connection()` is invoked
- **AND** each session is closed via `SessionController.close_session()` (which handles RunHandle lifecycle and calls `cleanup_session(session_id)`)
- **AND** MCP resources (toolsets, transports, ACP connections) are cleaned up for each session

#### Scenario: WebSocket disconnect during active run
- **WHEN** WebSocket disconnects while a session has an active run
- **THEN** `SessionController.close_session()` cancels the RunHandle with timeout before MCP cleanup
- **AND** no MCP toolsets are closed mid-tool-call
