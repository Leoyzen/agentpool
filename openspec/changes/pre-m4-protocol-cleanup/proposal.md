# Pre-M4 Protocol Server Debt Cleanup

## Why

The `refactor/agentwolf_v1` branch completed M1 (HostContext), M2 (lifecycle dimensions), and M3 (capability-native refactor). The `.agent_pool` backdoor is eliminated (0 refs), ResourceProvider hierarchy is deleted, and the capability system is in place. However, three systematic explorations of ACP server, OpenCode server, and shared infrastructure revealed **51 technical debt items** — 6 blocking, 14 severe/moderate, and 31 nice-to-have.

This change covers the debt items that are **orthogonal to M4** — they touch files and abstractions that M4 does not modify. Debt items that overlap with M4's scope (OpenCode server hardening, RunScope identity abstraction) are merged into the `m4-multi-config` change to avoid two rounds of changes to the same files.

## What Changes

### Phase 1: ACP Execution Path Unification (6 tasks)
- Build `ACPAgentAPI` adapter implementing full `ACPClientProtocol` (add `stream_events()`, `get_messages()`); bridge state-polling to async-iterator; remove `cast()`
- Refactor `ACPAgent._stream_events()` to delegate to `ACPTurn.execute()`
- Remove `_run_stream_once()` hook firing for ACP agents
- Remove `hooks_fired` double-fire guard (21 refs across 4 files); replace `_log_tool_execution` idempotency guard with `_logged_tools` set
- Remove deprecated `queue_prompt`/`inject_prompt` ACP branching
- Update tests that mock `_run_stream_once` (3 test files, BLOCKING dependency)

### Phase 2: Legacy Field & API Cleanup (6 tasks)
- Verify/adapt existing `MCPManager` API (`get_session_context`, `update_session_snapshot` already exist); add `add_transport` delegation
- Update `ACPSession.initialize_mcp_servers()` to call `MCPManager` methods instead of mutating agent fields
- Remove `_mcp_snapshot` and `_session_connection_pool` from `NativeAgent` (only accessed in `session.py`, not `get_agentlet()`)
- Add `deliver_feedback` and `set_replaying` to `CommChannel` protocol; remove duck-typing, `try/except AttributeError` dead code, and `# type: ignore`
- Remove `HostContext.pool` escape hatch (1 remaining site in `base_team.py:411`)
- Remove legacy `RunStatus` enum (in `run.py:130`, NOT `lifecycle/types.py`); add `RunOutcome` enum to `lifecycle/types.py`; migrate `steer()`/`followup()` checks; update all consumers across `src/`
- Remove dead code in `session_controller.py:484-491`

### Phase 4: Type Safety & Code Quality (6 tasks)
- Remove 6 `# type: ignore[attr-defined]` in `run.py`; remove `__post_init__` journal injection pattern
- Add `set_replaying()` to `CommChannel` protocol; replace direct `_replaying` access
- Replace `_channel_publishes_to_event_bus` isinstance check with `publishes_to_event_bus: bool` property
- Replace `hasattr` patterns in ACP code with typed interfaces
- Fix `ACPTurn` generic `except Exception` clauses (3 sites)
- Refactor `RunHandle.start()` (397 SLOC) into 5 composable sub-methods each < 100 SLOC

### Phase 6: Event System Gaps (3 tasks)
- Wire `McpToolsChangedEvent` (defined but never emitted/consumed); note cross-layer wiring if event stays in server models
- Distinguish `StreamCompleteEvent(cancelled=True)` in `EventProcessor`
- Remove deprecated `stream_adapter._handle_event` (kept for tests only)

### Phase 7: Deferred / Nice-to-have (optional, not blocking M4)
- 14 items: `_agent_pool` constructor threading, ACP TODOs, test modernization. Tracked in tasks.md but not required for M4 start.

## Merged into M4

The following phases were moved to the `m4-multi-config` change because they touch the same files M4 modifies:

- **Phase 3: OpenCode Server Hardening** (5 tasks) — `state.pool.*` migration (68 sites), private attribute access (6 files), dual abort paths, legacy fallback removal. M4's RunScope routing modifies the same OpenCode route files.
- **Phase 5: M4 Identity Preparation** (4 tasks) — `RunScope` dataclass, session/pool identity abstraction, `session_controller` hardcoding removal. M4's task groups 7-8 and 14 already cover RunScope creation and HostContext modifications; the remaining OpenCode-specific identity work is added as M4 task group 18.

## Capabilities

### Modified Capabilities
- `acp-server`: Unify ACP execution through `Turn.execute()`, remove dual-path hook firing
- `session-orchestration`: Remove legacy `RunStatus`, type `CommChannel`, refactor `RunHandle.start()`
- `unified-event-routing`: Handle `RunStartedEvent`, wire `McpToolsChangedEvent`, distinguish cancellation

## Impact

- `src/agentpool/agents/base_agent.py` — remove `_run_stream_once` hook firing, deprecated `queue_prompt`/`inject_prompt` ACP branching
- `src/agentpool/agents/acp_agent/acp_agent.py` — new `ACPAgentAPI` adapter, refactor `_stream_events()`
- `src/agentpool/agents/acp_agent/turn.py` — fix generic except clauses
- `src/agentpool/orchestrator/run.py` — type `CommChannel`, refactor `start()`, remove `# type: ignore` cluster
- `src/agentpool/orchestrator/turn.py` — remove `hooks_fired` guard
- `src/agentpool/orchestrator/session_controller.py` — remove dead code
- `src/agentpool/lifecycle/comm_channel.py` — add `deliver_feedback` to protocol
- `src/agentpool/lifecycle/types.py` — add `RunOutcome` enum (next to `RunState`)
- `src/agentpool/orchestrator/run.py` — remove `RunStatus` enum (at line 130), remove `_status`/`status` fields, migrate `steer()`/`followup()`, refactor `start()`, remove `# type: ignore`
- `src/agentpool/agents/native_agent/agent.py` — remove `_mcp_snapshot`, `_session_connection_pool`
- `src/agentpool_server/acp_server/session.py` — stop mutating agent internals
- `src/agentpool_server/opencode_server/event_processor.py` — handle missing events

## Scope

- **In scope**: Phases 1, 2, 4, 6 (20 tasks) + Phase 7 optional (10 tasks)
- **Merged into M4**: Phase 3 (OpenCode hardening) + Phase 5 (identity preparation) — see `m4-multi-config` task group 18
- **Out of scope**: AgentWolf rename (deferred to last), M4 implementation itself, `subagent_display_mode` removal (deferred to Phase 7.10 — actively used, requires design decision)
