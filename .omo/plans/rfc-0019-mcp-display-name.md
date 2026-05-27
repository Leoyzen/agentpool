# RFC-0019: MCP Server Display Name Separation from Client ID

## TL;DR

> **Quick Summary**: Implement a `display_name` property on MCP server config classes that returns the user-configured `name` if available, otherwise falls back to the auto-generated `client_id`. Use `display_name` for UI presentation while keeping `client_id` for internal unique identification.
> 
> **Deliverables**:
> - `display_name` property on `BaseMCPServerConfig` (covers all three config types)
> - Updated provider naming in `MCPManager` to use `display_name`
> - Updated MCP status API to return `display_name`
> - Comprehensive unit tests for all config types
> - Integration tests for API response
> - Updated documentation and comments
> 
> **Estimated Effort**: Medium (4-6 hours)
> **Parallel Execution**: YES - 2 waves (Core Implementation + Testing)
> **Critical Path**: Config property → Manager update → API update → Tests → Final Verification

---

## Context

### Original Request
Implement RFC-0019 to separate MCP Server Display Name from Client ID in AgentPool. Currently, MCP servers display with auto-generated identifiers like `pool_mcp_streamable_http_http://10.147.254.3:8721/mcp` instead of user-configured friendly names.

### Interview Summary
**Key Discussions**:
- RFC specifies Option 2: Separate `display_name` property (recommended approach)
- Keep `client_id` unchanged for internal operations (uniqueness, lookups)
- `display_name` is presentation-layer only, never used for identification

**Research Findings**:
- `name: str | None` field exists on `BaseMCPServerConfig` but is currently unused for display
- `client_id` property auto-generates unique IDs from command/args or URL
- Three config types: `StdioMCPServerConfig`, `SSEMCPServerConfig`, `StreamableHTTPMCPServerConfig`
- Internal lookups at `agent_routes.py:193, 214` MUST keep using `client_id`
- Comment at `agent_routes.py:149` acknowledges "custom names not supported" - needs update

### Metis Review
**Identified Gaps** (addressed in plan):
- [Gap 1] Whitespace-only names: Use `self.name and self.name.strip()` for robust fallback
- [Gap 2] Empty string handling: Document that empty string triggers fallback to `client_id`
- [Gap 3] MCPStatus API: Add `display_name` as new field to avoid breaking existing consumers
- [Gap 4] Provider naming: Carefully evaluate manager.py:137 change for uniqueness impact
- [Gap 5] No existing tests for `client_id` - need comprehensive test coverage for new property

---

## Work Objectives

### Core Objective
Add a `display_name` property to MCP server configuration that enables user-defined names to appear in UI while preserving stable internal identifiers.

### Concrete Deliverables
- `display_name` property on `BaseMCPServerConfig` returning `self.name.strip() if self.name else self.client_id`
- Updated `MCPManager.setup_server()` to use `config.display_name` for provider naming
- Updated MCP status endpoint to return `display_name` in API response
- Comment at `agent_routes.py:149` updated to reflect custom names are now supported
- Unit tests: 7 test cases covering all config types and edge cases
- Integration tests: API response verification

### Definition of Done
- [ ] All unit tests pass: `uv run pytest tests/config/test_mcp_server_config.py -v`
- [ ] All integration tests pass: `uv run pytest tests/servers/opencode_server/test_mcp_routes.py -v`
- [ ] Type checking passes: `uv run mypy src/agentpool_config/mcp_server.py`
- [ ] Linting passes: `uv run ruff check src/agentpool_config/ src/agentpool/mcp_server/ src/agentpool_server/opencode_server/`
- [ ] Full test suite passes: `uv run pytest -x`

### Must Have
- [ ] `display_name` property working on all three config types
- [ ] Backward compatibility: configs without `name` continue to work (fallback to `client_id`)
- [ ] Internal lookups continue using `client_id` (no breakage)
- [ ] API response includes display name for UI consumption
- [ ] Test coverage for edge cases (None, empty string, whitespace-only)

