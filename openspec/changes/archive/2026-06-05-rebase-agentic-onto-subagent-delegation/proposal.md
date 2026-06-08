## Why

The `develop/agentic` branch introduces a major architectural evolution: unified session orchestration via `SessionPool`, `EventBus`-based event routing, capability-based extensions, and pydantic-graph team execution. Meanwhile, `feat/0042` (the current working branch) adds ACP subagent delegation, catalog advertisement, foreground child cancellation, and `ToolCallStart(kind="subagent")` event conversion.

These two branches diverged from a common base (`bcd8ac876`) and both touch the same core ACP server files (`acp_agent.py`, `session.py`, `event_converter.py`, `session_manager.py`). We need to rebase `develop/agentic` onto `feat/0042` so that the SessionPool orchestration coexists with subagent delegation capabilities. Without this, the project cannot move forward with both features in a single branch.

## What Changes

- **Rebase `develop/agentic` onto `feat/0042`**: Replay the 2 commits (orchestrator + race fix) on top of the 11 subagent/delegation commits.
- **Resolve merge conflicts** in 4 ACP server files where both branches made incompatible modifications:
  - `acp_agent.py`: Remove per-session agent registry (`_session_agents`, `get_or_create_session_agent`) while keeping subagent catalog provider (`_catalog_provider`).
  - `session.py`: Keep subagent delegation policies and foreground child cancellation; adapt to direct pool agent assignment (SessionPool replaces per-session agents).
  - `event_converter.py`: Merge `TurnCompleteUpdate` emission (from develop) with subagent `ToolCallStart` conversion (from feat/0042).
  - `session_manager.py`: Accept SessionPool-based child session creation while preserving `cancel_session()` method.
- **Update tests**: Rewrite or delete tests referencing removed APIs (`get_or_create_session_agent`, `_session_agents`), fix session manager tests for SessionPool, and add new tests for subagent delegation under SessionPool.
- **BREAKING**: `get_or_create_session_agent()` and `remove_session_agent()` are permanently removed from `AgentPoolACPAgent`. Per-session agents are now managed exclusively by `SessionPool`.

## Capabilities

### New Capabilities
- `subagent-delegation-session-pool-compat`: Ensures subagent delegation policies (`auto`/`disable`/`prefer`/`require`) work correctly when the ACP server is running in SessionPool mode (`use_session_pool=true`). Currently, delegation only works in the legacy `ACPSession.process_prompt()` path.

### Modified Capabilities
- *(none — this is a rebase/integration task with no new spec-level requirements)*

## Impact

- **Files affected**: `src/agentpool_server/acp_server/{acp_agent.py,session.py,event_converter.py,session_manager.py,handler.py}`, tests in `tests/servers/acp_server/`, `tests/acp_server/`
- **APIs removed**: `AgentPoolACPAgent.get_or_create_session_agent()`, `AgentPoolACPAgent.remove_session_agent()`, `AgentPoolACPAgent._session_agents`
- **APIs preserved**: `AgentPoolACPAgent.get_subagent_catalog()`, `ACPSession.cancel_session()`, subagent `ToolCallStart` events, `PromptDelegation` handling
- **Test breakage expected**: `tests/servers/acp_server/test_acp_per_session_agent_red_flags.py` (tests removed API), `tests/servers/acp_server/test_acp_session_manager_child_session.py` (SessionPool rewrite), some `tests/servers/opencode_server/` tests (`_session_agents` removal)
