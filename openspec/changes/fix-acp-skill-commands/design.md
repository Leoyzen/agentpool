## Context

The ACP server has a complete `ACPSkillBridge` class (`src/agentpool_server/acp_server/commands/skill_commands.py`) that was intended to expose skills as ACP slash commands. However, this class is **dead code** — never instantiated or wired into the session lifecycle. As a result, ACP clients never see skills in `available_commands_update` notifications and cannot invoke skills via `/skill-name`.

The OpenCode server already has working skill command wiring (`src/agentpool_server/opencode_server/skill_bridge.py` + `server.py:145-235`). This change brings the ACP server to parity.

### Current State

- `ACPSkillBridge` produces `AvailableCommand` (ACP display format) — NOT executable `SlashedCommand` objects
- `ACPSession.__post_init__` creates `CommandStore(commands=get_all_commands())` where `get_all_commands()` returns `[]`
- `init_client_skills()` discovers skills from `.claude/skills` but does NOT bridge them to commands
- `send_available_commands_update()` merges `get_acp_commands()` (from `command_store`) + `_remote_commands` — but no skill commands are in `command_store`
- `execute_slash_command()` uses `command_store.execute_command()` — skills not in `command_store` can't be executed

### Key Insight: `ctx.data` Shape Compatibility

ACP's `execute_slash_command()` creates a command context with `data=agent_context` where `agent_context = self.agent.get_context(data=self)`. This produces an `AgentContext` with:
- `.node` = the `BaseAgent` instance (has `staged_content`)
- `.data` = the `ACPSession` instance

OpenCode's `create_skill_command()` executor accesses `ctx.data.node.staged_content` — this works identically for ACP since `ctx.data` is the same `AgentContext` type. The function can be reused directly.

## Goals / Non-Goals

**Goals:**
- Wire `ACPSkillBridge` into `ACPSession` lifecycle so skills appear as slash commands in ACP
- Make skill commands executable via existing `execute_slash_command()` path
- Support both pool-level and client-side skills
- Support dynamic skill changes via `ExtensionRegistry` events
- Comprehensive test coverage to prevent regression

**Non-Goals:**
- Refactoring `ACPSkillBridge` API surface (public API unchanged)
- Adding new skill discovery mechanisms
- Modifying the ACP protocol itself
- Changing `send_available_commands_update()` logic (works unchanged)
- Modifying the OpenCode skill bridge (already works)

## Decisions

### D1: Modify `ACPSkillBridge` to produce `SlashedCommand` (not `AvailableCommand`)

**Choice**: Replace `_to_acp_command()` with `create_skill_command()` (reuse from OpenCode's `skill_bridge.py`).

**Rationale**: `SlashedCommand` objects are executable and can be registered in `CommandStore`. `AvailableCommand` is display-only. Since `get_acp_commands()` already converts `CommandStore` commands to `AvailableCommand`, using `SlashedCommand` means both display and execution work with zero changes to `send_available_commands_update()`.

**Alternative considered**: Keep `ACPSkillBridge` producing `AvailableCommand` and merge separately in `send_available_commands_update()`. Rejected because skill commands wouldn't be in `CommandStore` and couldn't be executed via `execute_slash_command()`.

### D2: Reuse `create_skill_command()` from `opencode_server/skill_bridge.py`

**Choice**: Import and reuse the existing `create_skill_command()` function directly.

**Rationale**: The function is protocol-agnostic — it loads skill instructions and injects them into `ctx.data.node.staged_content`. The `ctx.data` shape (`AgentContext`) is identical between ACP and OpenCode. No need to duplicate ~100 lines of execution logic.

**Alternative considered**: Create an ACP-specific `create_skill_command()`. Rejected because the execution logic is identical — only the bridge class structure differs.

### D3: Add `_register_skill_commands()` method to `ACPSession`

**Choice**: New method following the existing `_register_manifest_commands()` / `_register_mcp_prompts_as_commands()` / `_register_prompt_hub_commands()` pattern.

**Rationale**: Consistency with existing code. Each command source has its own registration method. The method builds `SkillCommand` objects from the skills registry, feeds them through `ACPSkillBridge.handle_change()`, and registers the resulting `SlashedCommand` objects in `command_store`.

### D4: Initialize bridge in `__post_init__`, register pool skills synchronously

**Choice**: Create `ACPSkillBridge` instance in `__post_init__` and call `_register_skill_commands()` immediately for pool-level skills.

**Rationale**: Pool-level skills are available at session creation time. Registering them synchronously ensures they appear in the first `send_available_commands_update()` call (which is scheduled as a background task in `acp_agent.py:new_session()`).

### D5: Bridge client skills in `init_client_skills()` with explicit update

**Choice**: After discovering client-side skills, call `_register_skill_commands()` again (idempotent via `replace=True`) then `send_available_commands_update()`.

**Rationale**: Client skills are discovered asynchronously. The second call to `_register_skill_commands()` rebuilds from the full registry (pool + client), and `replace=True` on `register_command` handles duplicates safely.

### D6: Subscribe to `ExtensionRegistry.merge_change_streams()` for dynamic updates

**Choice**: Add `_watch_skill_changes()` background task in `__post_init__`, cancelled on session close.

**Rationale**: Matches OpenCode's `_watch_skill_changes()` pattern. Skills can change at runtime (added/removed/modified). The watcher rebuilds skill commands and sends an update when `skills_changed` events arrive.

## Risks / Trade-offs

- **[Double-registration race] → Mitigation: `replace=True` on `register_command`**: Pool skills registered in `__post_init__` get re-registered when `init_client_skills()` calls `_register_skill_commands()`. `replace=True` prevents duplicates. Verify `slashed.CommandStore.register_command` supports `replace=True` (OpenCode uses it).

- **[Concurrent `_register_skill_commands()` calls] → Mitigation: Guard with lock**: `init_client_skills()` runs as a background task while the dynamic watcher may also fire. Consider using `self._task_lock` or a dedicated `asyncio.Lock` to serialize registration calls.

- **[`hasattr` usage in `create_skill_command()`] → Mitigation: Accept existing pattern**: The existing OpenCode code uses `hasattr` for `staged_content` access. While the project rules discourage `hasattr`, this is pre-existing code being reused, not new code. A future cleanup could replace with `isinstance` checks.

- **[Watcher task leak on abnormal close] → Mitigation: Cancel in `close()`**: The `_watch_skill_changes()` background task must be cancelled in `ACPSession.close()` or equivalent cleanup path. Missing this causes task leaks.