### Must NOT Have (Guardrails)
- [ ] **NO** changes to `client_id` generation logic
- [ ] **NO** changes to internal lookup logic (lines 193, 214 in agent_routes.py)
- [ ] **NO** serialization of `display_name` to YAML (computed property only)
- [ ] **NO** uniqueness validation on `display_name`
- [ ] **NO** breaking changes to `MCPStatus` schema (add new field, don't modify existing `name` field behavior)
- [ ] **NO** refactoring of unrelated MCP code

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (pytest, existing MCP tests)
- **Automated tests**: TDD-style (Tests written first, then implementation)
- **Framework**: pytest with uv
- **TDD Flow**: Each task follows RED (failing test) → GREEN (implementation) → REFACTOR

### QA Policy
Every task includes agent-executed QA scenarios with evidence saved to `.sisyphus/evidence/`.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation - Config & Tests):
├── Task 1: Add display_name property to BaseMCPServerConfig
├── Task 2: Create unit tests for display_name property (all 3 config types)
└── Task 3: Create integration test file for MCP routes

Wave 2 (Implementation - Manager & API):
├── Task 4: Update MCPManager to use display_name for provider naming
├── Task 5: Update agent_routes.py MCP status endpoint
└── Task 6: Update comment at agent_routes.py:149

Wave 3 (Verification & Documentation):
├── Task 7: Run full test suite and fix any issues
├── Task 8: Type check and lint all modified files
└── Task 9: Update RFC document status to ACCEPTED

Wave FINAL (4 Parallel Reviews):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|------------|--------|
| 1 | — | 2, 4, 5 |
| 2 | — | 7 |
| 3 | — | 7 |
| 4 | 1 | 7 |
| 5 | 1 | 7 |
| 6 | 5 | 7 |
| 7 | 2, 3, 4, 5, 6 | 8, 9, F1-F4 |
| 8 | 7 | 9, F1-F4 |
| 9 | 7, 8 | F1-F4 |

### Agent Dispatch Summary

- **Wave 1**: **3** tasks — T1 → `quick`, T2 → `quick`, T3 → `quick`
- **Wave 2**: **3** tasks — T4 → `unspecified-high`, T5 → `quick`, T6 → `quick`
- **Wave 3**: **3** tasks — T7 → `unspecified-high`, T8 → `quick`, T9 → `quick`
- **FINAL**: **4** tasks — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [x] 1. Add display_name property to BaseMCPServerConfig

  **What to do**:
  - Add a `display_name` property to `BaseMCPServerConfig` class in `src/agentpool_config/mcp_server.py`
  - Property should return `self.name.strip() if self.name else self.client_id`
  - Handle edge cases: None, empty string, whitespace-only strings
  - Add proper docstring following Google style

  **Must NOT do**:
  - Do NOT add caching or computed fields - simple property only
  - Do NOT add validation logic (length limits, character restrictions)
  - Do NOT modify the existing `name` field or its type
  - Do NOT change `client_id` generation on any config type

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple property addition, no complex logic
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Tasks 4, 5 (depend on property existing)
  - **Blocked By**: None

  **References**:
  - `src/agentpool_config/mcp_server.py:143-146` - BaseMCPServerConfig class location
  - `src/agentpool_config/mcp_server.py:191-194` - StdioMCPServerConfig.client_id pattern to follow
  - `src/agentpool_config/mcp_server.py:55-60` - name field definition

  **Acceptance Criteria**:
  - [ ] Property added to `BaseMCPServerConfig` with correct signature
  - [ ] Property returns `self.name.strip() if self.name else self.client_id`
  - [ ] Docstring follows Google style without types in Args
  - [ ] Type checking passes: `uv run mypy src/agentpool_config/mcp_server.py`

  **QA Scenarios**:
  ```
  Scenario: Verify property exists and is accessible
    Tool: Bash (python REPL)
    Preconditions: None
    Steps:
      1. Run: uv run python -c "from agentpool_config.mcp_server import StdioMCPServerConfig; c = StdioMCPServerConfig(command='uv', args=['run']); print(hasattr(c, 'display_name'))"
    Expected Result: Output contains "True"
    Evidence: .sisyphus/evidence/task-1-property-exists.txt

  Scenario: Verify property returns client_id when name is None
    Tool: Bash (python REPL)
    Preconditions: None
    Steps:
      1. Run: uv run python -c "from agentpool_config.mcp_server import StdioMCPServerConfig; c = StdioMCPServerConfig(command='uv', args=['run']); print(c.display_name == c.client_id)"
    Expected Result: Output contains "True"
    Evidence: .sisyphus/evidence/task-1-fallback-works.txt
  ```

  **Evidence to Capture**:
  - [ ] task-1-property-exists.txt - Proof property is accessible
  - [ ] task-1-fallback-works.txt - Proof fallback to client_id works

  **Commit**: YES
  - Message: `feat(config): Add display_name property to BaseMCPServerConfig`
  - Files: `src/agentpool_config/mcp_server.py`

