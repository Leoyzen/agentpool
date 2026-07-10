## Context

M1 introduced `HostContext` as a frozen dataclass to carry infrastructure handles to agents, replacing direct `AgentPool` access. M2 added `DeprecationWarning` to `MessageNode.agent_pool` and began migrating call sites. However, the migration was only partially completed:

- **~181 of ~211 core agent refs migrated** (Phase 1 done)
- **~64 refs remain** across protocol servers (ACP ~47, OpenCode ~8), core messaging (~6), and misc (~3)
- **`HostContext.pool: AgentPool | None`** back-reference re-creates the backdoor HostContext was designed to eliminate
- **Skill orchestration** (`skill_capabilities`, `skill_provider`, `skill_commands`, `is_skill_visible_to_node`, `get_skill_instructions_for_node`) lives directly on `AgentPool` with no service abstraction
- **`AgentFactory`** stores `self._pool` AND reads `host_context.pool` (3 refs) — redundant

The existing design spec (`docs/superpowers/specs/2026-07-10-agent-pool-backdoor-cleanup-design.md`) and work plan (`.omo/plans/agent-pool-backdoor-cleanup.md`) contain the detailed file-by-file migration analysis. This design document summarizes the architectural decisions.

## Goals / Non-Goals

**Goals:**
- Complete all ~64 `.agent_pool` backdoor reference migrations to `host_context`
- Extract `SkillService` Protocol to decouple skill access from `AgentPool`
- Remove `HostContext.pool` back-reference field
- Migrate `ACPProtocolHandler` to receive `HostContext` instead of `AgentPool`
- Verify M1 deferred integration tests (T6.1/T6.4/T6.5/T6.6) and M2 DeprecationWarning clean check (T11.4/T12.9)
- Establish clean architectural baseline before M4

**Non-Goals:**
- No `AgentHost` or `HostRegistry` implementation (M4 scope)
- No config model split — `HostConfig`/`AgentManifest` stays deferred to M4
- No removal of `AgentFactory.self._pool` (blocked by `cfg.get_agent(pool=...)` which needs M4 config split)
- No removal of `MessageNode._agent_pool` private field (`host_context` property depends on it)
- No `ModelCache`/`ModelRegistry` real implementation (M4 scope)
- No `AgentFactory.recompile()` implementation (M4 scope, M1 T3.3 deferred)
- No changes to `MessageNode.__init__` `agent_pool` constructor parameter

## Decisions

### D1: SkillService Protocol matches AgentPool's existing method names exactly

**Choice**: `SkillService` Protocol uses `skill_capabilities`, `skill_provider`, `skill_commands`, `is_skill_visible_to_node`, `get_skill_instructions_for_node` — identical to AgentPool's existing method names.

**Rationale**: `@runtime_checkable Protocol` checks attribute name presence via `hasattr`. If names mismatch, `isinstance(pool, SkillService)` returns `False`. AgentPool already implements all five methods — duck-typing makes it conform without code changes.

**Alternatives considered**: Renaming to shorter names (e.g., `capabilities` instead of `skill_capabilities`) — rejected because it would require modifying AgentPool and break existing callers.

### D2: SkillService excludes write operations

**Choice**: `SkillService` only exposes read operations. `register_skill_provider`, `unregister_skill_provider`, and other mutation methods are excluded.

**Rationale**: Write operations are only called during pool initialization, not during agent execution. Agents and protocol servers only need read access to skill state.

### D3: `_bind_pool()` for internal Talk wiring

**Choice**: Add `MessageNode._bind_pool(pool)` method that sets `self._agent_pool = pool` directly, used by `Talk` wiring instead of the public `agent_pool` setter.

**Rationale**: `talk.py` currently uses `ctx.pool` to wire connected nodes. After removing `HostContext.pool`, Talk needs an alternative. Using the public `agent_pool` setter would emit `DeprecationWarning`. A dedicated internal method avoids warnings and makes the wiring path explicit.

**Alternatives considered**: Passing `HostContext` through Talk connections instead of pool — rejected because `host_context` property on `MessageNode` delegates to `_agent_pool.get_context()`, so the pool reference is still needed internally.

### D4: AgentFactory keeps `self._pool` — does not read from HostContext

**Choice**: `AgentFactory` uses its own `self._pool` field (already exists at line 93) instead of reading `host_context.pool` (3 refs at lines 419, 509, 567).

**Rationale**: `AgentFactory` receives `pool` in its constructor and stores it. The 3 `host_context.pool` reads are redundant with `self._pool`. Full removal of `self._pool` is blocked by `cfg.get_agent(pool=...)` which requires the pool parameter — that dependency resolves with M4's config split.

### D5: ACPProtocolHandler receives HostContext, not AgentPool

**Choice**: Change `ACPProtocolHandler.__init__` parameter from `agent_pool: AgentPool` to `host_context: HostContext`.

**Rationale**: `ACPProtocolHandler` only accesses `session_pool` and `event_bus` — both available on `HostContext`. This is the only protocol server constructor that takes `AgentPool` directly; OpenCode and AG-UI already receive it through other paths.

### D6: Optional property removal deferred to M4

**Choice**: Removing the `agent_pool` property getter/setter from `MessageNode` and migrating test files is optional pre-M4. Can defer if time-constrained.

**Rationale**: The property emits `DeprecationWarning` and delegates to `_agent_pool`. With all source code migrated, only test files reference it. Removing it is clean but not blocking for M4.

## Risks / Trade-offs

- **[Risk] ~64 reference sites across 15 files** → Mitigation: Each migration is mechanical (property → host_context field mapping is 1:1 for most accesses). Parallelized across 5 waves.
- **[Risk] ACP snapshot tests may break** → Mitigation: `ACPSession.agent_pool` property rename to `host_context` is a breaking change for test code. Snapshot tests will be updated in the same todo.
- **[Risk] Null-safety for `skill_service`** → Mitigation: All accesses guard with `if ctx.skill_service is not None` before calling methods. Pattern documented in each todo.
- **[Trade-off] `AgentFactory.self._pool` remains** → Accepted: Full removal needs M4 config split (`cfg.get_agent(pool=...)` dependency). Not a backdoor — factory is L3, pool is L2.
- **[Trade-off] `MessageNode._agent_pool` private field remains** → Accepted: `host_context` property depends on it. Full removal needs M4 `AgentHost` to own context construction.
