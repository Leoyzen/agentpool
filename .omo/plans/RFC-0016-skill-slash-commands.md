# RFC-0016: Unified Skill-to-Slash Command Architecture - Implementation Plan

## TL;DR

> **Objective**: Implement unified exposure of Skills as Slash Commands across OpenCode, ACP, and AG-UI protocols per RFC-0016 v2.0 specification.
> 
> **Approach**: Unified Command Registry with Protocol Bridges (Option 2 from RFC)
> - Single `SkillCommandRegistry` watches SkillsRegistry and broadcasts changes
> - Three bridges map to protocol-native formats (slashed Commands, AvailableCommand[], Tools)
> - Graceful degradation when SkillsRegistry absent
>
> **Deliverables**: 
> - SkillCommand dataclass and SkillCommandRegistry infrastructure
> - ACPSkillBridge for ACP protocol integration
> - AGUISkillBridge for AG-UI protocol tools
> - OpenCodeSkillBridge for native slash commands
> - Integration with all three server implementations
> - Comprehensive test coverage (>80%) and observability hooks
>
> **Scope**: ~2000 LOC, 4 phases (25+ tasks), 6-8 waves of parallel execution
> **Critical Path**: SkillCommand → Registry → ACP Bridge → Server Integration → QA

---

## Context

### Original Request
Implement RFC-0016 to enable users to trigger Claude Code skills via intuitive slash command syntax (e.g., `/skill:python-expert`) across all three supported protocols (OpenCode, ACP, AG-UI).

### RFC Specification Highlights
- **Architecture**: Unified `SkillCommandRegistry` + Protocol Bridges
- **Command prefix**: `/skill:` by default (e.g., `/skill:my-skill`)
- **Protocols**: OpenCode (native slashed Commands), ACP (AvailableCommand[]), AG-UI (Tools)
- **Key constraint**: No changes to SKILL.md format (backward compatible)
- **Performance goal**: Command registration <50ms

### Metis Gap Analysis (Applied)
1. **SkillsRegistry event system** - ADDED to plan as Task 1 (prerequisite)
2. **Argument schema handling** - ADDED unified schema conversion logic
3. **AG-UI tool discovery** - ACCEPTED limitation (tools per-request per RFC)
4. **Opt-in mechanism** - ADDED `expose_as_command: true` flag to skill config

**Guardrails from Analysis:**
- Must NOT implement skill dependency resolution in this phase (out of scope)
- Must NOT implement skill editing UI (out of scope)
- Must NOT implement command timeout logic (use existing timeouts)

---

## Work Objectives

### Core Objective
Create a unified skill-to-slash command system that:
1. Automatically exposes discovered skills as protocol-native commands
2. Supports runtime discovery (skills added/removed without restart)
3. Works consistently across OpenCode, ACP, and AG-UI
4. Maintains backward compatibility with existing skill system

### Concrete Deliverables
| ID | Deliverable | Location |
|----|-------------|----------|
| D1 | SkillCommand dataclass | `src/agentpool/skills/command.py` |
| D2 | SkillCommandRegistry | `src/agentpool/skills/command_registry.py` |
| D3 | ACP Schema Update | `src/acp/schema/capabilities.py` |
| D4 | ACPSkillBridge | `src/agentpool_server/acp_server/commands/skill_commands.py` |
| D5 | AGUISkillBridge | `src/agentpool_server/agui_server/skill_tools.py` |
| D6 | OpenCodeSkillBridge | `src/agentpool_server/opencode_server/skill_bridge.py` |
| D7 | SkillsRegistry Event System | `src/agentpool/skills/registry.py` (modify) |
| D8 | Skill Config Schema | `src/agentpool_config/skill_commands.py` |
| D9 | Test Suite | `tests/skills/test_commands*.py`, `tests/server/*/test_skill_commands*.py` |

### Must Have
- [ ] Skills auto-register as slash commands on discovery
- [ ] Runtime updates propagate to all protocol bridges
- [ ] Command prefix `/skill:` by default
- [ ] Backward compatible (existing skill tool still works)
- [ ] Graceful degradation without SkillsRegistry
- [ ] All protocol bridges functional and integrated
- [ ] Test coverage >80%

### Must NOT Have (Guardrails)
- **M1**: MUST NOT implement skill dependency resolution (out of scope per Metis)
- **M2**: MUST NOT implement skill editing or dynamic skill creation
- **M3**: MUST NOT implement command timeout/logic (reuse existing)
- **M4**: MUST NOT require modifications to SKILL.md structure
- **M5**: MUST NOT implement comprehensive argument parsers per skill
- **M6**: MUST NOT implement skill versioning comparisons
- **M7**: MUST NOT require server restarts for skill changes (runtime only)

### Definition of Done
- User can type `/skill:skill-name args` in OpenCode TUI and skill loads
- ACP clients receive AvailableCommand list via capabilities
- AG-UI clients can invoke skills via tool calls
- Tests pass with >80% coverage
- No regressions in existing skill functionality

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest is the test runner)
- **Test approach**: TDD for new components, integration tests for bridges
- **Coverage target**: >80%
- **Test locations**: 
  - Unit tests: `tests/skills/test_command*.py`, `tests/skills/test_registry*.py`
  - Integration tests: `tests/server/acp/test_skill_commands*.py`, `tests/server/agui/test_skill_tools*.py`, `tests/server/opencode/test_skill_bridge*.py`

### Agent-Executed QA Scenarios (Per Task)
Every task includes concrete verify scenarios:
- **Unit tests**: Component-level assertions with mocks
- **Integration tests**: Bridge + server interaction verification
- **E2E tests**: Full flow from user input to skill execution

### QA Evidence
- Screenshots/logs saved to `.sisyphus/evidence/{task-id}-test.{log,png}`
- Test coverage reports generated and reviewed

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately - Prerequisites):
├── Task 1: Add SkillsRegistry event system [quick]
├── Task 2: Create SkillCommand dataclass [quick]  
└── Task 3: Add skill config schema with opt-in flag [quick]
       
Wave 2 (Foundation - After Wave 1):
├── Task 4: Create SkillCommandRegistry core [unspecified-high]
├── Task 5: Add command watching/broadcast [unspecified-high]
└── Task 6: Add filesystem watcher integration [quick]

Wave 3 (ACP Bridge - After Wave 2):
├── Task 7: ACP Schema: Add slash_commands field [quick]
├── Task 8: Create ACPSkillBridge [quick]
└── Task 9: Integrate bridge with ACP server [unspecified-low]

Wave 4 (AG-UI Bridge - After Wave 2):
├── Task 10: Create AGUISkillToolAdapter [quick]
├── Task 11: Create AGUISkillBridge [quick]
└── Task 12: Integrate bridge with AG-UI server [unspecified-low]

Wave 5 (OpenCode Bridge - After Wave 2):
├── Task 13: Create SkillCommandWrapper extending slashed.Command [quick]
├── Task 14: Create OpenCodeSkillBridge [unspecified-high]
├── Task 15: Implement CommandStore registration [quick]
└── Task 16: Integrate with agent routes & context injection [unspecified-high]

Wave 6 (Integration - After Waves 3-5):
├── Task 17: Add AgentPool skill_commands property [quick]
├── Task 18: Auto-enable bridges on server start [quick]
└── Task 19: Error handling and logging [unspecified-low]

Wave 7 (Tests - After Wave 6):
├── Task 20: Unit tests for SkillCommandRegistry [unspecified-high]
├── Task 21: Integration tests for ACP bridge [unspecified-high]
├── Task 22: Integration tests for AG-UI bridge [unspecified-high]
├── Task 23: Integration tests for OpenCode bridge [unspecified-high]
└── Task 24: End-to-end tests [deep]