---

- [x] 2. Create unit tests for display_name property

  **What to do**:
  - Create new test file `tests/config/test_mcp_server_config.py`
  - Write tests for `display_name` property on all three config types:
    - `StdioMCPServerConfig`
    - `SSEMCPServerConfig`
    - `StreamableHTTPMCPServerConfig`
  - Test cases:
    1. display_name returns name when set
    2. display_name falls back to client_id when name is None
    3. display_name falls back when name is empty string ""
    4. display_name falls back when name is whitespace-only "   "
    5. display_name strips whitespace from name

  **Must NOT do**:
  - Do NOT test `client_id` generation (assume it works)
  - Do NOT add integration tests here (unit tests only)
  - Do NOT skip edge cases - test all 5 scenarios

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Unit tests, straightforward assertions
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: Task 7 (test execution)
  - **Blocked By**: None (write tests first for TDD)

  **References**:
  - `tests/toolsets/test_mcp_discovery.py:23-26` - Example config creation pattern
  - `tests/mcp_client/test_mcp_features.py:23-27` - Example with name field
  - `tests/servers/acp_server/test_mcp_integration.py:31-44` - Multiple config test pattern

  **Acceptance Criteria**:
  - [ ] Test file created at `tests/config/test_mcp_server_config.py`
  - [ ] 15 test functions (5 scenarios × 3 config types)
  - [ ] All tests initially fail (TDD - property doesn't exist yet)
  - [ ] Tests use descriptive names like `test_stdio_display_name_with_custom_name`

  **QA Scenarios**:
  ```
  Scenario: Verify tests are written and discoverable
    Tool: Bash
    Preconditions: None
    Steps:
      1. Run: uv run pytest tests/config/test_mcp_server_config.py --collect-only
    Expected Result: Output shows 15 tests collected
    Evidence: .sisyphus/evidence/task-2-tests-collected.txt

  Scenario: Verify tests fail before implementation (TDD)
    Tool: Bash
    Preconditions: Task 1 not yet complete
    Steps:
      1. Run: uv run pytest tests/config/test_mcp_server_config.py -v 2>&1 | head -30
    Expected Result: Tests fail with AttributeError for display_name
    Evidence: .sisyphus/evidence/task-2-tests-fail.txt
  ```

  **Evidence to Capture**:
  - [ ] task-2-tests-collected.txt - Proof 15 tests exist
  - [ ] task-2-tests-fail.txt - Proof TDD cycle started

  **Commit**: YES
  - Message: `test(config): Add unit tests for MCP server display_name property`
  - Files: `tests/config/test_mcp_server_config.py`

---

- [x] 3. Create integration test file for MCP routes

  **What to do**:
  - Create new test file `tests/servers/opencode_server/test_mcp_routes.py`
  - Write integration tests for MCP status endpoint:
    1. Test that API response includes display_name field
    2. Test display_name in response matches configured name
    3. Test display_name falls back to client_id when name not provided
  - Use FastAPI TestClient pattern (see existing server tests)

  **Must NOT do**:
  - Do NOT test internal lookup logic (keep using client_id)
  - Do NOT test actual MCP server connections (mock where needed)
  - Do NOT duplicate unit test coverage

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: API integration tests, standard patterns
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Task 7 (test execution)
  - **Blocked By**: None

  **References**:
  - `src/agentpool_server/opencode_server/routes/agent_routes.py:167-180` - MCP status endpoint
  - `src/agentpool_server/opencode_server/routes/agent_routes.py:143-165` - MCP add endpoint
  - Look for existing FastAPI test patterns in `tests/servers/`

  **Acceptance Criteria**:
  - [ ] Test file created at `tests/servers/opencode_server/test_mcp_routes.py`
  - [ ] Tests for MCP status endpoint response format
  - [ ] Tests verify display_name field presence
  - [ ] Tests use FastAPI TestClient

  **QA Scenarios**:
  ```
  Scenario: Verify integration test file exists
    Tool: Bash
    Preconditions: None
    Steps:
      1. Run: ls -la tests/servers/opencode_server/test_mcp_routes.py
    Expected Result: File exists
    Evidence: .sisyphus/evidence/task-3-file-exists.txt

  Scenario: Verify tests are discoverable
    Tool: Bash
    Preconditions: None
    Steps:
      1. Run: uv run pytest tests/servers/opencode_server/test_mcp_routes.py --collect-only
    Expected Result: Output shows tests collected
    Evidence: .sisyphus/evidence/task-3-tests-collected.txt
  ```

  **Evidence to Capture**:
  - [ ] task-3-file-exists.txt - Proof file created
  - [ ] task-3-tests-collected.txt - Proof tests are discoverable

  **Commit**: YES
  - Message: `test(server): Add integration tests for MCP routes display_name`
  - Files: `tests/servers/opencode_server/test_mcp_routes.py`

---

- [x] 4. Update MCPManager to use display_name for provider naming

  **What to do**:
  - Update `src/agentpool/mcp_server/manager.py` line 137
  - Change: `name=f"{self.name}_{config.client_id}"`
  - To: `name=f"{self.name}_{config.display_name}"`
  - Verify this doesn't break provider uniqueness (display_name can have duplicates, but provider name with manager prefix should be unique enough)

  **Must NOT do**:
  - Do NOT change provider lookup logic (keep using client_id for lookups)
  - Do NOT change any other references to `client_id` in the file
  - Do NOT modify the `MCPResourceProvider` class

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Needs careful review to ensure uniqueness isn't broken
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Task 1)
  - **Parallel Group**: Wave 2 (with Tasks 5, 6)
  - **Blocks**: Task 7 (test execution)
  - **Blocked By**: Task 1 (property must exist)

  **References**:
  - `src/agentpool/mcp_server/manager.py:137` - Provider name construction
  - `src/agentpool/mcp_server/manager.py:130-145` - setup_server method context
  - `src/agentpool_server/opencode_server/routes/agent_routes.py:214` - Provider lookup (MUST keep using client_id)

  **Acceptance Criteria**:
  - [ ] Line 137 updated to use `config.display_name`
  - [ ] No other changes to manager.py
  - [ ] Provider lookup still works (regression test)

  **QA Scenarios**:
  ```
  Scenario: Verify manager.py line 137 is updated
    Tool: Bash (grep)
    Preconditions: Task 1 complete
    Steps:
      1. Run: grep -n "display_name" src/agentpool/mcp_server/manager.py
    Expected Result: Line 137 shows display_name usage
    Evidence: .sisyphus/evidence/task-4-manager-updated.txt

  Scenario: Verify provider lookup still uses client_id (regression)
    Tool: Bash (grep)
    Preconditions: None
    Steps:
      1. Run: grep -n "client_id" src/agentpool_server/opencode_server/routes/agent_routes.py | grep -E "193:|214:"
    Expected Result: Lines 193 and 214 still reference client_id
    Evidence: .sisyphus/evidence/task-4-lookup-unchanged.txt
  ```

  **Evidence to Capture**:
  - [ ] task-4-manager-updated.txt - Proof manager.py uses display_name
  - [ ] task-4-lookup-unchanged.txt - Proof internal lookups unchanged

  **Commit**: YES
  - Message: `refactor(mcp): Use display_name for provider naming in MCPManager`
  - Files: `src/agentpool/mcp_server/manager.py`

