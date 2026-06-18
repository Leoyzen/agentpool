## Why

`AgentPoolACPAgent.resume_session()` (and `load_session()`) call `session_manager.create_session()` instead of `session_manager.resume_session()` when a session is not already active in memory. This causes fresh `SessionData` to overwrite the stored session data, breaking session state restoration and making ACP `session/resume` effectively behave as `session/new`.

## What Changes

- **Fix `AgentPoolACPAgent.resume_session()`**: Use `session_manager.resume_session()` to restore session state from storage instead of `create_session()` which overwrites it.
- **Fix `AgentPoolACPAgent.load_session()`**: Same fix — use `session_manager.resume_session()` for the session lookup path.
- **Pass MCP servers through resume**: `session_manager.resume_session()` currently hardcodes `mcp_servers=None`. It must accept and pass through the MCP server list from the resume/load request.
- **Fallback behavior**: When `session_manager.resume_session()` returns `None` (session not found in store), return an empty response instead of silently creating a new session. This matches the ACP spec expectation that `session/resume` resumes an *existing* session.

## Capabilities

### New Capabilities
- `acp-session-resume`: Correctly restore ACP session state from persistent storage when `session/resume` or `session/load` is called, without overwriting stored session data.

### Modified Capabilities
<!-- None — this is a bug fix, not a requirement change -->

## Impact

- **Affected code**: `src/agentpool_server/acp_server/acp_agent.py` (lines 607–654, 480–529), `src/agentpool_server/acp_server/session_manager.py` (lines 183–244)
- **Affected tests**: `tests/servers/acp_server/test_acp_resume.py`, `tests/servers/acp_server/test_acp_session_resume.py`, `tests/servers/acp_server/test_rpc.py`
- **Protocol impact**: ACP `session/resume` and `session/load` methods will correctly restore session state
- **No breaking changes**: The response shape and protocol wire format remain identical
