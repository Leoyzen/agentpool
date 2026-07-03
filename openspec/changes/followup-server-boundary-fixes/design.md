## Design Decisions

### D1: Incremental fix strategy

Fix violations in batches by dependency direction:
1. **config→core (72 violations)**: These are the hardest. `agentpool_config` imports from `agentpool` for type references. Options: (a) move shared types to a neutral package, (b) use `TYPE_CHECKING` imports, (c) move the importing module to core. Evaluate per-case.
2. **server→cli/commands (8 violations)**: Server should not call CLI. Move the shared code to core or a utility module.

### D2: CI pipeline

Add `lint-imports` as a required check in `.github/workflows/ci.yml`. Initially, it runs with `allow_indirect_imports = true` (current state). As `ignore_imports` entries are removed, tighten the config. Final state: zero `ignore_imports`, `allow_indirect_imports = false`.

## Risks

- **R1**: 72 config→core violations may require significant refactoring. Some imports may be fundamental to the architecture (config models referencing core types). These need architectural decisions, not just import moves.
- **R2**: Moving code between packages may break entry points or create new circular deps.