---

- [x] 5. Update agent_routes.py MCP status endpoint

  **What to do**:
  - Update `src/agentpool_server/opencode_server/routes/agent_routes.py`
  - Add `display_name` field to `MCPStatus` response (line 178 area)
  - Keep existing `name` field unchanged (backward compatibility)
  - Update the response to include both fields:
    - `name`: Keep as `config.client_id` (existing behavior)
    - `display_name`: Add as `config.display_name` (new field)

  **Must NOT do**:
  - Do NOT change internal lookup logic (lines 193, 214)
  - Do NOT remove or change existing `name` field behavior
  - Do NOT change `MCPStatus` model definition elsewhere

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple API response modification
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Task 1)
  - **Parallel Group**: Wave 2 (with Tasks 4, 6)
  - **Blocks**: Task 7 (test execution)
  - **Blocked By**: Task 1 (property must exist)

  **References**:
  - `src/agentpool_server/opencode_server/routes/agent_routes.py:167-180` - MCP status endpoint
  - `src/agentpool_server/opencode_server/routes/agent_routes.py:178` - Current response using client_id
  - Look for `MCPStatus` model definition to understand schema

  **Acceptance Criteria**:
  - [ ] API response includes new `display_name` field
  - [ ] Existing `name` field unchanged (still uses client_id)
  - [ ] Both fields present in response

  **QA Scenarios**:
  ```
  Scenario: Verify API response includes display_name field
    Tool: Bash (curl or pytest)
    Preconditions: Server running or use TestClient
    Steps:
      1. Run: uv run pytest tests/servers/opencode_server/test_mcp_routes.py::test_mcp_status_includes_display_name -v
    Expected Result: Test passes
    Evidence: .sisyphus/evidence/task-5-api-test.txt

  Scenario: Verify response has both name and display_name
    Tool: Bash (python with TestClient)
    Preconditions: None
    Steps:
      1. Run: uv run python -c "
         from fastapi.testclient import TestClient;
         from agentpool_server.opencode_server.main import app;
         client = TestClient(app);
         # Test logic here - check response has both fields
         print('Both fields present')
         "
    Expected Result: Output shows both fields
    Evidence: .sisyphus/evidence/task-5-both-fields.txt
  ```

  **Evidence to Capture**:
  - [ ] task-5-api-test.txt - Proof API tests pass
  - [ ] task-5-both-fields.txt - Proof both fields in response

  **Commit**: YES
  - Message: `feat(api): Add display_name field to MCP status response`
  - Files: `src/agentpool_server/opencode_server/routes/agent_routes.py`

