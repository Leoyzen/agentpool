## Why

The ACP server never sends skill slash commands in `available_commands_update` notifications. The `ACPSkillBridge` class exists but is dead code — never instantiated or wired into the session lifecycle. This means ACP clients (e.g., Zed) never see skills as available slash commands, and users cannot invoke skills via `/skill-name` in the ACP UI. The OpenCode server already has this wiring; the ACP server was left behind.

## What Changes

- **Modify `ACPSkillBridge`** to produce executable `SlashedCommand` objects (matching `OpenCodeSkillBridge`'s output type) instead of display-only `AvailableCommand` objects, so skill commands can be registered in `CommandStore` and executed via `execute_slash_command()`.
- **Add `_register_skill_commands()` to `ACPSession`** following the existing `_register_manifest_commands()` / `_register_mcp_prompts_as_commands()` pattern. This method builds `SkillCommand` objects from the skills registry, feeds them through `ACPSkillBridge`, and registers the resulting `SlashedCommand` objects in `command_store`.
- **Wire pool-level skills in `ACPSession.__post_init__`** so skills configured at the pool level appear immediately at session creation.
- **Bridge client-side skills in `init_client_skills()`** so skills discovered from `.claude/skills` are also registered and trigger `send_available_commands_update()`.
- **Add dynamic update subscription** via `ExtensionRegistry.merge_change_streams()` to rebuild skill commands when capabilities emit `skills_changed` events, matching OpenCode's `_watch_skill_changes()` pattern.
- **Add comprehensive unit and integration tests** covering: bridge conversion, command registration, dual skill sources (pool + client), dynamic updates, command execution, `user_invocable` filtering, idempotent re-registration, and session lifecycle cleanup.

## Capabilities

### New Capabilities

_(None — this is a bug fix that modifies an existing capability.)_

### Modified Capabilities

- `acp-server`: Add requirements for skill command availability in `available_commands_update` notifications, skill command execution via `execute_slash_command()`, dynamic skill command updates via `ExtensionRegistry`, and session lifecycle cleanup of skill watchers.

## Impact

- **Files modified**: `src/agentpool_server/acp_server/commands/skill_commands.py` (ACPSkillBridge), `src/agentpool_server/acp_server/session.py` (ACPSession), `src/agentpool_server/acp_server/acp_agent.py` (task scheduling)
- **Files created**: Unit tests for `ACPSkillBridge` and `_register_skill_commands()`, integration/e2e tests for the full ACP skill command flow
- **APIs affected**: No public API changes — `ACPSkillBridge` was never used externally
- **Dependencies**: No new dependencies; reuses existing `slashed`, `ExtensionRegistry`, and `SkillCommand` types
- **Risk**: Low — the fix activates dead code; no existing behavior changes. Main risk is `ctx.data` shape in the skill executor (needs verification).