Wave 8 (Documentation & Polish - After Wave 7):
├── Task 25: Add observability hooks for invocations [unspecified-low]
├── Task 26: Performance benchmarking [unspecified-low]
└── Task 27: Update documentation [writing]

Wave FINAL (Independent Reviews - after ALL tests pass):
├── Task F1: Plan compliance audit - oracle
├── Task F2: Code quality review - unspecified-high
├── Task F3: Test coverage verification - quick
└── Task F4: Scope fidelity check - deep

Critical Path Analysis:
- Longest path: Task 1 → Task 4 → Task 14 → Task 16 → Task 24 → F3
- Parallel speedup: ~60% (10 of 25 tasks in Wave 2-5 can run independently)
- Max concurrent: 4 (Waves 2-5)
```

### Dependency Matrix

| Task | Blocks | Blocked By |
|------|--------|------------|
| T1 (Registry Events) | T4, T5 | — |
| T2 (SkillCommand) | T4, T8, T13 | — |
| T3 (Config Schema) | T4, T8, T17 | — |
| T4 (Registry Core) | T5, T6, T7-T16 | T1, T2 |
| T5 (Watching) | T6 | T4 |
| T6 (FS Watch) | T19 | T5 |
| T7-T16 (Bridges) | T17-T19 | T4 |
| T17-T19 (Integration) | T20-T27 | T7-T16 |
| T20-T27 (Tests/Docs) | F1-F4 | T17-T19 |
| F1-F4 (Final Review) | — | T20-T27 |

---

## TODOs

### Wave 1: Prerequisites

- [x] 1. Add SkillsRegistry Event System

  **What to do**:
  Add event emission to SkillsRegistry for skill addition/removal so SkillCommandRegistry can watch for changes.
  
  1. Add `skill_added` and `skill_removed` events using asyncio signals or callback pattern
  2. Emit `skill_added(name, skill_instance)` when skill discovered
  3. Emit `skill_removed(name)` when skill deleted
  4. Maintain backward compatibility (emit noop for callbacks not registered)
  5. Add unit tests with mocked callbacks

  **Must NOT do**:
  - Do not break existing SkillsRegistry API
  - Do not add complexity to skill loading itself
  - Do not emit events during initialization batch (emit after batch complete)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - **Rationale**: Simple callback registration pattern, existing codebase patterns

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 2, 3)
  - **Blocks**: Tasks 4, 5, 6
  - **Blocked By**: None

  **References**:
  - Pattern: `src/agentpool/delegation/pool.py` - AgentPool lifecycle callbacks
  - API: `src/agentpool/skills/registry.py` - SkillsRegistry class structure
  - Test: `tests/skills/test_registry.py` - Existing registry tests

  **Acceptance Criteria**:
  - [ ] `SkillsRegistry.on_skill_added(callback)` method exists
  - [ ] `SkillsRegistry.on_skill_removed(callback)` method exists  
  - [ ] Callback receives `(name: str, skill: Skill)` when skill added
  - [ ] Callback receives `(name: str, skill: None)` when skill removed
  - [ ] `uv run pytest tests/skills/test_registry_events.py -v` passes with 5+ test cases

  **QA Scenarios**:
  ```
  Scenario: Callback fires when skill discovered
    Tool: Bash
    Preconditions: SkillsRegistry initialized with event handlers
    Steps:
      1. Register callback: registry.on_skill_added(lambda n, s: captured.append((n, s)))
      2. Simulate skill discovery: registry._handle_skill_file_change("new-skill", "added")
      3. Assert callback was invoked with ("new-skill", <Skill instance>)
    Expected Result: Callback invoked exactly once with correct args
    Evidence: .sisyphus/evidence/task-1-a-callback-fire.log

  Scenario: No events during batch initialization
    Tool: Bash
    Preconditions: Empty registry, 5 skills in filesystem
    Steps:
      1. Create counter, register: registry.on_skill_added(lambda n, s: counter.increment())
      2. Call await registry.initialize() (scans filesystem)
      3. Assert counter.count == 5 (one per skill, after batch completes)
    Expected Result: Exactly 5 callbacks fired after batch initialization
    Evidence: .sisyphus/evidence/task-1-b-batch-init.log

  Scenario: Backward compatibility - no errors without handlers
    Tool: Bash
    Preconditions: Registry with no event handlers registered
    Steps:
      1. Do NOT register any callbacks
      2. Add skill: registry.register("test", skill_instance)
      3. Assert no exceptions raised
    Expected Result: Operations succeed silently, no errors
    Evidence: .sisyphus/evidence/task-1-c-compat.log
  ```

  **Commit**: YES
  - Message: `feat(skills): Add SkillsRegistry event system for skill discovery/removal`

---

- [x] 2. Create SkillCommand Dataclass

  **What to do**:
  Create protocol-agnostic dataclass representing a skill as a slash command.

  1. Create `src/agentpool/skills/command.py` with `SkillCommand` dataclass
  2. Fields: `name`, `description`, `skill` (Skill), `input_hint`, `category="skill"`
  3. Add `is_valid_input(self, input_text) -> tuple[bool, str | None]` method
  4. Add docstrings per Google-style
  5. Make frozen dataclass for immutability

  **Must NOT do**:
  - Do not add protocol-specific fields
  - Do not implement execution logic (bridges handle that)
  - Do not import protocol-specific types

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1, 3)
  - **Blocks**: Tasks 4, 5, 8, 13
  - **Blocked By**: None

  **References**:
  - Model: `src/agentpool/skills/skill.py` - Skill class structure to reference
  - Style: `src/agentpool/messaging/models.py` - How dataclasses are structured

  **Acceptance Criteria**:
  - [ ] File created: `src/agentpool/skills/command.py`
  - [ ] Dataclass is `@dataclass(frozen=True)`
  - [ ] All required fields present with correct types
  - [ ] `uv run python -c "from agentpool.skills.command import SkillCommand; print('import ok')"` succeeds
  - [ ] Unit tests pass: `uv run pytest tests/skills/test_command.py -v` (5+ test cases)

  **QA Scenarios**:
  ```
  Scenario: Dataclass instantiation
    Tool: Bash (Python REPL)
    Preconditions: SkillCommand class available
    Steps:
      1. from agentpool.skills.command import SkillCommand
      2. cmd = SkillCommand(name="test", description="test cmd", skill=mock_skill)
      3. Assert: cmd.name == "test", cmd.category == "skill"
      4. Assert: cmd.input_hint is not None
    Expected Result: Instance created with defaults applied
    Evidence: .sisyphus/evidence/task-2-a-dataclass.log

  Scenario: Frozen dataclass immutability
    Tool: Bash (Python REPL)
    Preconditions: Valid SkillCommand instance
    Steps:
      1. cmd = SkillCommand(name="test", description="test", skill=mock_skill)
      2. Try: cmd.name = "new_name"
      3. Assert: FrozenInstanceError raised
    Expected Result: Immutable - mutation throws error
    Evidence: .sisyphus/evidence/task-2-b-frozen.log
  ```

  **Commit**: YES
  - Message: `feat(skills): Add SkillCommand dataclass for protocol-agnostic command representation`

---

- [x] 3. Add Skill Config Schema with Opt-in Flag

  **What to do**:
  Create config schema for skill slash command exposure with opt-in mechanism.

  1. Create `src/agentpool_config/skill_commands.py` with `SkillSlashConfig` class
  2. Fields: `enabled: bool = True`, `input_schema: dict | None = None`, `aliases: list[str] = []`
  3. Create `SkillCommandConfig` for per-skill overrides
  4. Add extensible config for `slash_command` metadata in skills
  5. Write tests validating config parsing

  **Must NOT do**:
  - Do not require SKILL.md format changes
  - Do not change existing agent config schema
  - Do not make opt-out required (opt-in is optional)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1, 2)
  - **Blocks**: Tasks 4, 8, 17
  - **Blocked By**: None

  **References**:
  - Schema pattern: `src/agentpool_config/manifest.py` - Schema definitions
  - Config pattern: `src/agentpool_config/agents.py` - AgentConfig structure

  **Acceptance Criteria**:
  - [ ] File created: `src/agentpool_config/skill_commands.py`
  - [ ] `SkillSlashConfig` with: enabled, allowed_agents, require_confirmation
  - [ ] `SkillCommandConfig` for global and per-skill config
  - [ ] Tests pass: `uv run pytest tests/config/test_skill_commands.py -v`

  **QA Scenarios**:
  ```
  Scenario: Config with default values
    Tool: Bash (Python REPL)
    Preconditions: SkillSlashConfig available
    Steps:
      1. from agentpool_config.skill_commands import SkillSlashConfig
      2. cfg = SkillSlashConfig()
      3. Assert: cfg.enabled == True, cfg.require_confirmation == False
      4. Assert: cfg.allowed_agents == []
    Expected Result: Defaults applied correctly
    Evidence: .sisyphus/evidence/task-3-a-defaults.log

  Scenario: Per-skill override parsing
    Tool: Bash (Python REPL)
    Preconditions: Config classes loaded
    Steps:
      1. cfg = SkillSlashConfig(enabled=False, require_confirmation=True)
      2. Assert: cfg.enabled == False
      3. Assert: cfg.require_confirmation == True
    Expected Result: Overrides apply correctly
    Evidence: .sisyphus/evidence/task-3-b-override.log
  ```

  **Commit**: YES
  - Message: `feat(config): Add skill command config schema with opt-in flag`

---

### Wave 2: Foundation (After Wave 1)

- [x] 4. Create SkillCommandRegistry Core

  **What to do**:
  Create the central registry that watches SkillsRegistry and maintains commands.

  1. Create `src/agentpool/skills/command_registry.py` with `SkillCommandRegistry` class
  2. Extend `BaseRegistry[str, SkillCommand]` for consistent registry pattern
  3. Constructor accepts `SkillsRegistry | None` for graceful degradation
  4. Implement `has_skills` and `has_commands` boolean properties
  5. Add error handling for missing skills gracefully

  **Must NOT do**:
  - Do not assume SkillsRegistry is always provided
  - Do not implement filesystem watching here (delegated to Task 6)
  - Do not implement change broadcasting here (delegated to Task 5)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - **Rationale**: Requires coordination of skills and commands, understanding of registry patterns

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Tasks 1, 2, 3)
  - **Parallel Group**: Sequential (Wave 2 starts after Wave 1 complete)
  - **Blocks**: Tasks 5, 6, 7-16
  - **Blocked By**: Tasks 1, 2, 3

  **References**:
  - Pattern: `src/agentpool/utils/baseregistry.py` - Target class to extend
  - Pattern: `src/agentpool/skills/registry.py` - Similar registry implementation
  - Usage: RFC-0016 Section "SkillCommandRegistry" - Full specification

  **Acceptance Criteria**:
  - [ ] File created: `src/agentpool/skills/command_registry.py`
  - [ ] Class extends `BaseRegistry[str, SkillCommand]`
  - [ ] `has_skills` property returns `bool` indicating if skills registry present
  - [ ] `has_commands` property returns `bool` indicating any commands registered
  - [ ] Gracefully handles `SkillsRegistry | None` in constructor
  - [ ] Unit tests pass: `uv run pytest tests/skills/test_command_registry_core.py -v`

  **QA Scenarios**:
  ```
  Scenario: Registry with skills source
    Tool: Bash (Python REPL)
    Preconditions: Mock SkillsRegistry with 3 skills
    Steps:
      1. registry = SkillCommandRegistry(skills_registry=mock_registry)
      2. Assert: registry.has_skills == True
      3. After manual registration: assert registry.has_commands == True
    Expected Result: Properties reflect state correctly
    Evidence: .sisyphus/evidence/task-4-a-with-skills.log

  Scenario: Registry without skills source (graceful degradation)
    Tool: Bash (Python REPL)
    Preconditions: None
    Steps:
      1. registry = SkillCommandRegistry(skills_registry=None)
      2. Assert: registry.has_skills == False
      3. registry.register("manual", mock_command)
      4. Assert: registry.has_commands == True
    Expected Result: Works without skills registry
    Evidence: .sisyphus/evidence/task-4-b-no-skills.log
  ```

  **Commit**: YES
  - Message: `feat(skills): Create SkillCommandRegistry core class`

---

- [x] 5. Add Command Watching and Broadcasting

  **What to do**:
  Implement callback registration and change broadcasting from registry.

  1. Add `on_command_change(handler: CommandChangeHandler)` method
  2. Define `CommandChangeHandler = Callable[[str, SkillCommand | None], None]`
  3. When command added: call handlers with (name, command)
  4. When command removed: call handlers with (name, None)
  5. Notify new handlers of existing commands on registration
  6. Add `_sync_commands()` method to sync with SkillsRegistry

  **Must NOT do**:
  - Do not leak skills internals through callbacks (use Command only)
  - Do not allow handler removal (register only, for lifecycle simplicity)
  - Do not forget to notify new handlers of existing state

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - **Rationale**: Event/callback coordination, needs test of multiple handlers

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 4)
  - **Parallel Group**: Sequential
  - **Blocks**: Tasks 6, 8, 10, 14
  - **Blocked By**: Task 4

  **References**:
  - Pattern: `src/agentpool/messaging/publisher.py` - Event subscription patterns
  - Callback pattern: Task 1 - SkillsRegistry events (mirror approach)

  **Acceptance Criteria**:
  - [ ] `on_command_change` method exists and accepts callable
  - [ ] New handlers receive notification of existing commands
  - [ ] Add command: handlers called with (name, command)
  - [ ] Remove command: handlers called with (name, None)
  - [ ] Multiple handlers supported
  - [ ] Tests pass: `uv run pytest tests/skills/test_command_registry_broadcast.py -v`

  **QA Scenarios**:
  ```
  Scenario: Handler receives add notification
    Tool: Bash (Python REPL)
    Preconditions: Registry with broadcast capability
    Steps:
      1. received = []
      2. registry.on_command_change(lambda n, c: received.append((n, c)))
      3. registry.register("test-cmd", test_command)
      4. Assert: received == [("test-cmd", test_command)]
    Expected Result: Handler invoked with correct data
    Evidence: .sisyphus/evidence/task-5-a-add-notify.log

  Scenario: Handler receives remove notification
    Tool: Bash (Python REPL)
    Preconditions: Registered command exists
    Steps:
      1. received = []
      2. registry.on_command_change(lambda n, c: received.append((n, c)))
      3. registry.unregister("test-cmd")
      4. Assert: received == [("test-cmd", None)]
    Expected Result: Handler invoked with None for removal
    Evidence: .sisyphus/evidence/task-5-b-remove-notify.log

  Scenario: New handler notified of existing commands
    Tool: Bash (Python REPL)
    Preconditions: Registry with 3 commands already registered
    Steps:
      1. calls = []
      2. registry.on_command_change(lambda n, c: calls.append(n))
      3. Assert: len(calls) == 3
      4. Assert: set(calls) == {"cmd1", "cmd2", "cmd3"}
    Expected Result: New handler receives existing state
    Evidence: .sisyphus/evidence/task-5-c-existing.log
  ```

  **Commit**: YES
  - Message: `feat(skills): Add command change broadcasting to SkillCommandRegistry`

---

- [x] 6. Add Filesystem Watcher Integration

  **What to do**:
  Integrate SkillCommandRegistry with SkillsRegistry events for runtime updates.

  1. Add `initialize()` method to SkillCommandRegistry
  2. Call `_sync_commands()` to populate initial commands
  3. Subscribe to SkillsRegistry events: `on_skill_added`, `on_skill_removed`
  4. When skill added: create SkillCommand, register, broadcast
  5. When skill removed: unregister, broadcast
  6. Handle dependency ordering (basic - just add in discovery order)

  **Must NOT do**:
  - Do not implement complex dependency resolution (out of scope, guardrail M1)
  - Do not block on Filesystem I/O (use async subscribe)
  - Do not double-register commands on re-sync

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - **Rationale**: Integration task, mainly connecting events to handlers

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Tasks 1, 5)
  - **Parallel Group**: Sequential
  - **Blocks**: Tasks 8, 10, 14 (bridge implementations)
  - **Blocked By**: Tasks 1 (registry events), 5 (broadcasting)

  **References**:
  - Pattern: Task 1 implementation for subscription approach
  - Sync logic: RFC-0016 `SkillCommandRegistry._sync_commands()` example
  - Usage: `await registry.initialize()` in pool setup

  **Acceptance Criteria**:
  - [ ] `initialize()` method exists and is async
  - [ ] Initial sync creates commands for all existing skills
  - [ ] Subscribed to SkillsRegistry events
  - [ ] Add/remove events propagate to registered commands
  - [ ] Tests pass: `uv run pytest tests/skills/test_command_registry_watch.py -v`

  **QA Scenarios**:
  ```
  Scenario: Initialize syncs existing skills
    Tool: Bash (Python REPL)
    Preconditions: SkillsRegistry with 5 skills
    Steps:
      1. registry = SkillCommandRegistry(skills_registry=mock_registry)
      2. await registry.initialize()
      3. Assert: registry.has_commands == True
      4. Assert: len(registry._items) == 5
    Expected Result: All skills have commands
    Evidence: .sisyphus/evidence/task-6-a-init-sync.log

  Scenario: Runtime skill addition
    Tool: Bash (Python REPL)
    Preconditions: Initialized, empty registry
    Steps:
      1. received = []
      2. registry.on_command_change(lambda n, c: received.append((n, c)))
      3. mock_registry.emit_skill_added("new-skill", new_skill_instance)
      4. Assert: len(received) == 1
      5. Assert: received[0][0] == "new-skill"
    Expected Result: New skill propagates to commands
    Evidence: .sisyphus/evidence/task-6-b-runtime-add.log

  Scenario: Runtime skill removal
    Tool: Bash (Python REPL)
    Preconditions: Registry with "test-skill" command
    Steps:
      1. assert "test-skill" in registry._items
      2. mock_registry.emit_skill_removed("test-skill")
      3. Assert: "test-skill" not in registry._items
    Expected Result: Skill command removed
    Evidence: .sisyphus/evidence/task-6-c-runtime-remove.log
  ```

  **Commit**: YES
  - Message: `feat(skills): Integrate SkillCommandRegistry with SkillsRegistry events`

---

### Wave 3: ACP Bridge (After Wave 2)

- [x] 7. ACP Schema: Add slash_commands Field

  **What to do**:
  Add `slash_commands` field to `AgentCapabilities` schema per RFC-0016.

  1. Modify `src/acp/schema/capabilities.py` to add `slash_commands: list[AvailableCommand]`
  2. Field should be `Field(default_factory=list)` for optional
  3. Add docstring explaining field purpose
  4. Ensure JSON schema is still valid
  5. Add unit tests for schema validation

  **Must NOT do**:
  - Do not break existing ACP protocol compatibility
  - Do not require slash_commands field (must be optional)
  - Do not change other capability fields

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 3 is independent of Wave 4-5)
  - **Parallel Group**: Wave 3 (ACP Bridge)
  - **Blocks**: Tasks 8, 9
  - **Blocked By**: None (schema change is independent)

  **References**:
  - Schema: `src/acp/schema/capabilities.py` - AgentCapabilities class
  - Pattern: `src/acp/schema/slash_commands.py` - AvailableCommand definition
  - RFC: Section "ACP Schema Changes" - Exact specification

  **Acceptance Criteria**:
  - [ ] `slash_commands` field added to AgentCapabilities
  - [ ] Type is `list[AvailableCommand] | None` with default `[]`
  - [ ] JSON schema validates with new field
  - [ ] Backward compatible (old clients without field still work)
  - [ ] Tests pass: `uv run pytest tests/acp/schema/test_capabilities.py -v`

  **QA Scenarios**:
  ```
  Scenario: Schema accepts available commands
    Tool: Bash (Python REPL)
    Preconditions: Updated schema
    Steps:
      1. from acp.schema.capabilities import AgentCapabilities
      2. cmd = AvailableCommand(name="test", description="test")
      3. caps = AgentCapabilities(slash_commands=[cmd])
      4. Assert: len(caps.slash_commands) == 1
    Expected Result: Slash commands accepted in capabilities
    Evidence: .sisyphus/evidence/task-7-a-schema.log

  Scenario: Backward compatibility
    Tool: Bash (Python REPL)
    Preconditions: Old format capabilities
    Steps:
      1. caps = AgentCapabilities()  # No slash_commands
      2. Assert: caps.slash_commands == []
      3. caps_json = caps.model_dump_json()
      4. Assert: "slash_commands" in json.loads(caps_json)
    Expected Result: Default empty list, serialization works
    Evidence: .sisyphus/evidence/task-7-b-compat.log
  ```

  **Commit**: YES
  - Message: `feat(acp): Add slash_commands field to AgentCapabilities schema`

---

- [x] 8. Create ACPSkillBridge

  **What to do**:
  Create bridge class mapping SkillCommand to ACP AvailableCommand.

  1. Create `src/agentpool_server/acp_server/commands/skill_commands.py`
  2. Create `ACPSkillBridge` class with `handle_change` method
  3. Store commands in dict: `name -> AvailableCommand`
  4. Implement `_to_acp_command(skill_cmd) -> AvailableCommand`
  5. Implement `get_available_commands() -> list[AvailableCommand]`

  **Must NOT do**:
  - Do not implement ACP protocol logic (handled by server)
  - Do not handle command execution (ACP just lists commands, execution via prompt)
  - Do not modify ACP schema here (done in Task 7)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 3 independent)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 9
  - **Blocked By**: Tasks 2, 7 (SkillCommand, ACP schema)

  **References**:
  - Schema: `src/acp/schema/slash_commands.py` - AvailableCommand
  - Pattern: RFC-0016 "ACP Bridge" code example
  - Usage: Will be called by ACP server capabilities endpoint

  **Acceptance Criteria**:
  - [ ] File created: `src/agentpool_server/acp_server/commands/skill_commands.py`
  - [ ] `ACPSkillBridge` class exists
  - [ ] `handle_change(name, command)` signature matches `CommandChangeHandler`
  - [ ] `get_available_commands()` returns list of AvailableCommand
  - [ ] Tests pass: `uv run pytest tests/server/acp/test_skill_commands.py -v`

  **QA Scenarios**:
  ```
  Scenario: Bridge converts SkillCommand to AvailableCommand
    Tool: Bash (Python REPL)
    Preconditions: SkillCommand and bridge available
    Steps:
      1. bridge = ACPSkillBridge()
      2. bridge.handle_change("test", skill_command)
      3. cmds = bridge.get_available_commands()
      4. Assert: len(cmds) == 1
      5. Assert: cmds[0].name == "test"
    Expected Result: Correct mapping to AvailableCommand
    Evidence: .sisyphus/evidence/task-8-a-conversion.log

  Scenario: Bridge handles command removal
    Tool: Bash (Python REPL)
    Preconditions: Bridge with "test" command
    Steps:
      1. bridge.handle_change("test", None)
      2. cmds = bridge.get_available_commands()
      3. Assert: len(cmds) == 0
    Expected Result: Command removed from list
    Evidence: .sisyphus/evidence/task-8-b-remove.log
  ```

  **Commit**: YES
  - Message: `feat(acp): Create ACPSkillBridge for command mapping`

---

- [x] 9. Integrate ACP Bridge with Server

  **What to do**:
  Connect ACPSkillBridge to ACP server's capabilities endpoint.

  1. Modify `src/agentpool_server/acp_server/server.py` or session.py
  2. Create bridge instance on server initialization
  3. Subscribe bridge to SkillCommandRegistry changes
  4. Include `slash_commands` in `AgentCapabilities` response
  5. Add graceful degradation if no skill commands enabled

  **Must NOT do**:
  - Do not modify ACP protocol handling (just add capability field)
  - Do not break existing capability negotiation
  - Do not apply if registry has no commands (graceful)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: []
  - **Rationale**: Moderate integration, need to understand ACP server flow

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 8)
  - **Parallel Group**: Sequential within Wave 3
  - **Blocks**: None
  - **Blocked By**: Task 8

  **References**:
  - Server: `src/agentpool_server/acp_server/server.py` - Server initialization
  - Pattern: Similar capability addition patterns in server
  - RFC: Section "ACP Server Integration"

  **Acceptance Criteria**:
  - [ ] ACP server includes slash_commands in capabilities when skills present
  - [ ] Bridge subscribed to registry changes via `on_command_change`
  - [ ] Graceful degradation: empty sl_commands if no skills configured
  - [ ] Integration tests pass: `uv run pytest -m integration tests/server/acp/test_skill_integration.py -v`

  **QA Scenarios**:
  ```
  Scenario: Capabilities include skill commands
    Tool: Bash (CLI with test server)
    Preconditions: ACP server running with skills configured
    Steps:
      1. Start server: agentpool serve-acp test_config.yml
      2. Send capabilities request to server
      3. Response includes slash_commands list with skill names
    Expected Result: Commands visible in capabilities
    Evidence: .sisyphus/evidence/task-9-a-capabilities.json

  Scenario: Graceful without skills
    Tool: Bash (CLI with test server)
    Preconditions: Server without skill_dirs configured
    Steps:
      1. Start server without skills config
      2. Send capabilities request
      3. Response includes slash_commands: [] (empty)
    Expected Result: Empty slash_commands, no errors
    Evidence: .sisyphus/evidence/task-9-b-no-skills.log
  ```

  **Commit**: YES
  - Message: `feat(acp): Integrate skill commands with ACP server capabilities`

---

### Wave 4: AG-UI Bridge (After Wave 2)

- [x] 10. Create AGUISkillToolAdapter
- [x] 11. Create AGUISkillBridge
- [x] 12. Integrate AG-UI Bridge with Server

  **What to do**:
  Connect AGUISkillBridge to AG-UI server's tool system.

  1. Modify `src/agentpool_server/agui_server/base.py` or agent adapter
  2. Initialize bridge in server setup
  3. Include skill tools in agent tool list
  4. Handle skill tool execution via `execute()` method
  5. Route tool calls to correct adapter

  **Must NOT do**:
  - Do not modify AG-UI protocol handling beyond tool injection
  - Do not break existing tool execution

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 11)
  - **Parallel Group**: Wave 4
  - **Blocks**: None
  - **Blocked By**: Task 11

  **References**:
  - Server: `src/agentpool_server/agui_server/base.py`
  - Pattern: Task 9 approach

  **Acceptance Criteria**:
  - [ ] Skill tools included in AG-UI agent response
  - [ ] `skill__{name}` tool calls invoke correct adapter
  - [ ] Integration tests pass: `uv run pytest -m integration tests/server/agui/test_skill_integration.py -v`

  **QA Scenarios**:
  ```
  Scenario: AG-UI includes skill tools
    Tool: Bash (curl to AG-UI server)
    Preconditions: Server running with skills
    Steps:
      1. curl /agent endpoint
      2. Assert response.tools contains skill__my-skill
    Expected Result: Tools visible in agent endpoint
    Evidence: .sisyphus/evidence/task-12-a-tools.json
  ```

  **Commit**: YES
  - Message: `feat(agui): Integrate skill tools with AG-UI server`

---

### Wave 5: OpenCode Bridge (After Wave 2)

- [x] 13. Create SkillCommandWrapper Extending slashed.Command
- [x] 14. Create OpenCodeSkillBridge
- [x] 15. Implement CommandStore Registration
- [x] 16. Integrate with Agent Routes and Context Injection

  **What to do**:
  Connect bridge to OpenCode server `GET /command` endpoint and context injection.

  1. Modify `src/agentpool_server/opencode_server/server.py`:
     - Initialize bridge
     - Subscribe to registry changes
  2. Modify `agent_routes.py`:
     - Update `list_commands` to include skill commands from CommandStore
  3. Implement `inject_skill_context()` in OpenCodeAgent
  4. Wire up skill loading in command execution

  **Must NOT do**:
  - Do not break existing OpenCode commands
  - Do not modify unrelated agent behavior
  - Do not duplicate context injection logic

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - **Rationale**: Complex server integration requiring deep OpenCode knowledge

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Tasks 14, 15)
  - **Parallel Group**: Wave 5
  - **Blocks**: None
  - **Blocked By**: Tasks 14, 15

  **References**:
  - Server: `src/agentpool_server/opencode_server/server.py`
  - Routes: `agent_routes.py` - GET /command endpoint
  - Agent: `src/agentpool/agents/opencode_agent.py` - Context injection
  - RFC: "OpenCode Server Integration" section

  **Acceptance Criteria**:
  - [ ] `GET /command` includes skill commands (category=skill)
  - [ ] `/skill:{name}` commands load skill instructions
  - [ ] Agent context updated via `inject_skill_context()`
  - [ ] Integration tests pass: `uv run pytest -m integration tests/server/opencode/test_skill_endtoend.py -v`

  **QA Scenarios**:
  ```
  Scenario: GET /command includes skills
    Tool: Bash (python test_client.py)
    Preconditions: Server running with skills
    Steps:
      1. response = client.get("/command")
      2. skill_cmds = [c for c in response.json() if c.name.startswith("skill:")]
      3. Assert: len(skill_cmds) > 0
    Expected Result: Skills appear in command list
    Evidence: .sisyphus/evidence/task-16-a-commands.json

  Scenario: Skill command loads and executes
    Tool: Bash (python test_client.py)
    Preconditions: Server running, /command returns skill:test
    Steps:
      1. Send: /skill:test arg1 arg2
      2. Assert: Response shows skill loaded
      3. Assert: Agent context includes skill instructions
    Expected Result: Full command flow works
    Evidence: .sisyphus/evidence/task-16-b-flow.log
  ```

  **Commit**: YES
  - Message: `feat(opencode): Integrate skill commands with server and agent routes`

---

### Wave 6: AgentPool Integration (After Waves 3-5)

- [x] 17. Add AgentPool skill_commands Property

  **What to do**:
  Expose `skill_commands` registry via AgentPool for server access.

  1. Modify `src/agentpool/delegation/pool.py`:
  2. Add `_skill_commands: SkillCommandRegistry` private field
  3. Add `skill_commands` property (read-only) returning the registry
  4. Initialize in `__aenter__` or startup
  5. Connect to `_skills` registry if present

  **Must NOT do**:
  - Do not break existing pool configuration
  - Do not require skills to be configured (graceful)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 6 parallel with other integrations)
  - **Parallel Group**: Wave 6
  - **Blocks**: Tasks 18, 19
  - **Blocked By**: Tasks 4, 6 (Registry)

  **References**:
  - Pool: `src/agentpool/delegation/pool.py` - AgentPool class
  - Pattern: Other pool resources like `_skills`, `_storage`

  **Acceptance Criteria**:
  - [ ] `AgentPool.skill_commands` property exists
  - [ ] Property returns `SkillCommandRegistry`
  - [ ] Registry auto-initialized with skills if configured
  - [ ] Works when no skills configured (empty registry)

  **QA Scenarios**:
  ```
  Scenario: Pool exposes skill_commands
    Tool: Bash (Python REPL)
    Preconditions: AgentPool with skills_registered
    Steps:
      1. async with AgentPool(config) as pool:
      2.   registry = pool.skill_commands
      3.   Assert: isinstance(registry, SkillCommandRegistry)
    Expected Result: Registry accessible
    Evidence: .sisyphus/evidence/task-17-a-property.log
  ```

  **Commit**: YES
  - Message: `feat(pool): Add AgentPool.skill_commands property`

---

- [x] 18. Auto-Enable Bridges on Server Start

  **What to do**:
  Automatically enable skill command bridges when servers start with skills.

  1. OpenCode Server: Check `pool.skill_commands.has_commands`, enable if True
  2. ACP Server: Same check in capabilities endpoint
  3. AG-UI Server: Same check in agent setup
  4. Add graceful skip logging if no skills

  **Must NOT do**:
  - Do not require explicit enable config
  - Do not crash if skills not configured

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 6)
  - **Parallel Group**: Wave 6
  - **Blocks**: Task 19
  - **Blocked By**: Tasks 9, 12, 16, 17

  **References**:
  - Server files: integration points from Tasks 9, 12, 16
  - Pattern: `if pool.skill_commands.has_commands:` condition

  **Acceptance Criteria**:
  - [ ] All 3 servers auto-enable when skills present
  - [ ] All 3 servers skip gracefully when skills absent
  - [ ] Log messages indicate state at startup

  **QA Scenarios**:
  ```
  Scenario: Servers auto-enable with skills
    Tool: Bash (pytest with server fixtures)
    Preconditions: Config with skill_dirs
    Steps:
      1. Start each server type
      2. Assert logs show "Skill commands enabled"
      3. Assert commands available in endpoints
    Expected Result: Auto-enable works
    Evidence: .sisyphus/evidence/task-18-a-auto.log
  ```

  **Commit**: YES (with Task 17)
  - Message: `feat(servers): Auto-enable skill command bridges on startup`

---

- [x] 19. Error Handling and Logging

  **What to do**:
  Add comprehensive error handling and observability logging.

  1. Add `get_logger(__name__)` to all new modules
  2. Log: skill discovery, command registration, errors, warnings
  3. Handle errors: skill load failures, bridge errors, exec errors
  4. Add graceful degradation throughout (already in design, ensure complete)
  5. Log at DEBUG for normal, INFO for registration, WARNING for issues

  **Must NOT do**:
  - Do not add redundant logging (avoid double logging same event)
  - Do not log sensitive skill content (only metadata)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Tasks 17, 18)
  - **Parallel Group**: Wave 6
  - **Blocks**: None
  - **Blocked By**: Tasks 17, 18

  **References**:
  - Logger: `src/agentpool/log.py` - get_logger function
  - Pattern: Other servers' logging approach

  **Acceptance Criteria**:
  - [ ] All new modules have proper logging
  - [ ] Errors caught and logged (not propagated as crashes)
  - [ ] DEBUG logs for registration flow
  - [ ] No PII in logs

  **QA Scenarios**:
  ```
  Scenario: Logging captures skill operations
    Tool: Bash (run with LOG_LEVEL=DEBUG)
    Preconditions: Server with skills
    Steps:
      1. Start server: LOG_LEVEL=DEBUG agentpool serve...
      2. Observe logs: "Registered skill command", "Skill command enabled"
      3. Trigger error (invalid skill file)
      4. Assert: Error logged, not crashed
    Expected Result: Proper observability
    Evidence: .sisyphus/evidence/task-19-a-logs.log
  ```

  **Commit**: YES
  - Message: `feat(skills): Add error handling and logging throughout`

---

### Wave 7: Test Suite (After Wave 6)

- [ ] 20. Unit Tests for SkillCommandRegistry

  **What to do**:
  Comprehensive unit tests for SkillCommand, SkillCommandRegistry, and event system.

  1. Create `tests/skills/test_command.py` - SkillCommand tests (frozen, validation)
  2. Create `tests/skills/test_command_registry_core.py` - Registry basics
  3. Create `tests/skills/test_command_registry_broadcast.py` - Event callbacks
  4. Create `tests/skills/test_command_registry_watch.py` - FS sync, async handling
  5. Create `tests/skills/test_command_integration.py` - End-to-end registry tests
  6. Target: >90% coverage for `src/agentpool/skills/command*.py`

  **Test Scenarios**:
  - SkillCommand: frozen dataclass, input validation
  - Registry: CRUD operations, graceful degradation
  - Broadcasting: single handler, multiple handlers, existing state notify
  - Watching: skill add/remove propagates, re-sync, edge cases
  - Integration: full flow from registry events to commands

  **Must NOT do**:
  - Do not test protocol bridges here (separate files)
  - Do not depend on actual filesystem for registry tests (mock)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - **Rationale**: Comprehensive testing requires thoroughness

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 7 parallel test writing)
  - **Parallel Group**: Wave 7A (Core)
  - **Blocks**: None
  - **Blocked By**: Tasks 1-6

  **References**:
  - Pattern: `tests/skills/test_registry.py` - Existing registry tests
  - Conftest: `tests/conftest.py` - TestModel, fixtures
  - Coverage: Run `uv run pytest --cov=src/agentpool/skills/` after

  **Acceptance Criteria**:
  - [ ] All 4 test files created with comprehensive tests
  - [ ] Coverage >90% for skill command modules
  - [ ] All tests pass: `uv run pytest tests/skills/test_command*.py -v`
  - [ ] Edge cases: empty registry, no skills, duplicates, errors

  **QA Scenarios**:
  ```
  Scenario: Test coverage meets target
    Tool: Bash
    Steps:
      1. uv run pytest tests/skills/test_command*.py --cov=src/agentpool/skills/ --cov-report=term
      2. Assert: command.py X% >= 90%
      3. Assert: command_registry.py X% >= 90%
    Expected Result: Coverage met
    Evidence: .sisyphus/evidence/task-20-a-coverage.txt
  ```

  **Commit**: YES
  - Message: `test(skills): Add unit tests for SkillCommandRegistry and command dataclass`

---

- [x] 21. Integration Tests for ACP Bridge

  **What to do**:
  Integration tests for ACPSkillBridge with mock ACP server.

  1. Create `tests/server/acp/test_skill_commands.py`
  2. Test: Command conversion from SkillCommand to AvailableCommand
  3. Test: handle_change add/remove/update scenarios
  4. Test: Server integration if possible (skip if no server fixture)
  5. Mock SkillsManager for skill loading

  **Test Scenarios**:
  - Basic conversion: Name, description mapping
  - Edge cases: Long descriptions, special characters in names
  - Lifecycle: Add skill → remove skill → add again

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 7 parallel)
  - **Parallel Group**: Wave 7B (ACP)
  - **Blocks**: None
  - **Blocked By**: Tasks 8, 9

  **References**:
  - Pattern: `tests/server/acp/` - Existing ACP server tests
  - ACP tests: Similar capability tests

  **Acceptance Criteria**:
  - [ ] Test file created with 5+ integration tests
  - [ ] Coverage >70% for `skill_commands.py`
  - [ ] Tests pass: `uv run pytest tests/server/acp/test_skill_commands.py -v`

  **Commit**: YES
  - Message: `test(acp): Add integration tests for ACPSkillBridge`

---

- [x] 22. Integration Tests for AG-UI Bridge

  **What to do**:
  Integration tests for AGUISkillBridge and tool adapter.

  1. Create `tests/server/agui/test_skill_tools.py`
  2. Test: AGUISkillToolAdapter.to_agui_tool() format
  3. Test: AGUISkillBridge handle_change and get_tools()
  4. Test: Tool execution flow if AG-UI server available
  5. Mock SkillsManager for execution

  **Test Scenarios**:
  - Tool format: OpenAI function schema validation
  - Name format: skill__{name} prefix
  - Execution: Arguments passed correctly

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 7 parallel)
  - **Parallel Group**: Wave 7C (AG-UI)
  - **Blocks**: None
  - **Blocked By**: Tasks 10, 11

  **References**:
  - Pattern: `tests/server/agui/` - Existing AG-UI server tests

  **Acceptance Criteria**:
  - [ ] Test file with comprehensive tool tests
  - [ ] Coverage >70% for `skill_tools.py`
  - [ ] Tests pass: `uv run pytest tests/server/agui/test_skill_tools.py -v`

  **Commit**: YES
  - Message: `test(agui): Add integration tests for AGUISkillBridge`

---

- [x] 23. Integration Tests for OpenCode Bridge

  **What to do**:
  Integration tests for OpenCodeSkillBridge and SkillCommandWrapper.

  1. Create `tests/server/opencode/test_skill_bridge.py`
  2. Test: SkillCommandWrapper name, category, execute
  3. Test: OpenCodeSkillBridge handle_change and command management
  4. Test: CommandStore registration via mock
  5. Test: Argument substitution logic ($1, $2, $ARGUMENTS)
  6. Create `tests/server/opencode/test_skill_endtoend.py` if server fixture available

  **Test Scenarios**:
  - Command creation: name format, category
  - Execution: skill loading, context injection (mocked)
  - Args: substitution for various formats

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 7 parallel)
  - **Parallel Group**: Wave 7D (OpenCode)
  - **Blocks**: None
  - **Blocked By**: Tasks 13, 14, 15

  **References**:
  - Pattern: `tests/server/opencode/` - Existing tests
  - slashed: Mock CommandContext for testing

  **Acceptance Criteria**:
  - [ ] Bridge tests created with thorough coverage
  - [ ] End-to-end tests if server fixture available
  - [ ] Coverage >70% for `skill_bridge.py`

  **Commit**: YES
  - Message: `test(opencode): Add integration tests for OpenCodeSkillBridge`

---

- [x] 24. End-to-End Tests

  **What to do**:
  Full flow tests across all three protocols with actual skill invocation.

  1. Create test skills in `tests/data/test_skills/`
  2. Create `tests/integration/test_skill_commands_e2e.py`
  3. Test ACP: Start server → query capabilities → verify commands present
  4. Test AG-UI: Start server → query tools → verify skill__name present
  5. Test OpenCode: Mock run → verify command registration
  6. Test cross-protocol consistency (same skills appear everywhere)

  **Test Skills**:
  - `hello_world` - Simple output skill
  - `test_with_args` - Skill accepting arguments
  - `test_lifecycle` - Skill to test add/remove

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []
  - **Rationale**: Complex integration requiring multiple systems

  **Parallelization**:
  - **Can Run In Parallel**: NO (last test task, comprehensive)
  - **Parallel Group**: Wave 7 FINAL
  - **Blocks**: Tasks 25, 26, 27
  - **Blocked By**: Tasks 20-23, all bridge implementation

  **References**:
  - Pattern: `tests/integration/` - Existing integration tests
  - Server fixtures: `tests/conftest.py` or server-specific conftest

  **Acceptance Criteria**:
  - [ ] E2E test file with cross-protocol tests
  - [ ] Tests demonstrate end-to-end flow (skills → commands → execution)
  - [ ] Tests pass: `uv run pytest -m integration tests/integration/test_skill_commands_e2e.py -v`

  **QA Scenarios**:
  ```
  Scenario: E2E skill discovery
    Tool: Bash (pytest with fixtures)
    Steps:
      1. Configure AgentPool with test skills directory
      2. Start ACP, AG-UI, OpenCode servers
      3. Query each: capabilities, tools, commands
      4. Assert: Same skill names appear in all
    Expected Result: Consistent cross-protocol behavior
    Evidence: .sisyphus/evidence/task-24-a-e2e.json
  ```

  **Commit**: YES
  - Message: `test(integration): Add end-to-end tests for skill slash commands`

---

### Wave 8: Documentation and Polish (After Wave 7)

- [x] 25. Add Observability Hooks for Invocations

  **What to do**:
  Add telemetry/observability for skill command usage tracking.

  1. Add hooks in command execution for:
     - Command invocation start (name, protocol, timestamp)
     - Command completion (duration, success/failure)
     - Error tracking (error type)
  2. Integrate with existing storage/analytics
  3. Add Logfire spans for skill command execution
  4. Track: skill_name, protocol, args_hash, duration_ms, success

  **Must NOT do**:
  - Do not track raw user input (hash arguments)
  - Do not create new storage schema (extend existing)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 8)
  - **Parallel Group**: Wave 8
  - **Blocks**: Task 27
  - **Blocked By**: Task 24 (all features done)

  **References**:
  - Observability: `src/agentpool/observability/` - Logfire integration
  - Storage: `src/agentpool/storage/` - Interaction tracking

  **Acceptance Criteria**:
  - [ ] Hooks added to all bridge execution paths
  - [ ] Logfire spans for skill command execution
  - [ ] Anonymized usage tracking

  **Commit**: YES
  - Message: `feat(observability): Add invocation tracking hooks for skill commands`

---

- [x] 26. Performance Benchmarking

  **What to do**:
  Benchmark skill command registration performance.

  1. Create `tests/performance/test_skill_performance.py`
  2. Benchmark: Command registration time (target <50ms for 100 commands)
  3. Benchmark: Skill discovery time (target <100ms for 50 skills)
  4. Benchmark: Bridge conversion overhead
  5. Run benchmarks and record results

  **Acceptance Criteria**:
  - [ ] Performance meets RFC targets:
    - Registration <50ms for 100 commands
    - Discovery <100ms for 50 skills
  - [ ] Benchmarks repeatable via pytest

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 8
  - **Blocks**: Task 27
  - **Blocked By**: Task 24

  **Commit**: YES
  - Message: `perf(skills): Add performance benchmarks`

---

- [x] 27. Update Documentation

  **What to do**:
  Update project documentation for skill slash commands.

  1. Create `docs/features/skill-commands.md`:
   - Feature overview
   - Protocol comparison
   - Configuration guide
   - Usage examples
  2. Update `docs/` index.md if needed
  3. Update README with skill command mention
  4. Update CHANGELOG.md

  **Content Outline**:
  - What are skill commands
  - How to enable (config syntax)
  - Usage per protocol (examples)
  - Troubleshooting

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 8
  - **Blocks**: None (last task)
  - **Blocked By**: All other tasks

  **References**:
  - Docs: `docs/` directory structure
  - RFC: Full specification with examples

  **Acceptance Criteria**:
  - [ ] Documentation file created with comprehensive guide
  - [ ] README mentions skill commands
  - [ ] CHANGELOG entry added

  **Commit**: YES
  - Message: `docs: Add documentation for skill slash commands`

---

## Final Verification Wave

### F1. Plan Compliance Audit — `oracle`
**What**: Verify all deliverables match plan specification
**Method**: 
1. Check each file exists at expected path
2. Verify all "Must Have" criteria are implemented
3. Verify no "Must NOT" violations exist
4. Check all TODOs are marked complete
**Output**: `Deliverables [N/N] | Must Have [N/N] | Must NOT [N/N] | VERDICT`

### F2. Code Quality Review — `unspecified-high`
**What**: Run all code quality checks
**Method**:
1. `uv run ruff check src/` — must be clean
2. `uv run mypy src/` — must pass
3. `uv run pytest -m unit --no-cov` — unit tests pass
4. Search for AI slop patterns (excessive comments, `Any`, `TODO` without issue ref)
**Output**: `Ruff [PASS/FAIL] | Mypy [PASS/FAIL] | Tests [N/N] | Quality [PASS/FAIL]`

### F3. Test Coverage Verification — `quick`
**What**: Verify >80% test coverage
**Method**:
1. `uv run pytest --cov-report=term` — check overall coverage
2. Check new files: `src/agentpool/skills/command.py`, `command_registry.py` — must be >80%
3. Check bridge files: `src/agentpool_server/*server/*skill*.py` — must be >70%
**Output**: `Overall X% | Core Files Y% | Bridges Z% | VERDICT`

### F4. Scope Fidelity Check — `deep`
**What**: Verify no scope creep, all changes accounted for
**Method**:
1. Run `git diff --name-only HEAD` — list all changed files
2. Verify each file is in deliverables table or test-only
3. Check for changes outside `src/agentpool/skills/`, `src/agentpool_config/`, `src/agentpool_server/`, `tests/`
4. Verify no skill dependency resolution (M1 guardrail)
5. Verify no skill editing UI (M2 guardrail)
**Output**: `Deliverables Match [YES/NO] | No Creep [YES/NO] | Guardrails [N/N] | VERDICT`

---

## Commit Strategy

| Commit | Description | Files |
|--------|-------------|-------|
| 1 | feat(skills): Add SkillsRegistry event system | `src/agentpool/skills/registry.py`, tests |
| 2 | feat(skills): Add SkillCommand dataclass and config schema | `src/agentpool/skills/command.py`, `src/agentpool_config/skill_commands.py` |
| 3 | feat(skills): Implement SkillCommandRegistry | `src/agentpool/skills/command_registry.py`, tests |
| 4 | feat(acp): Add slash_commands to AgentCapabilities schema | `src/acp/schema/capabilities.py` |
| 5 | feat(acp): Add ACPSkillBridge | `src/agentpool_server/acp_server/commands/skill_commands.py` |
| 6 | feat(acp): Integrate skill commands in ACP server | `src/agentpool_server/acp_server/server.py` |
| 7 | feat(agui): Add AGUISkillBridge | `src/agentpool_server/agui_server/skill_tools.py` |
| 8 | feat(agui): Integrate skill tools in AG-UI server | `src/agentpool_server/agui_server/*.py` |
| 9 | feat(opencode): Add OpenCodeSkillBridge | `src/agentpool_server/opencode_server/skill_bridge.py` |
| 10 | feat(opencode): Integrate skill commands in OpenCode server | `src/agentpool_server/opencode_server/server.py`, `agent_routes.py` |
| 11 | feat(pool): Add AgentPool.skill_commands property | `src/agentpool/delegation/pool.py` |
| 12 | feat(skills): Add observability hooks | `src/agentpool/skills/*.py` |
| 13 | test(skills): Add comprehensive test coverage | `tests/skills/`, `tests/server/*/` |
| 14 | docs: Update documentation for skill slash commands | `docs/` |
| 15 | chore: Performance optimization and polish | various |

---

## Success Criteria

### Verification Commands
```bash
# Code quality
uv run ruff check src/ && echo "✓ Ruff clean"
uv run mypy src/ && echo "✓ Mypy clean"