---

- [x] 6. Update comment at agent_routes.py:149

  **What to do**:
  - Update `src/agentpool_server/opencode_server/routes/agent_routes.py` line 149
  - Current comment: `# Note: client_id is auto-generated from command/url, custom names not supported`
  - Replace with accurate description of current behavior
  - New comment: `# Note: client_id is auto-generated for internal identification; display_name uses configured name if available`

  **Must NOT do**:
  - Do NOT remove comment entirely - keep documentation
  - Do NOT change unrelated comments
  - Do NOT add unnecessary verbosity

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple comment update
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Task 5)
  - **Parallel Group**: Wave 2 (with Tasks 4, 5)
  - **Blocks**: Task 7 (test execution)
  - **Blocked By**: Task 5 (related context)

  **References**:
  - `src/agentpool_server/opencode_server/routes/agent_routes.py:149` - Comment location

  **Acceptance Criteria**:
  - [ ] Comment updated with accurate description
  - [ ] No other changes to the file

  **QA Scenarios**:
  ```
  Scenario: Verify comment is updated
    Tool: Bash (sed/grep)
    Preconditions: None
    Steps:
      1. Run: sed -n '149p' src/agentpool_server/opencode_server/routes/agent_routes.py
    Expected Result: Line shows updated comment about display_name
    Evidence: .sisyphus/evidence/task-6-comment-updated.txt
  ```

  **Evidence to Capture**:
  - [ ] task-6-comment-updated.txt - Proof comment is accurate

  **Commit**: YES
  - Message: `docs: Update comment to reflect display_name support`
  - Files: `src/agentpool_server/opencode_server/routes/agent_routes.py`

