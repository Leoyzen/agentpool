## 1. Modify ACPSkillBridge to produce SlashedCommand

- [x] 1.1 Replace `_to_acp_command()` with `create_skill_command()` import from `opencode_server/skill_bridge.py`; store `SlashedCommand` instead of `AvailableCommand` in `_commands`
- [x] 1.2 Rename `get_available_commands()` → `get_commands()` returning `list[SlashedCommand]`
- [x] 1.3 Remove unused `AvailableCommand`/`AvailableCommandInput`/`CommandInputHint` imports
- [x] 1.4 Verify `handle_change()` signature unchanged (still `(name: str, command: SkillCommand | None) -> None`)

## 2. Add `_register_skill_commands()` to ACPSession

- [x] 2.1 Add `_skill_bridge: ACPSkillBridge | None` field to `ACPSession.__post_init__`; instantiate `ACPSkillBridge()`
- [x] 2.2 Add `_skill_change_task: asyncio.Task | None` field and `_skill_register_lock: asyncio.Lock` for concurrent access guard
- [x] 2.3 Implement `_register_skill_commands()` method: iterate `host_context.skills_registry.list_skills()`, filter `user_invocable`, build `SkillCommand` objects, call `handle_change()` on bridge, register `SlashedCommand` objects in `command_store` with `replace=True`
- [x] 2.4 Call `_register_skill_commands()` in `__post_init__` after `_register_manifest_commands()`
- [x] 2.5 Call `self._notify_command_update()` after registration if any commands were added

## 3. Wire client skills in `init_client_skills()`

- [x] 3.1 After `add_skills_directory()` and skill count logging in `init_client_skills()`, call `_register_skill_commands()` to rebuild from full registry
- [x] 3.2 Call `await self.send_available_commands_update()` after registration to notify client
- [x] 3.3 Wrap registration + update in try/except with `log.exception` for graceful error handling

## 4. Add dynamic skill update subscription

- [x] 4.1 Implement `_watch_skill_changes()` async method: subscribe to `host_context.extension_registry.merge_change_streams(Scope(level=ScopeLevel.POOL))`, filter for `skills_changed` events, call `_register_skill_commands()` + `send_available_commands_update()`
- [x] 4.2 Start `_watch_skill_changes()` as background task in `__post_init__` (guarded by `extension_registry is not None`)
- [x] 4.3 Cancel `_skill_change_task` in `ACPSession.close()` or equivalent cleanup path
- [x] 4.4 Guard against `merge_change_streams()` returning `None` (no streams to merge)

## 5. Unit tests — ACPSkillBridge

- [x] 5.1 Test `handle_change()` adds a `SlashedCommand` to `_commands` (verify type is `SlashedCommand`, not `AvailableCommand`)
- [x] 5.2 Test `handle_change(name, None)` removes command from `_commands`
- [x] 5.3 Test `get_commands()` returns correct count and names after multiple `handle_change()` calls
- [x] 5.4 Test that `SlashedCommand` produced by bridge has correct `name`, `description`, `category="skill"`, `usage` fields
- [x] 5.5 Test bridge with empty registry (zero commands)

## 6. Unit tests — `_register_skill_commands()`

- [x] 6.1 Test pool-level skills registered in `command_store` after `__post_init__` (verify count matches `list_skills()` with `user_invocable=True`)
- [x] 6.2 Test `user_invocable=False` skills are excluded from `command_store`
- [x] 6.3 Test idempotent re-registration: call `_register_skill_commands()` twice, verify no duplicates in `command_store`
- [x] 6.4 Test `_register_skill_commands()` with empty skills registry (no commands added, no error)
- [x] 6.5 Test that `send_available_commands_update()` or `_notify_command_update()` is called after registration (mock + assert)
- [x] 6.6 Test `_register_skill_commands()` handles `host_context.skills_registry` being `None` gracefully

## 7. Unit tests — `init_client_skills()` integration

- [x] 7.1 Test that after `init_client_skills()` completes, client-discovered skills appear in `command_store`
- [x] 7.2 Test `send_available_commands_update()` is called after client skill registration
- [x] 7.3 Test `init_client_skills()` with no `.claude/skills` directory (no error, no new commands)
- [x] 7.4 Test pool + client skills coexist in `command_store` without duplicates

## 8. Unit tests — Dynamic skill updates

- [x] 8.1 Test `_watch_skill_changes()` calls `_register_skill_commands()` when `skills_changed` event arrives (mock the stream)
- [x] 8.2 Test `_watch_skill_changes()` ignores non-`skills_changed` events
- [x] 8.3 Test watcher task is cancelled when `close()` is called
- [x] 8.4 Test `_watch_skill_changes()` handles `merge_change_streams()` returning `None` (no crash, task exits)
- [x] 8.5 Test concurrent `_register_skill_commands()` calls are serialized by lock (no duplicate commands)

## 9. Unit tests — Skill command execution

- [x] 9.1 Test `execute_slash_command("/skill-name args")` finds skill in `command_store` and executes it
- [x] 9.2 Test skill executor loads instructions via `skill.load_instructions()` and injects into `ctx.data.node.staged_content`
- [x] 9.3 Test skill executor with `load_instructions()` raising `ValueError` (sends "no instructions" message, no injection)
- [x] 9.4 Test `execute_slash_command("/nonexistent")` returns without error
- [x] 9.5 Test injected prompt format: `<skill-instruction>` + `<user-request>` XML tags with correct content

## 10. Integration / E2E tests

- [x] 10.1 E2E: Create ACP session with pool skills → verify `available_commands_update` notification includes skill commands
- [x] 10.2 E2E: Create ACP session, call `init_client_skills()` with mock `.claude/skills` → verify update notification includes client skills
- [x] 10.3 E2E: Simulate `skills_changed` event from `ExtensionRegistry` → verify `available_commands_update` reflects new skill set
- [x] 10.4 E2E: Execute `/skill-name test-args` through ACP session → verify instructions injected into agent's `staged_content`
- [x] 10.5 E2E: Session close → verify watcher task cancelled, no lingering tasks
- [x] 10.6 E2E: Skills with `user-invocable: false` → verify they never appear in `available_commands_update` throughout full lifecycle

## 11. Regression and edge case tests

- [x] 11.1 Test existing `_register_manifest_commands()` still works (no interference from skill registration)
- [x] 11.2 Test existing `_register_mcp_prompts_as_commands()` still works
- [x] 11.3 Test `send_available_commands_update()` includes both manifest commands AND skill commands
- [x] 11.4 Test skill command names don't collide with manifest command names (last registered wins via `replace=True`)
- [x] 11.5 Test ACP session with `load_skills=False` config → pool skills still registered, client skills NOT discovered
- [x] 11.6 Test session with 50+ skills (performance: registration completes in reasonable time)
