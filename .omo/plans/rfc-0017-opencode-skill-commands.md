# RFC-0017: OpenCode Command Endpoint Skill Support

## TL;DR

> **Quick Summary**: Modify the `/session/{id}/command` endpoint to support both MCP Prompts AND slashed Commands (including skill commands), fixing the current 404 error when skill commands are invoked via `/skill:name` syntax.
> 
> **Deliverables**:
> - Modified `session_routes.py` with dual execution path (CommandStore → MCP Prompts)
> - `ServerState.command_store` field for unified command storage
> - CommandStore initialization in server.py
> - Comprehensive test suite with 6+ QA scenarios
> 
> **Estimated Effort**: Medium (4 days as per RFC)
> **Parallel Execution**: YES - 4 tracks, 8 tasks
> **Critical Path**: Task 1 → Task 2 → Task 3 → Task 8

---

## Context

### Original Request
Implement RFC-0017 to enable skill commands exposed as slashed Commands to be executed through the `/session/{id}/command` endpoint, resolving the 404 Not Found error.

### Current State (Explored)
**What EXISTS**:
- `SkillCommandRegistry` and `SkillCommand` fully implemented at `/src/agentpool/skills/`
- `pool.skill_commands` property initialized in `AgentPool.__aenter__`
- `OpenCodeSkillBridge` creates slashed Commands with 'skill:' prefix from skill definitions
- `execute_command` endpoint exists but ONLY executes MCP prompts (lines 1095-1214)
- `BaseAgent._command_store` using `slashed.CommandStore` already exists

**What's MISSING**:
- `command_store` field in `ServerState` (only has `skill_bridge`)
- `CommandStore` initialization from skill bridge commands
- Modified `execute_command()` endpoint with dual-path execution
- `_execute_slashed_command()` helper function

### Research Findings

**CommandStore Usage Pattern (from ACP server)**:
- Located: `src/agentpool_server/acp_server/session.py:207-208`
- Pattern: `self.command_store: CommandStore = field(default_factory=CommandStore)`
- Execution: Creates `CommandContext`, calls `command.execute(ctx, args)`

**OpenCodeSkillBridge Pattern**:
- Located: `src/agentpool_server/opencode_server/skill_bridge.py:109-168`
- Creates slashed Commands via `create_skill_command()`
- Commands prefixed with 'skill:' namespace
- Has `get_commands()` to retrieve all registered commands

**Test Infrastructure**:
- Fixtures at `tests/servers/opencode_server/conftest.py`
- Pattern: Mock agent with mocked methods, real storage/todos/file_ops
- HTTP testing via `AsyncClient` with `ASGITransport`
- Event capture via `EventCapture` helper class

### Metis Review
**Identified Gaps** (addressed in this plan):
- Precedence logic (CommandStore before MCP prompts)
- Warning logging when command collision detected
- 6 QA scenarios covering happy path, fallback, precedence, error cases
- Edge cases: None command_store, execution failure, 404 handling

---

## Work Objectives

### Core Objective
Extend the `/session/{id}/command` endpoint to support slashed Commands from the `CommandStore` as primary execution path, with MCP Prompts as fallback, while maintaining 100% backward compatibility.

### Concrete Deliverables
- `ServerState.command_store: CommandStore | None` field added
- `CommandStore` initialized in server startup from `skill_bridge.get_commands()`
- `_execute_slashed_command()` helper function implemented
- `execute_command()` endpoint modified with dual execution path:
  1. Check `CommandStore` for slashed Commands (includes skills)
  2. Fall back to MCP Prompts via `list_prompts()`
  3. Return 404 if neither found
- Warning logged when both slashed command and MCP prompt exist with same name
- Comprehensive test suite with 80%+ coverage

### Definition of Done
- [ ] All 6 QA scenarios pass (agent-executable, zero human intervention)
- [ ] Backward compatibility verified: MCP prompts still work
- [ ] Precedence verified: slashed commands take priority over MCP prompts when both exist
- [ ] Error handling verified: 404 for unknown, 500 for execution failures
- [ ] `uv run pytest tests/servers/opencode/ -v` passes

