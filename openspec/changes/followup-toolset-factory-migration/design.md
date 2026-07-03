## Design Decisions

### D1: Reconcile with existing openspec changes

- `migrate-to-mcptoolset` already covers the MCP migration path (MCPServer → MCPToolset). `MCPToolsetFactory` should wrap the result of that migration, not duplicate it. If `migrate-to-mcptoolset` completes first, `MCPToolsetFactory` becomes a thin wrapper.
- `refactor-skills-as-capabilities` covers skill → capability migration. `LocalSkillToolsetFactory` may be unnecessary if skills become capabilities directly. Track and reconcile.
- `unify-tool-interception-to-pydantic-ai-capabilities` covers tool interception via capabilities, which may subsume parts of `PoolToolsetFactory`.

### D2: Drop SkillBridgeCapability (task 5.11)

Phase 6 (PR #100) implemented `SkillActivationCapability` which injects skill content into `SystemPromptPart` via `before_model_request`. This supersedes the planned `SkillBridgeCapability`. Task 5.11 is dropped.

### D3: Incremental migration with deprecation period

Keep `ResourceProvider` hierarchy during migration. Add `DeprecationWarning` to `__init__` methods. Remove only after all callers are migrated. This avoids a big-bang refactor.

## Risks

- **R1**: 70+ caller migration may surface edge cases where `ResourceProvider` semantics differ from `ToolsetFactory`.
- **R2**: Dependency on `migrate-to-mcptoolset` completion — if that change stalls, `MCPToolsetFactory` is blocked.
