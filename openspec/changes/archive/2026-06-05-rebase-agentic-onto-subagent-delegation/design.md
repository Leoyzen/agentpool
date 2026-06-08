## Context

`develop/agentic` (2 commits ahead of base) and `feat/0042` (11 commits ahead of base) diverged from `bcd8ac876`. Both modify the ACP server core files but for orthogonal reasons:

- **develop/agentic** replaces per-session agent creation with `SessionPool` orchestration, adds `ACPProtocolHandler` as an event consumer bridge, and gates everything behind a `use_session_pool` canary flag.
- **feat/0042** adds subagent delegation (`PromptDelegation` policies), subagent catalog advertisement, foreground child session cancellation, and `ToolCallStart(kind="subagent")` event conversion.

The rebase must produce a single branch where SessionPool mode and subagent delegation coexist. The key insight is that `ACPProtocolHandler.handle_prompt()` (SessionPool path) currently bypasses `ACPSession.process_prompt()` entirely, so subagent delegation logic must be extracted and made available to both paths.

## Goals / Non-Goals

**Goals:**
- Rebase `develop/agentic` onto `feat/0042` with clean history (2 commits replayed on top of 11)
- Resolve all merge conflicts in ACP server files without losing functionality from either branch
- Preserve backward compatibility: legacy path (`use_session_pool=false`) continues to work exactly as in feat/0042
- Ensure all existing tests pass after rebase; fix or delete tests referencing removed APIs

**Non-Goals:**
- Port subagent delegation into `ACPProtocolHandler` in this change (tracked separately by `subagent-delegation-session-pool-compat` spec)
- Migrate opencode_server or other protocols to SessionPool
- Add new subagent features beyond what already exists in feat/0042
- Preserve the `_session_agents` registry — it is intentionally removed by develop/agentic

## Decisions

### Decision 1: `acp_agent.py` — keep both `_protocol_handler` and `_catalog_provider`
- **Rationale**: `_protocol_handler` (SessionPool bridge) and `_catalog_provider` (subagent catalog) are orthogonal responsibilities. The class is a dataclass with no `__slots__`, so adding both fields is safe.
- **Alternative considered**: Merge them into a single provider. **Rejected** — they have different lifecycles (protocol handler is optional/config-gated; catalog is always present).

### Decision 2: Accept develop/agentic's removal of per-session agent APIs
- **Rationale**: `SessionPool` replaces the per-session agent lifecycle. Keeping both systems would create duplicate state and confusion.
- **Migration**: Any code calling `get_or_create_session_agent()` must use `pool.all_agents[agent_name]` directly, or rely on `SessionPool.create_session()`.

### Decision 3: `event_converter.py` — merge both event handling paths
- **Rationale**: The two branches touch different event types (`StreamCompleteEvent` vs `SpawnSessionStart`) but both modify the converter's state management (`reset()`). We need both `TurnCompleteUpdate` emission AND subagent `ToolCallStart` conversion.
- **Conflict resolution order**: Apply feat/0042's subagent changes first, then layer develop/agentic's `TurnCompleteUpdate` and `UsageUpdate` always-yield logic on top.

### Decision 4: `session_manager.py` — adopt develop's SessionPool child session path, keep feat/0042's `cancel_session()`
- **Rationale**: `cancel_session()` is a simple delegation method used by foreground child cancellation in `ACPSession`. It does not conflict with SessionPool's child session creation logic.

### Decision 5: Delete `test_acp_per_session_agent_red_flags.py`
- **Rationale**: The entire file tests `get_or_create_session_agent()`, which is removed. No replacement tests are needed — SessionPool has its own test coverage.

## Risks / Trade-offs

- **[Risk]** `ACPSession.process_prompt()` subagent delegation does not work when `use_session_pool=true` because `ACPProtocolHandler.handle_prompt()` bypasses `ACPSession` entirely.
  - **Mitigation**: Documented as a known gap. The spec `subagent-delegation-session-pool-compat` tracks the follow-up work.
- **[Risk]** Test coverage for subagent catalog + SessionPool interaction is missing.
  - **Mitigation**: Add integration tests in `tests/servers/acp_server/` that verify catalog updates are still sent when SessionPool is active.
- **[Risk]** The rebase may introduce subtle bugs in event ordering (converter state is now mutated by both branches' logic).
  - **Mitigation**: Run the snapshot test suite (`test_acp_event_converter_snapshots.py`) before and after; any diff indicates an ordering regression.

## Migration Plan

1. **Pre-rebase**: Ensure `feat/0042` branch is clean (all tests pass on current branch).
2. **Rebase**: `git rebase develop/agentic` onto `feat/0042` — resolve 4-file conflicts.
3. **Post-rebase fixes**: Delete `test_acp_per_session_agent_red_flags.py`, fix `test_acp_session_manager_child_session.py` for SessionPool, run full test suite.
4. **Validation**: Run `pytest tests/servers/acp_server/ tests/acp_server/ -v` and confirm green.

## Open Questions

- Should `ACPProtocolHandler.handle_prompt()` be extended to support `PromptDelegation` in this change, or deferred?
- Do any opencode_server tests break due to `_session_agents` removal, and if so, should they be fixed here or in a separate change?
