## Design Decisions

### D1: Single change, multiple phases
All 5 remaining phases are tracked in one openspec change (`followup-thin-wrapper-refactor`) rather than 5 separate changes. Each phase has its own spec under `specs/`. This keeps the tracking lightweight and matches the original `thin-wrapper-refactor` structure.

### D2: Reconcile with existing openspec changes
Three pre-existing openspec changes overlap with this work:
- `migrate-to-mcptoolset` — covers MCPToolsetFactory equivalent. Reconcile: if that change completes first, task 5.2 becomes a thin wrapper.
- `refactor-skills-as-capabilities` — covers LocalSkillToolsetFactory + SkillCapability. Reconcile: may supersede tasks 5.3 and 6.10-6.11.
- `unify-tool-interception-to-pydantic-ai-capabilities` — covers tool interception via capabilities. Reconcile: may subsume ToolOutputBudgetCapability's truncation logic (task 6.14).

### D3: Phase ordering preserved
Phases must complete in order: 4 → 5 → 6 → 7 → 8. Phase 8 (rename) is last because it would conflict with any open phase's diff.

### D4: Drop SkillBridgeCapability (task 5.11)
Phase 6 (PR #100) implemented `SkillActivationCapability` which injects skill content into `SystemPromptPart` via `before_model_request`. This supersedes the planned `SkillBridgeCapability`. Task 5.11 is dropped.

## Risks

- **R1**: 72 config→core import violations (Phase 7) may require architectural decisions, not just import moves.
- **R2**: 70+ caller migration (Phase 5) may surface edge cases where `ResourceProvider` semantics differ from `ToolsetFactory`.
- **R3**: Dependency on `migrate-to-mcptoolset` and `refactor-skills-as-capabilities` — if those changes stall, Phase 5 is blocked.
