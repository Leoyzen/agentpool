# Spec: HostContext — Escape Hatch Removal

## Requirements

### REQ-1: pool Escape Hatch Removal

`HostContext.pool: AgentPool | None` MUST be removed. The 1 remaining access site at `base_team.py:411` MUST be migrated to use `host_context` accessors or `DelegationService`.

**Rationale**: `pool` was kept as a temporary escape hatch during M1-M3 migration. With `.agent_pool` references at 0, the escape hatch is no longer needed.

## Verification

- `grep -rn 'host_context.pool' src/` returns 0

> Note: RunScope introduction, session/pool identity abstraction, public API for private attributes, and state.pool migration were moved to the `m4-multi-config` change (task group 18) because they touch the same OpenCode route files that M4 modifies.