# Test coverage
uv run pytest --cov-report=term --cov=src/agentpool/skills/

# Integration tests
uv run pytest -m integration tests/server/acp/test_skill_commands.py
uv run pytest -m integration tests/server/agui/test_skill_tools.py
uv run pytest -m integration tests/server/opencode/test_skill_bridge.py

# E2E (manual QA)
# 1. Start ACP server with skills_dir configured
# 2. Connect client, verify slash commands in capabilities
# 3. Start OpenCode server, verify /skill:name works
# 4. Start AG-UI server, verify skill__name tool works
```

### Final Checklist
- [ ] All 9 deliverables exist at specified paths
- [ ] All 7 "Must Have" criteria met
- [ ] All 7 "Must NOT" guardrails respected
- [ ] Test coverage >80% for new code
- [ ] Ruff and Mypy checks pass
- [ ] Integration tests pass for all 3 protocols
- [ ] No scope creep detected in git diff
- [ ] Documentation updated

---

## Appendix: File Locations Reference

### Core Files
- `src/agentpool/skills/command.py` — SkillCommand dataclass
- `src/agentpool/skills/command_registry.py` — SkillCommandRegistry
- `src/agentpool/skills/registry.py` — Add event system to SkillsRegistry
- `src/agentpool_config/skill_commands.py` — Config schema with opt-in flag

### Protocol Bridges
- `src/agentpool_server/opencode_server/skill_bridge.py` — OpenCode bridge
- `src/agentpool_server/acp_server/commands/skill_commands.py` — ACP bridge
- `src/agentpool_server/agui_server/skill_tools.py` — AG-UI bridge

### Server Integration
- `src/agentpool/delegation/pool.py` — Add skill_commands property
- `src/agentpool_server/opencode_server/server.py` — OpenCode integration
- `src/agentpool_server/acp_server/server.py` — ACP integration
- `src/agentpool_server/agui_server/*.py` — AG-UI integration

### External Schemas
- `src/acp/schema/capabilities.py` — Add slash_commands field

### Tests
- `tests/skills/test_command*.py` — Command tests
- `tests/skills/test_registry*.py` — Registry tests
- `tests/server/acp/test_skill_commands*.py` — ACP bridge tests
- `tests/server/agui/test_skill_tools*.py` — AG-UI bridge tests
- `tests/server/opencode/test_skill_bridge*.py` — OpenCode bridge tests
