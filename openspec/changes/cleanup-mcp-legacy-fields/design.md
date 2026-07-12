## Context

The MCP session lifecycle system went through two major refactors, with a third (Phase 2) planned for the future:

1. **PR #88 (`6bf3e7235`, Jul 2)**: Introduced three-tier connection scoping. Added `_mcp_snapshot` and `_session_connection_pool` fields to `Agent`, designed to be set externally by `SessionController`. The `as_capability()` API took these as parameters.

2. **Phase 1 lifecycle fix (`610564e1e`, Jul 8)**: Migrated `as_capability()` to a `session_id`-based API. `MCPManager` now internally manages `_SessionContext` objects in `_session_contexts`. The old fields were kept "for compat" but no longer set — creating dead code that caused the ACP transport registration bug (fixed symptomatically in `558c64472`).

3. **This change (`cleanup-mcp-legacy-fields`)**: Completes Phase 1's incomplete migration by removing the dead legacy fields and migrating all call sites to the `session_id` API. Does NOT introduce Phase 2 features.

4. **Phase 2 (future, not yet proposed)**: Per Phase 1's design, will remove per-agent MCPManager entirely, add allow/block list config for MCP servers, and consolidate skill MCP dual paths. This change is a prerequisite — it removes the dead code that would otherwise complicate Phase 2's scope.

Current state: two parallel paths coexist — the new `session_id` API (active) and the old Agent-field API (dead). All reads of `_session_connection_pool` always get `None`; `_mcp_snapshot` is written in 2 places but only meaningfully read by `session.py:543` (which has already been synced via `update_session_snapshot()` in the fix).

Oracle reviewed the proposed cleanup and confirmed: both fields should be removed, `SkillMcpManager` legacy path should remain primary for skills (it has idle timeout + retry that `SessionConnectionPool` lacks), and no `__setattr__` guard is needed.

## Goals / Non-Goals

**Goals:**
- Complete Phase 1's incomplete migration: remove all legacy fields and dead code paths
- Consolidate MCP session state on `MCPManager._session_contexts` as single source of truth
- Add public accessor to replace private dict access from external callers
- Migrate all 6 affected call sites to use the session_id API consistently

**Non-Goals:**
- Phase 2 features: removing per-agent MCPManager, adding allow/block list config, consolidating skill MCP dual paths
- Redesigning the 4-tier snapshot model (pool/agent/session/skill)
- Making `_build_mcp_toolsets_from_pool()` the primary path for skills (legacy `SkillMcpManager` is better for skills)
- Adding `__setattr__` guards or deprecation warnings (over-engineering per Oracle review)
- Changing the `as_capability()` public API signature (already stable from Phase 1)

## Decisions

### D1: Remove both legacy fields (not just `_session_connection_pool`)

**Decision**: Remove `_mcp_snapshot` AND `_session_connection_pool` from `Agent`.

**Rationale**: `_mcp_snapshot` on Agent is architecturally wrong for per-session agents — multiple sessions sharing one Agent instance would clobber each other's snapshots. `MCPManager._session_contexts` is the correct single source of truth. Keeping `_mcp_snapshot` as a "convenient cache" creates dual-write bugs (which is exactly what happened).

**Alternative considered**: Keep `_mcp_snapshot` as a read-only cache populated from `MCPManager`. Rejected — adds complexity for no benefit; the MCPManager is always accessible via `agent.mcp`.

### D2: Add `get_session_context()` public accessor

**Decision**: Add `MCPManager.get_session_context(session_id: str) -> _SessionContext | None`.

**Rationale**: 4 call sites already access `agent.mcp._session_contexts` directly (private dict). This is a smell. The method returns `None` (not creates) to avoid phantom context leaks — a bug already caught by `test_review_fixes_r3.py`.

**Alternative considered**: Use existing `get_or_create_session()`. Rejected — it creates phantom contexts when the caller only wants to read. The `as_capability()` method already uses `.get()` internally; externalizing this pattern is correct.

### D3: Keep legacy fallback as primary for skills

**Decision**: After migrating `capability.py` to read from session context, keep the fallback to `_build_mcp_toolsets_legacy_session()` as the primary path for skills.

**Rationale**: `SkillMcpManager` has idle timeout (5 min), exponential backoff retry (3 attempts), and per-run cleanup. `SessionConnectionPool` lacks these features. Switching skills to the session pool path would lose lifecycle management. The snapshot-based path in `_build_mcp_toolsets_from_pool()` is kept as a future option but remains non-default.

**Alternative considered**: Make `_build_mcp_toolsets_from_pool()` the primary path. Rejected — would silently lose skill MCP lifecycle features.

### D4: Preserve `get_agentlet()` execution ordering

**Decision**: Do NOT change the ordering of `as_capability()` (line 902) before skill config registration (lines 914-930).

**Rationale**: `as_capability()` processes `session_scoped_configs` which includes skill configs. If skill configs were in the snapshot when `as_capability()` runs, they would be processed by BOTH `as_capability()` AND `SkillCapability`, resulting in duplicate tools. The current ordering (skill configs written after `as_capability()`) prevents this.

### D5: `session.py:552` write removal is safe

**Decision**: Remove the `self.agent._mcp_snapshot = new_snapshot` write at `session.py:552`. The `update_session_snapshot()` call at line 556 is the sole write path.

**Rationale**: `update_session_snapshot()` syncs to `MCPManager._session_contexts[session_id].snapshot`, which is what `as_capability(session_id)` reads. The Agent-local `_mcp_snapshot` was only read by `session.py:543` (for the next merge) and `capability.py:221` (dead path). After migration, both read from the session context. The session context is created by `get_or_create_session_agent()` before `initialize_mcp_servers()` runs, so it always exists.

## Risks / Trade-offs

- **[BREAKING: Agent fields removed]** Custom Agent subclasses setting `_mcp_snapshot` or `_session_connection_pool` will get `AttributeError`. → Mitigation: These fields were never functional in the new API; callers were already silently broken. `AttributeError` is strictly better than silent failure.

- **[Test breakage]** 2 test files directly assert on `agent._mcp_snapshot` (`test_acp_session_mcp_registration.py`, `test_e2e_acp_inheritance_function_model.py`); 13 additional test files directly access `mcp._session_contexts` private dict for assertions; 1 additional file has `_session_connection_pool` in a function name (false positive). → Mitigation: Update all test assertions to use `get_session_context()` public API. Same logic, different accessor. Add integration test for mock MCP tool inheritance to subagents.

- **[Skill config registration timing]** `get_agentlet()` writes skill configs to session context after `as_capability()` runs. If a future change moves `as_capability()` after skill registration, duplicate tools will appear. → Mitigation: Document this ordering constraint in code comments and the spec.

- **[`_SessionContext` type exposure]** `get_session_context()` returns `_SessionContext`, which is a private type. External callers accessing `ctx.snapshot` and `ctx.connection_pool` depend on internal structure. → Mitigation: `_SessionContext` is a simple dataclass; its fields are stable. Full public type would be a separate refactor.
