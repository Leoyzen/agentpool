## Why

Phase 6 of the thin-wrapper refactor implemented all 6 pdai Capabilities (LoopDetection, TokenBudget, ToolOutputBudget, DynamicContext, SkillActivation, Memory) as standalone classes with 54 unit tests (PR #100). However, the Capabilities are not yet wired into agents: there is no YAML config support (`capabilities:` section), the `Agent` class does not accept or attach Capabilities from config, and existing hooks (`pre_run`, `post_run`, `pre_tool_use`, `post_tool_use`) have not been audited for overlap with Capability hooks.

This overlaps with two existing openspec changes:
- `refactor-skills-as-capabilities` — covers `SkillActivationCapability` migration to `SkillCapability`
- `unify-tool-interception-to-pydantic-ai-capabilities` — covers hook migration to capabilities

## What Changes

- Add `capabilities:` section to agent YAML config schema
- Update `Agent` class to accept and attach Capabilities from config
- Audit existing hooks (`pre_run`, `post_run`, `pre_tool_use`, `post_tool_use`) — migrate or document overlap with Capabilities
- Reconcile with `refactor-skills-as-capabilities` (SkillCapability may supersede SkillActivationCapability)
- Reconcile with `unify-tool-interception-to-pydantic-ai-capabilities` (tool hooks may subsume ToolOutputBudget)

## Impact

Enables users to attach Capabilities to agents via YAML. No breaking changes — Capabilities are additive.

Part of #74. Depends on PR #93 merge.
