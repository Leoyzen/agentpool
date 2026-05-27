# RFC-0016 Implementation Learnings

## Project Structure

### Existing Skills Infrastructure
- `src/agentpool/skills/registry.py` - SkillsRegistry class extends BaseRegistry[str, Skill]
- `src/agentpool/skills/skill.py` - Skill Pydantic model with metadata and lazy-loaded instructions
- `src/agentpool/skills/manager.py` - SkillsManager for skill lifecycle management
- `src/acp/schema/slash_commands.py` - AvailableCommand schema for ACP protocol
- `src/acp/schema/capabilities.py` - AgentCapabilities schema (needs slash_commands field)

### BaseRegistry Pattern
- Uses `EventedDict` from psygnal for event emission
- Events available: adding, added, removing, removed, changing, changed
- Key methods: register(), get(), startup(), shutdown()
- Item validation via `_validate_item()` abstract method

### Slashed Library (OpenCode Commands)
- Used extensively across the codebase
- Key imports: `Command`, `CommandContext`, `CommandStore`, `BaseCommand`
- Commands have: name, description, category, execute() method
- CommandStore holds registered commands

### ACP Schema
- `AvailableCommand` has: name, description, input (AvailableCommandInput)
- Input hint stored in `CommandInputHint`
- `AgentCapabilities` is where slash_commands will be added

## Conventions

### Python Standards
- Python 3.13+ with modern syntax (match/case, walrus operator)
- Type hints required
- Google-style docstrings (no types in Args)
- `from __future__ import annotations`

### File Naming
- Core skills: `src/agentpool/skills/`
- Config: `src/agentpool_config/`
- Server bridges: `src/agentpool_server/{server_type}_server/`
- Tests: `tests/{module}/test_{feature}*.py`

## Dependencies

### Wave 1 (Independent)
- Task 1: registry.py modification (adds event system)
- Task 2: New file command.py (SkillCommand dataclass)
- Task 3: New file skill_commands.py (config schema)

### Wave 2 (Depends on Wave 1)
- Task 4: command_registry.py (SkillCommandRegistry extends BaseRegistry)
- Task 5: Broadcasting callbacks
- Task 6: Filesystem watcher integration

### Wave 3 (ACP - depends on Wave 2)
- Task 7: ACP schema update
- Task 8: ACPSkillBridge
- Task 9: ACP server integration

### Wave 4 (AG-UI - depends on Wave 2)
- Task 10: AGUISkillToolAdapter
- Task 11: AGUISkillBridge
- Task 12: AG-UI server integration

### Wave 5 (OpenCode - depends on Wave 2)
- Task 13: SkillCommandWrapper (extends slashed.Command)
- Task 14: OpenCodeSkillBridge
- Task 15: CommandStore registration
- Task 16: Server/routes integration

### Wave 6 (Integration)
- Task 17: AgentPool.skill_commands property
- Task 18: Auto-enable bridges
- Task 19: Error handling and logging

## Key Patterns to Follow

1. **Registry Pattern**: Extend BaseRegistry, implement _validate_item
2. **Event System**: Use callback pattern, notify existing state on subscription
3. **Graceful Degradation**: Accept Optional dependencies, check with has_* properties
4. **Bridging**: Convert from internal format to protocol-specific format
5. **Config Schema**: Pydantic models with defaults in src/agentpool_config/
