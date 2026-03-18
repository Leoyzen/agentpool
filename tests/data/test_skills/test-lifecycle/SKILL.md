---
name: test-lifecycle
description: A skill for testing the complete lifecycle from discovery through removal across all protocols
license: MIT
compatibility: 1.0.0
allowed-tools: bash, read, write
metadata:
  category: lifecycle-testing
  persistence: stateful
---

# test-lifecycle

A skill for testing the complete lifecycle management

## License
MIT

## Compatibility
1.0.0

## Allowed Tools
bash, read, write

## Instructions

This skill is used to verify the complete lifecycle of skill management:
- Discovery from filesystem
- Registration in SkillsRegistry
- Sync to SkillCommandRegistry
- Exposure in protocol bridges
- Live updates when modified
- Proper cleanup on removal

### Lifecycle Testing Scenarios

1. **Discovery Phase**:
   - Skill is discovered from filesystem
   - SKILL.md is parsed correctly
   - Metadata is extracted accurately

2. **Registration Phase**:
   - Skill is added to SkillsRegistry
   - Events are fired correctly
   - Command is created in SkillCommandRegistry

3. **Protocol Exposure Phase**:
   - ACP bridge exposes AvailableCommand
   - AG-UI bridge exposes Tool
   - OpenCode bridge exposes Command

4. **Update Propagation Phase**:
   - Changes to skill file propagate
   - All protocols receive updates
   - No stale references remain

5. **Removal Phase**:
   - Skill is removed from all registries
   - Protocol bridges clean up
   - No orphaned references

### Expected Behavior

When this skill is loaded:
- It should appear consistently across all protocols
- Updates should propagate immediately
- Removal should clean up all references

When this skill is updated:
- Description changes should reflect immediately
- New metadata should be available
- Protocol bridges should update representations

When this skill is removed:
- It should disappear from ACP commands
- It should disappear from AG-UI tools
- It should disappear from OpenCode commands
