## ADDED Requirements

### Requirement: ACP skill commands SHALL appear in available_commands_update

The ACP server SHALL register skills as slash commands in the session's `CommandStore` so they appear in `available_commands_update` notifications. The `ACPSkillBridge` SHALL be instantiated during `ACPSession.__post_init__` and wired to the session's `CommandStore`. Pool-level skills SHALL be registered synchronously in `__post_init__`. Client-side skills discovered via `init_client_skills()` SHALL be registered and trigger `send_available_commands_update()` upon discovery.

#### Scenario: Pool-level skills appear in first available_commands_update

- **WHEN** an ACP session is created with a pool that has 3 user-invocable skills
- **THEN** `ACPSkillBridge` SHALL be instantiated in `__post_init__`
- **AND** all 3 skills SHALL be converted to `SlashedCommand` objects and registered in `CommandStore`
- **AND** the first `send_available_commands_update()` call SHALL include all 3 skill commands as `AvailableCommand` entries

#### Scenario: Client-side skills appear after discovery

- **WHEN** `init_client_skills()` discovers 2 new skills from `.claude/skills`
- **THEN** `_register_skill_commands()` SHALL be called to rebuild skill commands from the full registry
- **AND** `send_available_commands_update()` SHALL be called to notify the client
- **AND** the 2 new skills SHALL appear in the next `available_commands_update` notification

#### Scenario: Skills with user-invocable=false are excluded

- **WHEN** the skills registry contains 5 skills, 2 of which have `user-invocable: false`
- **THEN** only 3 skill commands SHALL be registered in `CommandStore`
- **AND** the 2 non-invocable skills SHALL NOT appear in `available_commands_update`

### Requirement: ACP skill commands SHALL be executable via execute_slash_command

Skill commands registered in `CommandStore` SHALL be executable via the existing `execute_slash_command()` method. When a user types `/skill-name args` in the ACP client, the skill's instructions SHALL be loaded and injected into the agent's `staged_content` for processing. The executor function SHALL reuse `create_skill_command()` from `opencode_server/skill_bridge.py`.

#### Scenario: Execute a skill command with arguments

- **WHEN** a user sends `/ponytail refactor this function` via the ACP client
- **AND** "ponytail" is a registered skill command
- **THEN** `execute_slash_command()` SHALL find the command in `CommandStore`
- **AND** the skill's instructions SHALL be loaded via `skill.load_instructions()`
- **AND** the instructions and user arguments SHALL be wrapped in `<skill-instruction>` and `<user-request>` XML tags
- **AND** the combined prompt SHALL be injected into `ctx.data.node.staged_content`
- **AND** a confirmation message SHALL be sent to the client via `output_writer`

#### Scenario: Execute a skill with no instructions

- **WHEN** a user sends `/empty-skill test` and the skill has no instructions (or `load_instructions()` raises `ValueError`)
- **THEN** the executor SHALL send "Skill {name} has no instructions" to the client
- **AND** SHALL NOT inject anything into `staged_content`

#### Scenario: Execute a non-existent skill command

- **WHEN** a user sends `/nonexistent args`
- **THEN** `CommandStore.get_command("nonexistent")` SHALL return `None`
- **AND** `execute_slash_command()` SHALL log a warning and return without error

### Requirement: ACPSkillBridge SHALL produce executable SlashedCommand objects

`ACPSkillBridge` SHALL convert `SkillCommand` instances to `SlashedCommand` objects (not `AvailableCommand`) so they can be registered in `CommandStore` and executed. The `handle_change()` method SHALL maintain a `dict[str, SlashedCommand]`. The `get_commands()` method SHALL return `list[SlashedCommand]`.

#### Scenario: handle_change adds a skill command

- **WHEN** `handle_change("ponytail", skill_cmd)` is called with a valid `SkillCommand`
- **THEN** a `SlashedCommand` SHALL be created via `create_skill_command(skill_cmd)`
- **AND** it SHALL be stored in `_commands["ponytail"]`

#### Scenario: handle_change removes a skill command

- **WHEN** `handle_change("ponytail", None)` is called
- **THEN** `"ponytail"` SHALL be removed from `_commands`

#### Scenario: get_commands returns all stored commands

- **WHEN** 3 commands have been added via `handle_change()`
- **THEN** `get_commands()` SHALL return a list of 3 `SlashedCommand` objects

### Requirement: ACP session SHALL subscribe to ExtensionRegistry for dynamic skill updates

`ACPSession` SHALL subscribe to `ExtensionRegistry.merge_change_streams()` to receive `skills_changed` events. When a `skills_changed` event arrives, the session SHALL rebuild skill commands via `_register_skill_commands()` and send `send_available_commands_update()`. The subscription SHALL be a background task created in `__post_init__` and cancelled on session close.

#### Scenario: Skill added at runtime triggers command update

- **WHEN** a `skills_changed` event arrives from `ExtensionRegistry`
- **THEN** `_register_skill_commands()` SHALL be called to rebuild from the current registry
- **AND** `send_available_commands_update()` SHALL be called to notify the client
- **AND** any newly added skills SHALL appear in the update

#### Scenario: Skill removed at runtime triggers command update

- **WHEN** a skill is removed from the registry and a `skills_changed` event arrives
- **THEN** `_register_skill_commands()` SHALL rebuild without the removed skill
- **AND** the removed skill's command SHALL no longer be in `CommandStore`
- **AND** `send_available_commands_update()` SHALL reflect the removal

#### Scenario: Watcher task is cancelled on session close

- **WHEN** `ACPSession.close()` is called
- **THEN** the skill change watcher background task SHALL be cancelled
- **AND** no further `skills_changed` events SHALL be processed for this session

### Requirement: _register_skill_commands SHALL be idempotent

Calling `_register_skill_commands()` multiple times SHALL NOT create duplicate commands in `CommandStore`. Each call SHALL rebuild from the full current registry state. `CommandStore.register_command()` SHALL be called with `replace=True` to overwrite any existing command with the same name.

#### Scenario: Re-registration after client skill discovery

- **WHEN** `_register_skill_commands()` is called in `__post_init__` with 3 pool skills
- **AND** `init_client_skills()` calls `_register_skill_commands()` again with 3 pool + 2 client skills
- **THEN** `CommandStore` SHALL contain exactly 5 skill commands (no duplicates)
- **AND** the 3 pool skill commands SHALL be replaced (not duplicated)

#### Scenario: Concurrent registration calls are safe

- **WHEN** `_register_skill_commands()` is called concurrently by the dynamic watcher and `init_client_skills()`
- **THEN** the calls SHALL be serialized via a lock
- **AND** `CommandStore` SHALL NOT contain duplicate commands
