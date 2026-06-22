## Context

The OpenCode server (`src/agentpool_server/opencode_server/`) currently uses a dual-path architecture:

1. **SessionPool path** (`OpenCodeSessionPoolIntegration`): Routes main message processing through `SessionPool.receive_request()` → `TurnRunner.run_loop()` → `EventBus`. This is the intended architecture per `sessionpool-only-execution` spec.

2. **Legacy direct path** (`ServerState` in-memory): Uses `state.sessions`, `state.messages` dictionaries for session tracking. Route handlers directly mutate these dicts. Auxiliary paths (init, summarize, commands, skills, MCP prompts) may bypass SessionPool depending on feature flags.

The feature flags in `OpenCodeConfig` (`use_session_pool_for_*`) gate whether each auxiliary path routes through SessionPool or falls back to direct agent invocation. These flags default to `True` (SessionPool) but the fallback code paths remain in the codebase.

The `session_pool_integration.py` module contains helper functions (`_use_session_pool_for_messages`, `_use_session_pool_for_status`) that check these flags and branch between SessionPool and legacy dict paths.

### Codebase Reality

- `state.session_status` does **not** exist as a declared `ServerState` field. The fallback code in `set_session_status()`/`get_session_status()` uses `getattr(state, "session_status", None)` which always returns `None` in production — the fallback is dead code. However, tests inject `state.session_status = {}` dynamically.
- `state.messages` IS a real field (line 76 of `state.py`) used by subagent streaming fast-path, checkpoint restoration (`_reconstruct_tool_parts_from_checkpoint`), and `ensure_runtime_session_state`.
- `state.reverted_messages` IS a real field (line 75 of `state.py`) used by the revert/unrevert flow in `session_routes.py`.
- The summarize route has an identical compaction pipeline in BOTH branches (SessionPool: lines 1580-1603, non-SessionPool: lines 1617-1640). Flag removal just collapses the two identical blocks into one — no "preservation" work needed.

## Goals / Non-Goals

**Goals:**
- Remove all `OpenCodeConfig` feature flags — SessionPool is the unconditional single path
- Remove all conditional code paths that branch on feature flags
- Route all auxiliary paths (init, summarize, commands, skills, MCP prompts) through SessionPool unconditionally
- Preserve the summarize compaction pipeline by moving it into the always-executed path
- Simplify `session_pool_integration.py` by removing branching logic

**Non-Goals:**
- Do NOT remove `state.messages` from `ServerState` (deferred to follow-up)
- Do NOT remove `state.reverted_messages` from `ServerState` (deferred to follow-up)
- Do NOT modify event types or the EventBus itself
- Do NOT change `ProtocolEventConsumerMixin` behavior
- Do NOT touch ACP, AG-UI, OpenAI API, MCP, or A2A servers

## Decisions

### Decision 1: Remove feature flags entirely, not just default to True

**Options considered:**
- A) Set all flags to `True` as default, keep the flag fields for emergency rollback
- B) Remove the flag fields entirely — SessionPool is the only path

**Decision: B — Remove entirely.**

**Rationale:** The flags were introduced as a migration safety net. The SessionPool path has been in production and stable. Keeping dead code paths adds maintenance burden and confusion about which path is authoritative. If a rollback is needed, git revert is the mechanism, not runtime feature flags.

### Decision 2: Keep `state.messages` as streaming buffer, remove `session_status` fallback

**Options considered:**
- A) Remove `state.messages` from `ServerState` entirely (original plan)
- B) Keep `state.messages` as streaming buffer only, not authoritative source

**Decision: B — Keep as streaming buffer.**

**Rationale:** `state.messages` is used by subagent streaming fast-path (live ToolPart updates during streaming), checkpoint restoration (`_reconstruct_tool_parts_from_checkpoint`), and `ensure_runtime_session_state`. Removing it requires redesigning these consumers. This is better done as a separate follow-up change. For this change, restrict `state.messages` to streaming buffer use only — `get_messages_for_session()` uses SessionPool as the authoritative source, not `state.messages`.

For `session_status`: The `getattr(state, "session_status", None)` fallback in `set_session_status()`/`get_session_status()` is dead code in production (the field was never declared on `ServerState`). Remove this fallback path. Tests that inject `state.session_status = {}` dynamically should be updated to use `OpenCodeSessionPoolIntegration` APIs instead.