---

- [x] 7. Run full test suite and fix any issues

  **What to do**:
  - Run the complete test suite: `uv run pytest -x`
  - Fix any failing tests
  - Ensure all new tests pass (unit + integration)
  - Verify no regressions in existing MCP-related tests
  - Run specific test files:
    - `uv run pytest tests/config/test_mcp_server_config.py -v`
    - `uv run pytest tests/servers/opencode_server/test_mcp_routes.py -v`
    - `uv run pytest tests/toolsets/test_mcp_discovery.py -v`
    - `uv run pytest tests/mcp_client/test_mcp_features.py -v`

  **Must NOT do**:
  - Do NOT skip failing tests - fix them
  - Do NOT ignore test warnings
  - Do NOT modify unrelated tests to make them pass

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Needs to analyze and fix test failures
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (must wait for Wave 2)
  - **Parallel Group**: Wave 3
  - **Blocks**: Tasks 8, 9, F1-F4
  - **Blocked By**: Tasks 2, 3, 4, 5, 6

  **References**:
  - `tests/config/test_mcp_server_config.py` - New unit tests
  - `tests/servers/opencode_server/test_mcp_routes.py` - New integration tests
  - `tests/toolsets/test_mcp_discovery.py` - Existing MCP tests
  - `tests/mcp_client/test_mcp_features.py` - Existing MCP tests

  **Acceptance Criteria**:
  - [ ] All unit tests pass (15 tests)
  - [ ] All integration tests pass
  - [ ] All existing MCP tests still pass
  - [ ] No test failures in full suite

  **QA Scenarios**:
  ```
  Scenario: Run new unit tests
    Tool: Bash
    Preconditions: Tasks 1-6 complete
    Steps:
      1. Run: uv run pytest tests/config/test_mcp_server_config.py -v
    Expected Result: All 15 tests pass
    Evidence: .sisyphus/evidence/task-7-unit-tests.txt

  Scenario: Run new integration tests
    Tool: Bash
    Preconditions: Tasks 1-6 complete
    Steps:
      1. Run: uv run pytest tests/servers/opencode_server/test_mcp_routes.py -v
    Expected Result: All integration tests pass
    Evidence: .sisyphus/evidence/task-7-integration-tests.txt

  Scenario: Run full test suite
    Tool: Bash
    Preconditions: Tasks 1-6 complete
    Steps:
      1. Run: uv run pytest -x 2>&1 | tail -20
    Expected Result: No failures, clean exit
    Evidence: .sisyphus/evidence/task-7-full-suite.txt
  ```

  **Evidence to Capture**:
  - [ ] task-7-unit-tests.txt - Proof unit tests pass
  - [ ] task-7-integration-tests.txt - Proof integration tests pass
  - [ ] task-7-full-suite.txt - Proof full suite passes

  **Commit**: YES (if fixes needed)
  - Message: `fix: Address test failures from display_name implementation`
  - Files: [any files that needed fixes]

---

- [x] 8. Type check and lint all modified files

  **What to do**:
  - Run type checking: `uv run mypy src/agentpool_config/mcp_server.py`
  - Run linting: `uv run ruff check src/agentpool_config/ src/agentpool/mcp_server/ src/agentpool_server/opencode_server/`
  - Fix any type errors or lint violations
  - Ensure code formatting: `uv run ruff format --check src/`

  **Must NOT do**:
  - Do NOT ignore type errors with `# type: ignore`
  - Do NOT ignore lint violations without justification
  - Do NOT modify unrelated files to fix issues

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Tool execution and minor fixes
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Task 7)
  - **Parallel Group**: Wave 3
  - **Blocks**: Tasks 9, F1-F4
  - **Blocked By**: Task 7

  **References**:
  - `pyproject.toml` - mypy and ruff configuration

  **Acceptance Criteria**:
  - [ ] mypy passes on all modified files
  - [ ] ruff check passes with no violations
  - [ ] ruff format check passes (or auto-format applied)

  **QA Scenarios**:
  ```
  Scenario: Run type checker
    Tool: Bash
    Preconditions: Tasks 1-7 complete
    Steps:
      1. Run: uv run mypy src/agentpool_config/mcp_server.py
    Expected Result: No errors, exit code 0
    Evidence: .sisyphus/evidence/task-8-mypy.txt

  Scenario: Run linter
    Tool: Bash
    Preconditions: Tasks 1-7 complete
    Steps:
      1. Run: uv run ruff check src/agentpool_config/ src/agentpool/mcp_server/ src/agentpool_server/opencode_server/
    Expected Result: No violations, exit code 0
    Evidence: .sisyphus/evidence/task-8-ruff.txt
  ```

  **Evidence to Capture**:
  - [ ] task-8-mypy.txt - Proof type checking passes
  - [ ] task-8-ruff.txt - Proof linting passes

  **Commit**: YES (if fixes needed)
  - Message: `style: Fix type and lint issues`
  - Files: [any files that needed fixes]

