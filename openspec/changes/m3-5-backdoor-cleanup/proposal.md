## Why

M1 introduced `HostContext` to replace direct `AgentPool` access, and M2 added `DeprecationWarning` to `MessageNode.agent_pool`. But M2 Task 11 was falsely marked complete — only core agents (~181 refs) were migrated. Protocol servers (ACP ~47 refs, OpenCode ~8 refs) and remaining modules (~9 refs) were never migrated. Additionally, `HostContext` has a `pool: AgentPool | None` back-reference that re-creates the exact backdoor HostContext was designed to eliminate, and skill orchestration logic lives directly on `AgentPool` with no service abstraction. This change completes the migration before M4 (multi-config) begins, establishing a clean architectural baseline.

## What Changes

- Extract `SkillService` Protocol — a `@runtime_checkable Protocol` matching AgentPool's existing skill method names (`skill_capabilities`, `skill_provider`, `skill_commands`, `is_skill_visible_to_node`, `get_skill_instructions_for_node`). Write operations excluded.
- Extend `HostContext` with `main_agent_name: str | None = None` and `skill_service: SkillService | None = None` fields.
- Add `MessageNode._bind_pool()` internal method for Talk wiring without using the public `agent_pool` setter.
- Migrate all ~64 `.agent_pool` property accesses to `host_context` across 15 files (ACP server, OpenCode server, core agents, commands, misc).
- Migrate `AgentFactory` to use `self._pool` instead of `host_context.pool` (3 refs).
- Migrate `talk.py` to use `_bind_pool()` instead of `ctx.pool` (2 refs).
- **BREAKING**: Remove `pool: AgentPool | None` back-reference field from `HostContext`.
- Rename `ACPSession.agent_pool` property to `host_context`.
- Migrate `ACPProtocolHandler` constructor to receive `HostContext` instead of `AgentPool`.
- Update AGENTS.md to reflect completed migration.
- Verify M1 T6.1/T6.4/T6.5/T6.6 (deferred integration tests) and M2 T11.4/T12.9 (DeprecationWarning clean check).

## Capabilities

### New Capabilities
- `skill-service`: Protocol abstraction for skill orchestration operations (read-only: list capabilities, get provider, get commands, check visibility, get instructions). Decouples skill access from AgentPool god-class.

### Modified Capabilities
- `host-context`: Add `main_agent_name` and `skill_service` fields. Remove `pool` back-reference field. HostContext no longer carries a reference to AgentPool.
- `agent-pool`: AgentPool implements `SkillService` Protocol (duck-typed). `get_context()` passes `skill_service=self` and `main_agent_name=self.main_agent_name`.
- `agent-factory`: Use `self._pool` instead of `host_context.pool` for agent creation. Factory no longer reads pool from HostContext.

## Impact

- **Affected code**: ~64 `.agent_pool` references across 15 files (`src/agentpool_server/acp_server/`, `src/agentpool_server/opencode_server/`, `src/agentpool/agents/native_agent/`, `src/agentpool/delegation/`, `src/agentpool/messaging/`, `src/agentpool/host/`, `src/agentpool/talk/`, `src/agentpool_commands/`)
- **New files**: `src/agentpool/capabilities/skill_service.py`
- **Breaking changes**: `HostContext.pool` field removed (was `AgentPool | None = None`). `ACPSession.agent_pool` property renamed to `host_context`. `ACPProtocolHandler` constructor signature changed.
- **No YAML schema changes**: Existing configs work without modification.
- **No public API changes**: `pool.get_agent()`, `agent.run()`, `agent.run_stream()` unchanged.
- **Dependencies**: Unblocks M4 (multi-config). No external dependency changes.
- **Test impact**: `pytest -W error::DeprecationWarning` must pass on key suites after migration. Test files referencing `.agent_pool` property need migration (optional, can defer to M4).
