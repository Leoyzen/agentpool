## Why

M1 introduced `HostContext` to replace direct `AgentPool` access, and M2 added `DeprecationWarning` to `MessageNode.agent_pool`. But M2 Task 11 was falsely marked complete — only core agents (~181 refs) were migrated. Protocol servers (ACP ~47 refs, OpenCode ~8 refs) and remaining modules (~9 refs) were never migrated. Additionally, `HostContext` has a `pool: AgentPool | None` back-reference that is used as a temporary escape hatch for skill-related accesses. This change completes the mechanical migration before M4 (multi-config) begins, establishing a clean architectural baseline.

Skill orchestration abstraction (`SkillService` Protocol) is **deferred to a separate change** — it requires independent design for future extensibility (multi-tenant scoping, dynamic loading, permission model).

## What Changes

- Extend `HostContext` with `main_agent_name: str | None = None` field.
- Add `MessageNode._bind_pool()` internal method for Talk wiring without using the public `agent_pool` setter.
- Migrate all ~64 `.agent_pool` property accesses to `host_context` across 15 files (ACP server, OpenCode server, core agents, commands, misc). Skill-related refs (~6) migrate to `host_context.pool.X` as interim pattern — eliminates DeprecationWarning, will be replaced by `host_context.skill_service.X` in the skill-service change.
- Migrate `AgentFactory` to use `self._pool` instead of `host_context.pool` (3 refs).
- Migrate `talk.py` to use `_bind_pool()` instead of `ctx.pool` (2 refs).
- Rename `ACPSession.agent_pool` property to `host_context`.
- Migrate `ACPProtocolHandler` constructor to receive `HostContext` instead of `AgentPool`.
- Keep `HostContext.pool` field as temporary escape hatch for ~6 skill refs (will be removed in skill-service change).
- Update AGENTS.md to reflect completed migration.
- Verify M1 T6.1/T6.4/T6.5/T6.6 (deferred integration tests) and M2 T11.4/T12.9 (DeprecationWarning clean check).

## Capabilities

### New Capabilities

(none — SkillService is deferred to a separate change)

### Modified Capabilities
- `host-context`: Add `main_agent_name` field. `pool` field remains as temporary escape hatch for skill-related accesses (to be removed in skill-service change).
- `agent-pool`: `get_context()` passes `main_agent_name=self.main_agent_name`. AgentPool no longer passed to protocol server constructors.
- `agent-factory`: Use `self._pool` instead of `host_context.pool` for agent creation. Talk wiring uses `_bind_pool()` instead of `ctx.pool`.

## Impact

- **Affected code**: ~64 `.agent_pool` references across 15 files (`src/agentpool_server/acp_server/`, `src/agentpool_server/opencode_server/`, `src/agentpool/agents/native_agent/`, `src/agentpool/delegation/`, `src/agentpool/messaging/`, `src/agentpool/host/`, `src/agentpool/talk/`, `src/agentpool_commands/`)
- **Breaking changes**: `ACPSession.agent_pool` property renamed to `host_context`. `ACPProtocolHandler` constructor signature changed.
- **No YAML schema changes**: Existing configs work without modification.
- **No public API changes**: `pool.get_agent()`, `agent.run()`, `agent.run_stream()` unchanged.
- **Dependencies**: Unblocks M4 (multi-config). Skill-service extraction will be a follow-up change. No external dependency changes.
- **Test impact**: `pytest -W error::DeprecationWarning` must pass on key suites after migration. Test files referencing `.agent_pool` property need migration (optional, can defer to M4).
