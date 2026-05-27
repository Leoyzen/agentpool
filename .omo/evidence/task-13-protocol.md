# Task 13: Update Protocol Bridge Implementations

## Summary
Successfully updated protocol bridge implementations to use the new skill system with skill:// URIs.

## Changes Made

### 1. SkillCommand (`src/agentpool/skills/command.py`)
- Added `skill_uri` optional field to store explicit skill:// URI
- Added `resolved_skill_uri` property that generates URI from name if not explicitly set

### 2. SkillCommandRegistry (`src/agentpool/skills/command_registry.py`)
- Added `skill_provider` parameter to constructor for aggregating provider support
- Added `_subscribe_to_skill_provider()` method to subscribe to skill provider changes
- Added `_on_skill_provider_changed()` handler for skill change events
- Added `_sync_from_skill_provider()` to sync skills from the aggregating provider
- Updated `initialize()` to subscribe to skill provider changes

### 3. AgentPool (`src/agentpool/delegation/pool.py`)
- Updated SkillCommandRegistry initialization to pass both `skills_registry` and `skill_provider`

### 4. OpenCode Skill Bridge (`src/agentpool_server/opencode_server/skill_bridge.py`)
- Added `skill_uri` attribute to `SkillCommandWrapper`
- Updated `execute_skill` to log and display skill:// URIs
- Added `on_commands_changed()` callback registration to `OpenCodeSkillBridge`
- Added `_notify_change()` method to broadcast command changes
- Updated `handle_change()` to notify callbacks and include skill_uri in logs

### 5. OpenCode Server (`src/agentpool_server/opencode_server/server.py`)
- Added callback to update CommandStore when skills change dynamically

### 6. ACP Skill Bridge (`src/agentpool_server/acp_server/commands/skill_commands.py`)
- Updated `_to_acp_command()` to extract and log skill_uri

### 7. Tests Updated
- Updated `tests/server/opencode/test_skill_bridge.py` to match new output format with URIs

## Test Results
All 179 skill-related tests pass:
- 39 OpenCode skill bridge tests ✓
- 13 ACP skill command tests ✓
- 32 E2E integration tests ✓
- 37 command registry core tests ✓
- 21 command registry watch tests ✓
- 57 command registry broadcast tests ✓

## Key Features
1. **skill:// URI Support**: All protocol bridges now support skill:// URIs
2. **Dynamic Skill Updates**: SkillCommandRegistry subscribes to skill provider changes
3. **CommandStore Updates**: OpenCode CommandStore updates when skills change
4. **Consistent Logging**: All skill operations include skill:// URI in logs
