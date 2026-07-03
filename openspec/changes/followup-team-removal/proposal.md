## Why

Phase 4 of the thin-wrapper refactor delivered the `teams:` → `graph:` translation layer (PR #97), but the actual removal of `Team` and `TeamRun` classes was deferred. The `TeamConfig.get_team()` factory method is deprecated but still present. 50+ callers still route through `TeamRun` instead of `GraphConfig` + `GraphBuilder`. This creates a dual-path architecture where both `teams:` and `graph:` YAML configs work, but the legacy path bypasses the pydantic-graph run loop, meaning Capability hooks (Phase 6) do not fire on team execution paths.

## What Changes

- Remove `Team` class from `src/agentpool/delegation/team.py`
- Remove `TeamRun` class from `src/agentpool/delegation/teamrun.py`
- Remove `TeamConfig.get_team()` factory method (already deprecated)
- Remove `_TeamGraphState` from `graph_team.py` if fully replaced
- Update `AgentPool.__init__` to stop creating `Team`/`TeamRun` instances
- Route all 50 callers of `TeamRun` through `GraphConfig` + `GraphBuilder`
- Test translator against all `teams:` YAML configs in `site/examples/`

## Impact

Breaking change for any code that imports `Team` or `TeamRun` directly. Pre-1.0, no external consumers expected. The `teams:` YAML syntax continues to work via auto-translation to `graph:`.

Part of #74. Depends on PR #93 merge (or can be stacked on `refactor/thin-wrapper`).
