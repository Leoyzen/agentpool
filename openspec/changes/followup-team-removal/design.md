## Design Decisions

### D1: Keep `teams:` YAML syntax (auto-translation)

The `teams:` YAML syntax is NOT removed. The auto-translation layer (PR #97) converts `teams:` to `graph:` at config load time. Users who wrote `teams:` configs do not need to migrate. Only the Python `Team`/`TeamRun` classes are removed.

### D2: Gradual caller migration

50 callers need updating. Migration is mechanical: replace `TeamRun` with `GraphConfig` + `GraphBuilder`. Can be done in batches if needed, but a single atomic commit is preferred to avoid mixed-state.

### D3: `graph_team.py` cleanup

`_TeamGraphState` was a bridge type. If `GraphConfig` + `GraphBuilder` fully replace it, remove it. If any state is still needed, extract into `graph_config.py` and document.

## Risks

- **R1**: Some callers may depend on `TeamRun` behavior that differs from graph execution (e.g., parallel `asyncio.gather()` path). These need case-by-case verification.
- **R2**: `site/examples/` YAML configs may reveal translation edge cases not covered by unit tests.
