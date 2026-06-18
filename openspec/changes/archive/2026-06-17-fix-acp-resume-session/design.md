## Context

The ACP `session/resume` and `session/load` methods in `AgentPoolACPAgent` currently call `session_manager.create_session()` when a session is not already active in the in-memory `_active` dict. This is incorrect — `create_session()` creates a fresh `SessionData` and overwrites any existing stored session data, effectively discarding the previous session state.

The session manager already has a correct `resume_session()` method that loads from the store without overwriting, but it is not being called from `AgentPoolACPAgent`. Additionally, `session_manager.resume_session()` has `mcp_servers=None` hardcoded, preventing MCP server re-initialization on resume.

**Files involved:**
- `src/agentpool_server/acp_server/acp_agent.py` — `resume_session()` (L607–654) and `load_session()` (L480–529)
- `src/agentpool_server/acp_server/session_manager.py` — `resume_session()` (L183–244) and `create_session()` (L62–177)

## Goals / Non-Goals

**Goals:**
- `session/resume` correctly restores session state from storage without overwriting stored data
- `session/load` correctly restores session state from storage (same fix)
- MCP servers from the resume/load request are passed through to the restored session
- When session is not found in store, return an empty response (no silent session creation)
- Existing tests continue to pass; add regression test for the SessionData overwrite scenario

**Non-Goals:**
- Changing the wire protocol (response shapes remain identical)
- Refactoring the broader session lifecycle
- Adding session migration or cross-instance resume
- Fixing the `mcp_servers=None` issue for `load_session` (load session receives MCP servers from the request already)

## Decisions

### Decision 1: Route `resume_session()` and `load_session()` through `session_manager.resume_session()`

**Rationale**: `session_manager.resume_session()` already implements the correct logic — it loads `SessionData` from the store, validates the agent exists, creates the `ACPSession` wrapper, and calls `load_session()`. By routing through it, we avoid the `create_session()` side effect of overwriting stored data.

**Alternative considered**: Fix `create_session()` to check the store before creating. Rejected because `create_session()` is semantically for creating NEW sessions. Mixing resume logic into it would violate single responsibility.

### Decision 2: Pass MCP servers through `session_manager.resume_session()`

Add an `mcp_servers` parameter to `session_manager.resume_session()` (default `None` for backward compat with `handler.py` L276) and pass it to the `ACPSession` constructor instead of hardcoding `None`. Also add `await session.initialize_mcp_servers()` after `session.initialize()` — `ACPSession` separates construction from async MCP initialization (same pattern as `create_session()` L173-174). The caller (`AgentPoolACPAgent`) passes `params.mcp_servers` from the request.

**Alternative considered**: Initialize MCP servers separately after resume. Rejected because following `create_session()`'s pattern of calling both `initialize()` and `initialize_mcp_servers()` in sequence is simpler and keeps the resume flow self-contained.

### Decision 3: Return empty response when session not found

When `session_manager.resume_session()` returns `None` (session not in store), return an empty `ResumeSessionResponse()` / `LoadSessionResponse()`. Do NOT fall back to `create_session()`.

**Rationale**: The ACP spec says `session/resume` resumes an *existing* session. If the session doesn't exist, the correct behavior is to return an empty response (indicating no state to restore) rather than silently creating a new session. This matches the existing error-handling pattern in both methods.

### Decision 4: Keep `get_session()` check for active sessions

The existing pattern of checking `get_session()` first (for already-active sessions) is correct and should be preserved. The fix only changes the fallback path.

## Risks / Trade-offs

- **Risk**: `session_manager.resume_session()` doesn't initialize MCP servers → **Mitigation**: Pass `mcp_servers` from the request through to `ACPSession`, matching the behavior of `create_session()`.
- **Risk**: Existing tests may rely on `create_session()` being called → **Mitigation**: Review and update tests in `test_acp_resume.py`, `test_acp_session_resume.py`, and `test_rpc.py`.
- **Risk**: `session_manager.resume_session()` loads agent from `self._pool.all_agents[data.agent_name]` which may differ from `self.default_agent` → **Mitigation**: This is correct behavior — the stored agent name is the source of truth. If the agent has been renamed or removed, returning `None` is appropriate.