### Must Have
- CommandStore integration into ServerState
- Modified execute_command with dual-path execution
- Precedence: CommandStore > MCP prompts
- Warning logging for command name collision
- Full test coverage (unit + integration)

### Must NOT Have (Guardrails from Metis)
- NO changes to GET /command endpoint (already correct)
- NO changes to CommandRequest/response schemas
- NO new endpoints (per RFC Option A decision)
- NO removal or modification of existing MCP prompt execution
- NO streaming support addition (RFC specifies sync)
- NO middleware, metrics, or telemetry (future enhancement)
- NO refactor of command system (keep focused changes)

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (pytest fixtures in conftest.py)
- **Automated tests**: Tests-after (feature first, then test)
- **Framework**: pytest with AsyncClient for HTTP testing
- **Test command**: `uv run pytest tests/servers/opencode/test_command_execution.py -v`

### QA Policy
Every task MUST include agent-executed QA scenarios. Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **API/Backend**: Use Bash (curl) for HTTP endpoint verification
- **Unit tests**: Use Bash (pytest with -k filter) for specific test functions
- **Integration**: Use Low-level execution via pytest fixtures

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately - State Integration):
├── Task 1: Add command_store field to ServerState
└── Task 2: Initialize CommandStore in server.py

Wave 2 (After Wave 1 - Helper Implementation):
├── Task 3: Implement _execute_slashed_command helper
└── Task 4: Add CommandContext creation utilities

Wave 3 (After Wave 2 - Endpoint Modification):
├── Task 5: Modify execute_command endpoint with precedence
└── Task 6: Add warning logging for command collision

Wave 4 (After Wave 3 - Testing):
├── Task 7: Create comprehensive test suite
└── Task 8: Verify backward compatibility and integration

Wave FINAL (After ALL tasks - Verification):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real integration QA (unspecified-high)
└── Task F4: Test coverage verification (unspecified-high)

