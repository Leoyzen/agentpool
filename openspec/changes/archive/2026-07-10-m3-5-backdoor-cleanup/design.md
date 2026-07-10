## Context

M1 introduced `HostContext` as a frozen dataclass to carry infrastructure handles to agents, replacing direct `AgentPool` access. M2 added `DeprecationWarning` to `MessageNode.agent_pool` and began migrating call sites. However, the migration was only partially completed:

- **~181 of ~211 core agent refs migrated** (Phase 1 done)
- **~64 refs remain** across protocol servers (ACP ~47, OpenCode ~8), core messaging (~6), and misc (~3)
- **`HostContext.pool: AgentPool | None`** back-reference is used as temporary escape hatch for ~6 skill-related accesses
- **`AgentFactory`** stores `self._pool` AND reads `host_context.pool` (3 refs) — redundant
- **Skill orchestration abstraction** (`SkillService` Protocol) deferred to separate change — requires independent design

## Goals / Non-Goals

**Goals:**
- Complete all ~64 `.agent_pool` backdoor reference migrations to `host_context`
- Migrate ~6 skill-related refs to `host_context.pool.X` as interim pattern (eliminates DeprecationWarning)
- Add `main_agent_name` to HostContext
- Add `_bind_pool()` for internal Talk wiring
- Migrate `AgentFactory` to use `self._pool` instead of `host_context.pool` (3 refs)
- Migrate `ACPProtocolHandler` to receive `HostContext` instead of `AgentPool`
- Verify M1 deferred integration tests (T6.1/T6.4/T6.5/T6.6) and M2 DeprecationWarning clean check (T11.4/T12.9)
- Establish clean architectural baseline before M4

**Non-Goals:**
- No `SkillService` Protocol extraction — deferred to separate change (`skill-service-extraction`)
- No removal of `HostContext.pool` field — kept as temporary escape hatch for ~6 skill refs (will be removed in skill-service change)
- No `AgentHost` or `HostRegistry` implementation (M4 scope)
- No config model split (M4 scope)
- No removal of `AgentFactory.self._pool` (blocked by `cfg.get_agent(pool=...)`, M4 scope)
- No removal of `MessageNode._agent_pool` private field (`host_context` depends on it)
- No `AgentFactory.recompile()` implementation (M4 scope, M1 T3.3 deferred)

## Decisions

### D1: Skill refs migrate to `host_context.pool.X` as interim pattern

**Choice**: ~6 skill-related refs (in `native_agent/agent.py` and `base_team.py`) migrate from `self.agent_pool.skill_capabilities` to `self.host_context.pool.skill_capabilities`.

**Rationale**: This eliminates `DeprecationWarning` without introducing a new abstraction. When `SkillService` is designed in a separate change, these refs change from `host_context.pool.X` to `host_context.skill_service.X` — a simple find-replace.

**Alternatives considered**: (a) Leave skill refs on `self.agent_pool` — rejected, still emits DeprecationWarning. (b) Extract SkillService now — rejected, needs independent design for future extensibility.

### D2: `_bind_pool()` for internal Talk wiring

**Choice**: Add `MessageNode._bind_pool(pool)` method that sets `self._agent_pool = pool` directly, used by `Talk` wiring instead of the public `agent_pool` setter.

**Rationale**: `talk.py` currently uses `ctx.pool` to wire connected nodes. Using the public `agent_pool` setter would emit `DeprecationWarning`. A dedicated internal method avoids warnings and makes the wiring path explicit.

### D3: AgentFactory keeps `self._pool` — does not read from HostContext

**Choice**: `AgentFactory` uses its own `self._pool` field (already exists) instead of reading `host_context.pool` (3 refs).

**Rationale**: The 3 `host_context.pool` reads are redundant with `self._pool`. Full removal of `self._pool` is blocked by `cfg.get_agent(pool=...)` — that dependency resolves with M4's config split.

### D4: ACPProtocolHandler receives HostContext, not AgentPool

**Choice**: Change `ACPProtocolHandler.__init__` parameter from `agent_pool: AgentPool` to `host_context: HostContext`.

**Rationale**: `ACPProtocolHandler` only accesses `session_pool` and `event_bus` — both available on `HostContext`.

### D5: `HostContext.pool` kept as temporary escape hatch

**Choice**: `pool` field on HostContext is NOT removed in this change. It remains as the interim access path for ~6 skill refs.

**Rationale**: Removing `pool` requires `SkillService` to be in place. Since skill-service is a separate change, `pool` stays until that change completes. The field is documented as temporary.

## Risks / Trade-offs

- **[Risk] ~64 reference sites across 15 files** → Mitigation: Each migration is mechanical. Parallelized across waves.
- **[Risk] ACP snapshot tests may break** → Mitigation: `ACPSession.agent_pool` property rename covered in same task.
- **[Trade-off] `HostContext.pool` remains** → Accepted: Temporary escape hatch for ~6 skill refs. Will be removed in skill-service change.
- **[Trade-off] `AgentFactory.self._pool` remains** → Accepted: Full removal needs M4 config split.
- **[Trade-off] `MessageNode._agent_pool` private field remains** → Accepted: `host_context` property depends on it.

## Open Questions

- **`ACPSession.agent_pool` → `host_context` rename**: Does any external consumer (IDE integration, plugin) depend on `session.agent_pool`? ACP snapshot tests cover internal usage, but external dependencies are unknown.
- **`MessageNode._agent_pool` private field**: Acceptable as intermediate state? Full removal needs M4 `AgentHost` to own context construction via constructor injection rather than property derivation.