---

- [x] 9. Update RFC document status to ACCEPTED

  **What to do**:
  - Update `docs/rfcs/draft/RFC-0019-mcp-server-display-name-separation.md`
  - Change `status: DRAFT` to `status: ACCEPTED`
  - Update `last_updated` date
  - Fill in `Reviewers` and `Target Completion` fields
  - Add Decision Record section with implementation summary

  **Must NOT do**:
  - Do NOT change technical content of RFC
  - Do NOT move file location (keep in draft/)
  - Do NOT modify design decisions

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Documentation update
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Tasks 7, 8)
  - **Parallel Group**: Wave 3
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 7, 8

  **References**:
  - `docs/rfcs/draft/RFC-0019-mcp-server-display-name-separation.md` - RFC document
  - Look at other ACCEPTED RFCs for format reference

  **Acceptance Criteria**:
  - [ ] Status changed from DRAFT to ACCEPTED
  - [ ] last_updated date is current
  - [ ] Decision Record section populated
  - [ ] Reviewers field filled

  **QA Scenarios**:
  ```
  Scenario: Verify RFC status updated
    Tool: Bash (grep)
    Preconditions: None
    Steps:
      1. Run: grep "^status:" docs/rfcs/draft/RFC-0019-mcp-server-display-name-separation.md
    Expected Result: Shows "status: ACCEPTED"
    Evidence: .sisyphus/evidence/task-9-status-accepted.txt
  ```

  **Evidence to Capture**:
  - [ ] task-9-status-accepted.txt - Proof RFC status updated

  **Commit**: YES
  - Message: `docs: Mark RFC-0019 as ACCEPTED`
  - Files: `docs/rfcs/draft/RFC-0019-mcp-server-display-name-separation.md`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run test, check API response). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  
  **Verification Commands**:
  ```bash
  # Check Must Have items
  grep -n "display_name" src/agentpool_config/mcp_server.py
  grep -n "display_name" src/agentpool/mcp_server/manager.py
  grep -n "display_name" src/agentpool_server/opencode_server/routes/agent_routes.py
  
  # Check Must NOT Have items
  grep -n "display_name" src/agentpool_server/opencode_server/routes/agent_routes.py | grep -E "193:|214:" || echo "OK: Internal lookups unchanged"
  
  # Check evidence files
  ls -la .sisyphus/evidence/ | grep task-
  ```
  
  Output: `Must Have [5/5] | Must NOT Have [6/6] | Tasks [9/9] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `uv run ruff check src/` + `uv run mypy src/agentpool_config/mcp_server.py`. Review all changed files for: `as any`/`@ts-ignore`, empty catches, `print()` statements, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  
  **Verification Commands**:
  ```bash
  uv run ruff check src/agentpool_config/mcp_server.py src/agentpool/mcp_server/manager.py src/agentpool_server/opencode_server/routes/agent_routes.py
  uv run mypy src/agentpool_config/mcp_server.py
  uv run ruff format --check src/agentpool_config/mcp_server.py src/agentpool/mcp_server/manager.py src/agentpool_server/opencode_server/routes/agent_routes.py
  ```
  
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | TypeCheck [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state. Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration (features working together). Test edge cases: None, empty string, whitespace.
  
  **Verification Commands**:
  ```bash
  # Run all unit tests
  uv run pytest tests/config/test_mcp_server_config.py -v
  
  # Run all integration tests
  uv run pytest tests/servers/opencode_server/test_mcp_routes.py -v
  
  # Run all MCP-related tests
  uv run pytest tests/toolsets/test_mcp_discovery.py tests/mcp_client/test_mcp_features.py -v
  
  # Run full suite
  uv run pytest -x --tb=short 2>&1 | tail -30
  ```
  
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git log/diff). Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Detect cross-task contamination.
  
  **Verification Commands**:
  ```bash
  git diff --stat HEAD
  git diff HEAD -- src/agentpool_config/mcp_server.py
  git diff HEAD -- src/agentpool/mcp_server/manager.py
  git diff HEAD -- src/agentpool_server/opencode_server/routes/agent_routes.py
  ```
  
  Output: `Tasks [9/9 compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

| Commit | Message | Files |
|--------|---------|-------|
| 1 | `feat(config): Add display_name property to BaseMCPServerConfig` | `src/agentpool_config/mcp_server.py` |
| 2 | `test(config): Add unit tests for MCP server display_name property` | `tests/config/test_mcp_server_config.py` |
| 3 | `test(server): Add integration tests for MCP routes display_name` | `tests/servers/opencode_server/test_mcp_routes.py` |
| 4 | `refactor(mcp): Use display_name for provider naming in MCPManager` | `src/agentpool/mcp_server/manager.py` |
| 5 | `feat(api): Add display_name field to MCP status response` | `src/agentpool_server/opencode_server/routes/agent_routes.py` |
| 6 | `docs: Update comment to reflect display_name support` | `src/agentpool_server/opencode_server/routes/agent_routes.py` |
| 7 | `fix: Address test failures from display_name implementation` | [if needed] |
| 8 | `style: Fix type and lint issues` | [if needed] |
| 9 | `docs: Mark RFC-0019 as ACCEPTED` | `docs/rfcs/draft/RFC-0019-mcp-server-display-name-separation.md` |

---

## Success Criteria

### Verification Commands
```bash
# Type checking
uv run mypy src/agentpool_config/mcp_server.py

# Linting
uv run ruff check src/agentpool_config/ src/agentpool/mcp_server/ src/agentpool_server/opencode_server/

# Unit tests
uv run pytest tests/config/test_mcp_server_config.py -v

# Integration tests
uv run pytest tests/servers/opencode_server/test_mcp_routes.py -v

# Full test suite
uv run pytest -x
```

### Final Checklist
- [ ] `display_name` property exists on `BaseMCPServerConfig`
- [ ] Property returns `self.name.strip() if self.name else self.client_id`
- [ ] Unit tests pass (15 tests across 3 config types)
- [ ] Integration tests pass (API response verification)
- [ ] Provider naming uses `display_name` in manager.py
- [ ] API response includes `display_name` field
- [ ] Internal lookups still use `client_id` (lines 193, 214)
- [ ] Comment at line 149 updated
- [ ] RFC status changed to ACCEPTED
- [ ] All type checking passes
- [ ] All linting passes
- [ ] Full test suite passes

### Behavioral Verification
```python
# Test this manually to verify behavior:
from agentpool_config.mcp_server import StdioMCPServerConfig, SSEMCPServerConfig, StreamableHTTPMCPServerConfig

# Test 1: With custom name
config1 = StdioMCPServerConfig(name="My Server", command="uv", args=["run"])
assert config1.display_name == "My Server", f"Expected 'My Server', got {config1.display_name}"

# Test 2: Fallback to client_id
config2 = StdioMCPServerConfig(command="uv", args=["run"])
assert config2.display_name == config2.client_id, f"Expected {config2.client_id}, got {config2.display_name}"

# Test 3: Whitespace stripping
config3 = StdioMCPServerConfig(name="  Server Name  ", command="uv", args=["run"])
assert config3.display_name == "Server Name", f"Expected 'Server Name', got {config3.display_name}"

print("All behavioral tests pass!")
```