Critical Path: T1 → T2 → T3 → T5 → T8 → F1-F4
Parallel Speedup: ~60% faster than sequential
Max Concurrent: 2-3 tasks (dependencies limited)
```

### Dependency Matrix

| Task | Blocks | Blocked By |
|------|--------|------------|
| T1 (ServerState field) | T2 | — |
| T2 (Initialize CommandStore) | T3, T4 | T1 |
| T3 (_execute_slashed_command) | T5, T6 | T2 |
| T4 (CommandContext utils) | T5 | T2 |
| T5 (Modify endpoint) | T7, T8 | T3, T4 |
| T6 (Warning logging) | T7 | T5 |
| T7 (Test suite) | F1-F4 | T5, T6 |
| T8 (Integration verify) | F1-F4 | T5, T6 |

### Agent Dispatch Summary

- **Wave 1**: 2 tasks → `quick` category (field addition, initialization)
- **Wave 2**: 2 tasks → `quick` category (helper function, utilities)
- **Wave 3**: 2 tasks → `unspecified-high` category (endpoint mod, logging)
- **Wave 4**: 2 tasks → `unspecified-high` category (test suite, integration)
- **Wave FINAL**: 4 tasks → `oracle` + `unspecified-high` + `deep` categories

---

## TODOs

Implementation + Test = ONE Task. Never separate.
EVERY task MUST have: Recommended Agent Profile + Parallelization info + QA Scenarios.

- [x] 1. Add command_store field to ServerState

  **What to do**:
  - Add `command_store: CommandStore | None = field(default=None)` to `ServerState` class
  - Import `CommandStore` from `slashed` (already used in base_agent.py)
  - Add type annotation with proper TYPE_CHECKING handling
  - Verify no circular imports introduced

  **Must NOT do**:
  - Initialize the CommandStore in this task (just add field)
  - Modify other ServerState fields
  - Add any logic or methods

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple field addition, no complex logic
  - **Skills**: []
    - No specific skills needed for field addition
  - **Skills Evaluated but Omitted**:
    - `uv-package-manager`: Not needed for field addition
    - `git-master`: Standard git workflow sufficient

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1)
  - **Parallel Group**: Wave 1 (with Task 2)
  - **Blocks**: Task 2
  - **Blocked By**: None (can start immediately)

  **References**:
  - Pattern reference: `src/agentpool_server/acp_server/session.py:207-208`
    ```python
    self.command_store: CommandStore = field(default_factory=CommandStore)
    ```
  - Type definition: `src/agentpool/agents/base_agent.py:314-316`
    ```python
    self._command_store: CommandStore = field(
        default_factory=CommandStore, init=False, repr=False
    )
    ```
  - Target file: `src/agentpool_server/opencode_server/state.py`
    - `ServerState` class defined at line ~54
    - Add field after existing fields

  **Acceptance Criteria**:
  - [ ] `command_store` field added to `ServerState` class
  - [ ] Type annotation: `CommandStore | None`
  - [ ] Default value: `field(default=None)`
  - [ ] No circular imports introduced
  - [ ] `mypy src/agentpool_server/opencode_server/state.py` passes

  **QA Scenarios**:
  ```
  Scenario: ServerState field added correctly
    Tool: Bash
    Preconditions: Source file exists
    Steps:
      1. grep -n "command_store" src/agentpool_server/opencode_server/state.py
      2. Verify field exists with correct type annotation
      3. Run mypy on the file
    Expected Result: Field found, mypy passes with no errors
    Failure Indicators: mypy errors about type annotations
    Evidence: .sisyphus/evidence/task-1-field-added.txt
  ```

  **Commit**: YES
  - Message: `feat(opencode): Add command_store field to ServerState`
  - Files: `src/agentpool_server/opencode_server/state.py`
  - Pre-commit: `uv run ruff check src/agentpool_server/opencode_server/state.py`

- [x] 2. Initialize CommandStore in OpenCode server

  **What to do**:
  - In `server.py`, after `skill_bridge` initialization (lines 122-128)
  - Create `CommandStore` instance
  - Register all commands from `skill_bridge.get_commands()`
  - Assign to `state.command_store`
  - Handle case when `pool.skill_commands` is None (graceful)

  **Must NOT do**:
  - Change skill_bridge initialization order
  - Add new endpoints or routes
  - Modify other state fields

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Straightforward initialization logic
  - **Skills**: []
    - Using basic Python patterns
  - **Skills Evaluated but Omitted**:
    - No other skills relevant

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1)
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Task 3, Task 4
  - **Blocked By**: Task 1 (needs ServerState.command_store field)

  **References**:
  - Current initialization: `src/agentpool_server/opencode_server/server.py:122-128`
    ```python
    if state.pool.skill_commands is not None:
        state.skill_bridge = OpenCodeSkillBridge()
        state.pool.skill_commands.on_command_change(state.skill_bridge.handle_change)
    ```
  - CommandStore import: `from slashed import CommandStore`
  - Skill bridge commands: `skill_bridge.get_commands()` returns list of slashed Commands
  - Pattern reference: `src/agentpool/agents/base_agent.py:314-316` for CommandStore usage

  **Acceptance Criteria**:
  - [ ] CommandStore initialized after skill_bridge setup
  - [ ] All commands from `skill_bridge.get_commands()` registered
  - [ ] `state.command_store` properly assigned
  - [ ] Graceful handling when `pool.skill_commands` is None
  - [ ] No errors on server startup

  **QA Scenarios**:
  ```
  Scenario: CommandStore initializes correctly with skills
    Tool: Bash
    Preconditions: Server with skills configured
    Steps:
      1. Start OpenCode server with test config containing skills
      2. Verify no errors in startup logs
      3. Check state.command_store is not None
    Expected Result: Server starts, command_store populated
    Failure Indicators: AttributeError or startup errors
    Evidence: .sisyphus/evidence/task-2-init-success.txt

  Scenario: CommandStore None when no skills
    Tool: Bash
    Preconditions: Server without skills configured
    Steps:
      1. Start OpenCode server with config having no skills
      2. Verify server starts successfully
      3. Check state.command_store is None (graceful)
    Expected Result: Server starts, command_store is None
    Failure Indicators: Server fails to start
    Evidence: .sisyphus/evidence/task-2-no-skills.txt
  ```

  **Commit**: YES (groups with T1)
  - Message: `feat(opencode): Initialize CommandStore from skill bridge`
  - Files: `src/agentpool_server/opencode_server/server.py`
  - Pre-commit: `uv run ruff check src/agentpool_server/opencode_server/server.py`

- [x] 3. Implement _execute_slashed_command helper

  **What to do**:
  - Create helper function `_execute_slashed_command()` in `session_routes.py`
  - Accept `state: ServerState` and `request: CommandRequest`
  - Get command from `state.command_store`
  - Create `CommandContext` with agent, output, working_dir
  - Parse arguments using simple `split()`
  - Execute `command.execute(ctx, args)`
  - Return `MessageWithParts` with result
  - Handle exceptions: `CommandNotFoundError`, execution errors

  **Must NOT do**:
  - Modify the endpoint itself (that's Task 5)
  - Add argument quoting or complex parsing (keep simple split)
  - Return streaming response (RFC specifies sync)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Helper function with clear pattern to follow
  - **Skills**: []
    - Using existing patterns from codebase
  - **Skills Evaluated but Omitted**:
    - No additional skills needed

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2)
  - **Parallel Group**: Wave 2 (with Task 4)
  - **Blocks**: Task 5
  - **Blocked By**: Task 2 (needs CommandStore initialized)

  **References**:
  - Pattern: `src/agentpool_server/acp_server/session.py:567-605`
    ```python
    async def execute_slash_command(self, command_str: str):
        store = self.command_store
        command = store.get_command(command_name)
        ctx = CommandContext(agent=self.agent, output=CommandOutput(), working_dir=self.cwd)
        result = await command.execute(ctx, args)
    ```
  - CommandContext: `slashed.context.CommandContext`
  - CommandOutput: `slashed.output.CommandOutput`
  - Request type: `src/agentpool_server/opencode_server/models/message.py:CommandRequest`
  - Response type: `MessageWithParts` with `TextPart`

  **Acceptance Criteria**:
  - [ ] Function signature: `async def _execute_slashed_command(state, request)`
  - [ ] Validates `state.command_store` is not None
  - [ ] Retrieves command using `state.command_store.get_command()`
  - [ ] Creates CommandContext with proper fields
  - [ ] Parses arguments with `request.arguments.split() if request.arguments else []`
  - [ ] Exectues command with await
  - [ ] Returns MessageWithParts on success
  - [ ] Raises HTTPException(404) for missing command
  - [ ] Raises HTTPException(500) for execution failure

  **QA Scenarios**:
  ```
  Scenario: Execute slashed command successfully
    Tool: pytest unit test
    Preconditions: Mock state with CommandStore
    Steps:
      1. Create MockCommandStore with MockSlashedCommand("test")
      2. Call _execute_slashed_command(mock_state, request)
      3. Assert result is MessageWithParts with assistant role
    Expected Result: Returns valid MessageWithParts
    Failure Indicators: Exception raised or wrong return type
    Evidence: .sisyphus/evidence/task-3-helper-test.txt

  Scenario: Handle missing command
    Tool: pytest unit test
    Preconditions: CommandStore without requested command
    Steps:
      1. Create MockCommandStore without "missing" command
      2. Call _execute_slashed_command with "missing"
      3. Assert HTTPException with status 404
    Expected Result: HTTPException(status_code=404)
    Failure Indicators: Wrong exception type or status
    Evidence: .sisyphus/evidence/task-3-missing-cmd.txt

  Scenario: Handle execution error
    Tool: pytest unit test
    Preconditions: CommandStore with failing command
    Steps:
      1. Create MockCommandStore with command that raises Exception
      2. Call _execute_slashed_command
      3. Assert HTTPException with status 500
    Expected Result: HTTPException(status_code=500)
    Failure Indicators: Exception propagates uncaught
    Evidence: .sisyphus/evidence/task-3-exec-error.txt
  ```

  **Commit**: YES
  - Message: `feat(opencode): Add _execute_slashed_command helper`
  - Files: `src/agentpool_server/opencode_server/routes/session_routes.py`
  - Pre-commit: `uv run pytest tests/unit/test_slash_helper.py -v` (create if needed)

- [x] 4. Create CommandContext utilities

  **What to do**:
  - Create utility function `_create_command_context(state)` in `session_routes.py`
  - Extract common CommandContext creation logic
  - Handle working directory resolution
  - Prepare for use by both helper and future extensions

  **Must NOT do**:
  - Add complex state management
  - Modify state during creation

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Simple utility function
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2)
  - **Parallel Group**: Wave 2 (with Task 3)
  - **Blocks**: Task 5
  - **Blocked By**: Task 2

  **References**:
  - CommandContext: `slashed.context.CommandContext`
  - Working dir: `state.agent.working_dir` or `state.working_dir`
  - Agent: `state.agent`

  **Acceptance Criteria**:
  - [ ] Utility function creates CommandContext
  - [ ] Proper working directory assignment
  - [ ] Used by `_execute_slashed_command`

  **QA Scenarios**:
  ```
  Scenario: CommandContext created correctly
    Tool: pytest unit test
    Preconditions: Valid server state
    Steps:
      1. Call _create_command_context(state)
      2. Assert returns CommandContext instance
      3. Verify working_dir matches state
    Expected Result: Valid CommandContext
    Evidence: .sisyphus/evidence/task-4-context-util.txt
  ```

  **Commit**: YES (can group with T3)
  - Message: `refactor(opencode): Extract CommandContext creation utility`
  - Files: `src/agentpool_server/opencode_server/routes/session_routes.py`

- [x] 5. Modify execute_command endpoint with dual execution path

  **What to do**:
  - Modify `execute_command()` endpoint in `session_routes.py:1095-1214`
  - Add precedence check at start of function:
    1. If `state.command_store` and command in store: execute slashed
    2. Else: fall back to existing MCP prompt logic
  - Check for command collision (both exist) and log warning
  - Update docstring to reflect dual behavior
  - Ensure all existing MCP prompt code remains unchanged

  **Must NOT do**:
  - Remove or break existing MCP prompt execution
  - Change function signature or return type
  - Modify CommandRequest schema
  - Add streaming support

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Critical endpoint modification, requires careful execution
  - **Skills**: []
    - Focus on correctness over complexity
  - **Skills Evaluated but Omitted**:
    - No additional skills needed

  **Parallelization**:
  - **Can Run In Parallel**: NO (must complete helper first)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 6, Task 7, Task 8
  - **Blocked By**: Task 3, Task 4 (need helper and utilities)

  **References**:
  - Current endpoint: `src/agentpool_server/opencode_server/routes/session_routes.py:1095-1214`
  - Precedence pattern from RFC:
    ```python
    if state.command_store and request.command in state.command_store:
        return await _execute_slashed_command(state, request)
    
    # Fall back to MCP prompts (existing code)
    prompts = await state.agent.tools.list_prompts()
    ```
  - Command store check: `command in state.command_store` (uses __contains__)
  - Warning log when collision: Check if prompt also exists

  **Acceptance Criteria**:
  - [ ] Endpoint checks CommandStore before MCP prompts
  - [ ] Precedence documented in docstring
  - [ ] All existing MCP prompt logic preserved
  - [ ] Warning logged when both command types exist (see Task 6)
  - [ ] 404 returned when command in neither system
  - [ ] Session validation happens first (unchanged)

  **QA Scenarios**:
  ```
  Scenario: Slashed command executed when in CommandStore
    Tool: pytest with AsyncClient
    Preconditions: Server with CommandStore containing "skill:test"
    Steps:
      1. POST /session/{id}/command with {"command": "skill:test"}
      2. Verify endpoint calls _execute_slashed_command
      3. Assert response 200 with assistant role
    Expected Result: Command executed, valid response
    Failure Indicators: 404 returned or wrong code path
    Evidence: .sisyphus/evidence/task-5-slash-executed.txt

  Scenario: MCP prompt fallback when not in CommandStore
    Tool: pytest with AsyncClient
    Preconditions: Server with MCP prompt but no CommandStore entry
    Steps:
      1. POST /session/{id}/command with {"command": "mcp:prompt"}
      2. Verify existing MCP logic executes
      3. Assert response 200 with assistant role
    Expected Result: MCP prompt executed successfully
    Failure Indicators: 404 returned or command not executed
    Evidence: .sisyphus/evidence/task-5-mcp-fallback.txt

  Scenario: 404 when command in neither system
    Tool: pytest with AsyncClient
    Preconditions: Server without command in either system
    Steps:
      1. POST /session/{id}/command with {"command": "unknown"}
      2. Assert response 404
    Expected Result: HTTP 404 with "Command not found: unknown"
    Failure Indicators: 200 returned or wrong error
    Evidence: .sisyphus/evidence/task-5-not-found.txt
  ```

  **Commit**: YES
  - Message: `feat(opencode): Add dual-path command execution (CommandStore > MCP)`
  - Files: `src/agentpool_server/opencode_server/routes/session_routes.py`
  - Pre-commit: `uv run pytest tests/servers/opencode/ -v -k command`

- [x] 6. Add warning logging for command collision

  **What to do**:
  - In `execute_command()`, before executing slashed command
  - Check if command also exists as MCP prompt
  - If both exist: log warning with logger.warning()
  - Message: "Both slashed command and prompt exist for '{name}'. Using slashed command."
  - Import logger from `agentpool.log` (pattern from base_agent.py)

  **Must NOT do**:
  - Change behavior (still use slashed command)
  - Add configuration for precedence
  - Fail or error on collision

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Simple logging addition
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 5)
  - **Parallel Group**: Wave 3 (after Task 5)
  - **Blocks**: Task 7, Task 8
  - **Blocked By**: Task 5 (needs modified endpoint)

  **References**:
  - Logger pattern: `src/agentpool/agents/base_agent.py`
    ```python
    from agentpool.log import get_logger
    logger = get_logger(__name__)
    ```
  - Warning message RFC:
    ```python
    logger.warning(
        "Both slashed command and prompt exist for '%s'. Using slashed command.",
        request.command
    )
    ```
  - MCP prompt check: `await state.agent.tools.list_prompts()`

  **Acceptance Criteria**:
  - [ ] Warning logged when command name collision detected
  - [ ] Log message includes command name
  - [ ] Logging uses proper logger (not print)
  - [ ] Warning occurs before slashed command execution
  - [ ] Behavior unchanged (slashed still executed)

  **QA Scenarios**:
  ```
  Scenario: Warning logged when both command types exist
    Tool: pytest with caplog
    Preconditions: Server with same name in both CommandStore and MCP prompts
    Steps:
      1. Setup state with collision: same name in both systems
      2. POST /command with colliding command name
      3. Check logs for warning message
    Expected Result: Warning logged: "Both slashed command and prompt exist..."
    Failure Indicators: No warning or wrong message
    Evidence: .sisyphus/evidence/task-6-warning-logged.txt
  ```

  **Commit**: YES (can group with T5)
  - Message: `feat(opencode): Log warning when command name collision detected`
  - Files: `src/agentpool_server/opencode_server/routes/session_routes.py`

- [x] 7. Create comprehensive test suite

  **What to do**:
  - Create `tests/servers/opencode_server/test_command_execution.py`
  - Tests covering:
    1. Slashed command execution (happy path)
    2. MCP prompt fallback (backward compatibility)
    3. Precedence verification (slashed > MCP)
    4. 404 for unknown command
    5. Graceful handling of None command_store
    6. Command execution failure handling
    7. Warning logging for collision
  - Use fixtures from `conftest.py`
  - Mock CommandStore, agent, tools as needed

  **Must NOT do**:
  - Test implementation details (test behavior, not code structure)
  - Skip error cases
  - Add human-verification steps

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Comprehensive test suite requiring careful coverage
  - **Skills**: []
    - Using existing pytest patterns
  - **Skills Evaluated but Omitted**:
    - No additional skills needed

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 5, Task 6)
  - **Parallel Group**: Wave 4
  - **Blocks**: Final verification
  - **Blocked By**: Task 5, Task 6 (need functionality to test)

  **References**:
  - Test fixtures: `tests/servers/opencode_server/conftest.py:server_state`
  - Pattern: `tests/servers/opencode_server/test_session_lifecycle.py`
  - Async test pattern: `pytest.mark.asyncio`
  - HTTP client: `AsyncClient` from `httpx`
  - Mock pattern: `unittest.mock.AsyncMock`, `unittest.mock.MagicMock`

  **Acceptance Criteria**:
  - [ ] Test file created at correct path
  - [ ] All 6+ QA scenarios implemented as pytest functions
  - [ ] Tests use proper fixtures and mocks
  - [ ] Tests pass: `uv run pytest tests/servers/opencode_server/test_command_execution.py -v`
  - [ ] Coverage >80% for modified session_routes.py sections

  **QA Scenarios**:
  ```
  Scenario: All unit tests pass
    Tool: Bash
    Preconditions: Implementation complete
    Steps:
      1. Run: uv run pytest tests/servers/opencode_server/test_command_execution.py -v
      2. Verify all tests PASS
      3. Check coverage report
    Expected Result: 100% test pass rate
    Failure Indicators: Any test failures
    Evidence: .sisyphus/evidence/task-7-tests-pass.txt

  Scenario: Test coverage adequate
    Tool: Bash (with coverage)
    Steps:
      1. Run: uv run pytest --cov=src/agentpool_server/opencode_server/routes/session_routes.py tests/
      2. Verify coverage for new code >80%
    Expected Result: Coverage above threshold
    Evidence: .sisyphus/evidence/task-7-coverage.txt
  ```

  **Commit**: YES
  - Message: `test(opencode): Add comprehensive command execution test suite`
  - Files: `tests/servers/opencode_server/test_command_execution.py`
  - Pre-commit: `uv run pytest tests/servers/opencode_server/test_command_execution.py`

- [x] 8. Verify backward compatibility and integration

  **What to do**:
  - Run full OpenCode server test suite
  - Verify existing MCP prompt tests still pass
  - Verify session lifecycle tests pass
  - Verify no regressions in /command endpoint for MCP-only usage
  - Run type checking: `uv run mypy src/agentpool_server/opencode_server/`
  - Run linting: `uv run ruff check src/agentpool_server/opencode_server/`

  **Must NOT do**:
  - Skip any existing tests
  - Ignore type errors

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Integration verification, final validation
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - No additional skills needed

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 5, Task 6, Task 7)
  - **Parallel Group**: Wave 4
  - **Blocks**: Final verification wave
  - **Blocked By**: Task 7 (needs tests to validate)

  **References**:
  - Test command: `uv run pytest tests/servers/opencode_server/ -v`
  - Type check: `uv run --no-group docs mypy src/agentpool_server/opencode_server/`
  - Lint: `uv run ruff check src/agentpool_server/opencode_server/`
  - Format: `uv run ruff format --check src/agentpool_server/opencode_server/`

  **Acceptance Criteria**:
  - [ ] All existing OpenCode server tests pass
  - [ ] MCP prompt backward compatibility verified
  - [ ] mypy passes with no errors
  - [ ] ruff check passes with no errors
  - [ ] Full test suite: `uv run pytest tests/servers/opencode_server/` passes

  **QA Scenarios**:
  ```
  Scenario: Full test suite passes
    Tool: Bash
    Preconditions: All implementation complete
    Steps:
      1. Run: uv run pytest tests/servers/opencode_server/ -v
      2. Verify 100% pass rate across all tests
      3. Check no new warnings or deprecations
    Expected Result: All tests PASS (100%)
    Failure Indicators: Any failing tests
    Evidence: .sisyphus/evidence/task-8-full-suite.txt

  Scenario: Type checking passes
    Tool: Bash
    Steps:
      1. Run: uv run --no-group docs mypy src/agentpool_server/opencode_server/
      2. Verify no type errors
    Expected Result: mypy clean exit (0)
    Failure Indicators: Type errors reported
    Evidence: .sisyphus/evidence/task-8-mypy.txt

  Scenario: Linting passes
    Tool: Bash
    Steps:
      1. Run: uv run ruff check src/agentpool_server/opencode_server/
      2. Verify no lint errors
    Expected Result: ruff clean exit (0)
    Failure Indicators: Lint violations
    Evidence: .sisyphus/evidence/task-8-ruff.txt
  ```

  **Commit**: YES
  - Message: `test(opencode): Verify backward compatibility and integration`
  - Files: All modified in this plan
  - Pre-commit: `duty lint` or full validation suite

---

## Final Verification Wave

> 4 review agents run in PARALLEL. ALL must APPROVE. Rejection → fix → re-run.

- [x] F1. **Plan Compliance Audit** — `oracle` ✅ **APPROVE** (6/6 Must Have, 6/6 Must NOT Have)
  Read the RFC and this plan. For each "Must Have" and "Must NOT Have": verify implementation exists. Check:
  - CommandStore field added to ServerState
  - CommandStore initialized in server.py
  - _execute_slashed_command() exists and is correct
  - execute_command() has dual-path with precedence
  - Warning logging for collision exists
  - All tests pass
  - Backward compatibility verified
  - Output: `Must Have [N/N] | Must NOT Have [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high` ✅ **PASS** (2 HIGH severity issues are PRE-EXISTING, not from RFC-0017 changes)
  Review all changed files:
  - Type safety: No `as any`, no missing type annotations
  - Error handling: Proper exception handling
  - Code style: Follow existing patterns
  - Complexity: No over-engineering
  - Output: `Quality [PASS/FAIL] | Issues [N] | VERDICT`

- [x] F3. **Real Integration QA** — `unspecified-high` ✅ **PASS** (7/7 scenarios)
  Test the actual implementation:
  - Start OpenCode server with test config
  - Execute skill command via curl/HTTP client
  - Verify 200 response (not 404)
  - Verify MCP prompt still works
  - Verify precedence (skill > MCP)
  - Output: `Integration [PASS/FAIL] | Scenarios [N/N] | VERDICT`

- [x] F4. **Test Coverage Verification** — `deep` ✅ **PASS** (7/7 QA scenarios, ~95%+ command execution coverage)
  Verify comprehensive test coverage:
  - All QA scenarios exist and pass
  - Edge cases covered
  - Error paths tested
  - Coverage >80% for new code
  - Output: `Coverage [PASS/FAIL] | % [X] | VERDICT`

---

## Commit Strategy

- **1**: `feat(opencode): Add command_store field to ServerState`
  - Files: `src/agentpool_server/opencode_server/state.py`

- **2**: `feat(opencode): Initialize CommandStore from skill bridge`
  - Files: `src/agentpool_server/opencode_server/server.py`

- **3**: `feat(opencode): Add _execute_slashed_command helper`
  - Files: `src/agentpool_server/opencode_server/routes/session_routes.py`

- **4**: `refactor(opencode): Extract CommandContext creation utility`
  - Files: `src/agentpool_server/opencode_server/routes/session_routes.py`

- **5**: `feat(opencode): Add dual-path command execution (CommandStore > MCP)`
  - Files: `src/agentpool_server/opencode_server/routes/session_routes.py`

- **6**: `feat(opencode): Log warning when command name collision detected`
  - Files: `src/agentpool_server/opencode_server/routes/session_routes.py`

- **7**: `test(opencode): Add comprehensive command execution test suite`
  - Files: `tests/servers/opencode_server/test_command_execution.py`

- **8**: `test(opencode): Verify backward compatibility and integration`
  - All modified files final check

---

## Success Criteria

### Verification Commands
```bash
# Run all OpenCode server tests
uv run pytest tests/servers/opencode_server/ -v

# Type check
uv run --no-group docs mypy src/agentpool_server/opencode_server/

# Lint
uv run ruff check src/agentpool_server/opencode_server/

# Full validation
duty lint
```

### Final Checklist
- [ ] CommandStore field added to ServerState
- [ ] CommandStore initialized in server startup
- [ ] _execute_slashed_command() helper implemented
- [ ] execute_command() has dual-path execution (CommandStore > MCP)
- [ ] Warning logged when command collision detected
- [ ] All new tests pass
- [ ] All existing tests pass (backward compatibility)
- [ ] mypy passes with no errors
- [ ] ruff check passes with no errors
- [ ] Coverage >80% for modified code