### Decision 3: Route all auxiliary paths through `SessionPool.receive_request()`

**Options considered:**
- A) Route each auxiliary path (init, summarize, commands, skills, MCP) through separate SessionPool APIs
- B) Route all through `SessionPool.receive_request()` with appropriate priority

**Decision: B — Unified `receive_request()` entry point.**

**Rationale:** `SessionController.receive_request()` is already the single entry point for message routing. It handles idle-vs-active session detection, priority routing (steer/followup), and RunHandle creation. Auxiliary paths should use the same mechanism rather than inventing new SessionPool APIs.

**Critical detail for summarize path:** The compaction pipeline (`compact_conversation()` + `set_messages_for_session()`) already exists in BOTH branches (SessionPool: lines 1580-1603, non-SessionPool: lines 1617-1640). The SessionPool branch uses `session_pool.run_stream()` (awaitable) rather than `receive_request()` (fire-and-forget), which is correct — the route handler must await stream completion to run post-processing. Flag removal simply collapses the two identical branches into one. Commands, skills, init, and MCP prompts use `receive_request()` since they have no post-processing requirement.

### Decision 4: Keep subagent streaming fast-path via `state.messages`

**Options considered:**
- A) Redesign subagent streaming to use EventBus events only
- B) Keep the subagent fast-path (in-memory `state.messages` for live ToolPart updates)

**Decision: B — Keep fast-path.**

**Rationale:** Subagent sessions stream `ToolPart` updates in-place on `state.messages` objects. The SessionPool message cache is a snapshot, not live-updating during streaming. Redesigning this would require significant architectural changes (EventBus-based ToolPart streaming). This change keeps `state.messages` as a streaming buffer only — `get_messages_for_session()` reads from SessionPool, not from `state.messages`, for authoritative message history. The fast-path is a performance optimization, not a competing source of truth.

## Risks / Trade-offs

- **Risk: YAML configs with `use_session_pool_for_*` fields will fail validation** → Mitigation: Document this as a breaking change. The fields are removed from `OpenCodeConfig` (Pydantic/Schemez model), so unknown fields will cause parse errors. Add migration note.
- **Risk: Summarize compaction pipeline is lost** → Mitigation: Explicitly move the `compact_conversation()` + `set_messages_for_session()` call into the always-executed path after SessionPool summarization.
- **Risk: Tests that mock `should_use_session_pool_for` will break** → Mitigation: Update `scripts/qa_test_server.py` and test files that mock or test feature flag behavior. Tests that inject `state.session_status` dynamically should use `OpenCodeSessionPoolIntegration` APIs instead.
- **Risk: `state.session_status` removal breaks tests** → Mitigation: Tests that dynamically set `state.session_status = {}` will fail. Update to mock `SessionStatusBridge` or `OpenCodeSessionPoolIntegration.get_session_status()` instead.
- **Risk: `get_session_lock` incorrectly identified as legacy-only** → Mitigation: Verified that `get_session_lock()` (line 221 of `state.py`) is used by `ensure_session()` for session creation serialization — NOT legacy-only. Do NOT remove it.

## Migration Plan

1. Remove feature flags from `OpenCodeConfig` (Pydantic model fields)
2. Remove `_use_session_pool_for_messages()` and `_use_session_pool_for_status()` helpers
3. Remove branching in `session_pool_integration.py` functions
4. Remove branching in `session_routes.py` auxiliary paths, preserving summarize compaction
5. Remove `getattr(state, "session_status", None)` fallback in status functions
6. Update `scripts/qa_test_server.py` to remove `should_use_session_pool_for` mock
7. Update tests: remove feature flag test cases, update `state.session_status` injection to use integration APIs
8. Run full test suite, verify no regressions

**Rollback**: git revert of this change. No runtime flags needed.

## Open Questions

- **Q1: Should `state.messages` be marked as internal (prefixed with `_`) to signal it's a streaming buffer, not authoritative source?** → Yes, prefix as `_messages` in the follow-up change that removes it from the public API surface.
- **Q2: Do any external consumers (e.g., OpenCode TUI clients) depend on `state.messages` being populated during streaming?** → Yes, the subagent fast-path depends on this. Keep `state.messages` for now, address in follow-up change.
