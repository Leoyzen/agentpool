# RFC-0020: MCP Skills Resources Provider Protocol Implementation

## TL;DR

> **Quick Summary**: Implement MCP Skills Resources Provider Protocol support for AgentPool, enabling skills to be exposed via the `skill://` URI scheme while supporting both prompt-based and resource-based MCP skills.
>
> **Deliverables**:
> - Exception hierarchy for skills (`src/agentpool/skills/exceptions.py`)
> - URI resolver with skill:// scheme support (`src/agentpool/skills/uri_resolver.py`)
> - LocalResourceProvider for filesystem skills (`src/agentpool/resource_providers/local.py`)
> - Extended MCPResourceProvider with skill methods (`src/agentpool/resource_providers/mcp_provider.py`)
> - AggregatingResourceProvider skill aggregation (`src/agentpool/resource_providers/aggregating.py`)
> - Updated load_skill tool with URI support (`src/agentpool_toolsets/builtin/skills.py`)
> - AgentPool integration with skill resolver (`src/agentpool/delegation/pool.py`)
> - Comprehensive test coverage for all components
>
> **Estimated Effort**: Large (4 weeks)
> **Parallel Execution**: YES - 4 waves with dependencies
> **Critical Path**: T1 → T3 → T6 → T13 → T14 → T17 → F1-F4 → user okay

---

## Context

### Original Request

Implement RFC-0020 which adds MCP Skills Resources Provider Protocol support to AgentPool. This enables:
1. Consumption of MCP-exposed skills via `skill://` URI scheme
2. Dual MCP skill types: prompt-based and resource-based (FastMCP Skills Provider)
3. Unified skill access across local filesystem and MCP sources
4. Reference content access via URI paths
5. Integration with existing ResourceProvider infrastructure

### Interview Summary

**Key Discussions**:
- RFC-0020 has been reviewed by Metis, Momus, and Oracle
- Architecture decision: Extend ResourceProvider pattern (not create parallel SkillProvider)
- Dual MCP skill types confirmed: prompts and resources
- Integration approach: Extend existing SkillsManager, avoid parallel systems

**Research Findings**:
- ResourceProvider base class already has `get_skills()` method and `skills_changed` signal
- MCPResourceProvider currently returns empty list for `get_skills()` - needs implementation
- SkillsRegistry exists with `on_skill_added/removed` callbacks
- AggregatingResourceProvider lacks `get_skills()` override - needs to be added
- SkillsInstructionProvider (RFC-0008) exists but serves different purpose (prompt injection vs resource provision)
- ResourceInfo has `from_mcp_resource()` classmethod for MCP resource conversion

### Metis Review

**Identified Gaps** (addressed in plan):
- AggregatingResourceProvider.get_skills() missing - Added as Task 4
- MCPResourceProvider skill change callback - Derived from prompt/resource callbacks
- SkillsRegistry callback connection to signals - Handled in Task 5
- URI scheme handling - Implemented in Task 2
- Exception hierarchy - Created in Task 1

**Guardrails Applied**:
- MUST NOT modify SkillsInstructionProvider (different purpose)
- MUST follow existing ResourceProvider patterns
- MUST maintain backward compatibility with load_skill tool
- MUST use existing Signal pattern for change notifications

---

## Work Objectives

### Core Objective

Implement the complete MCP Skills Resources Provider Protocol as specified in RFC-0020, enabling AgentPool to discover, access, and resolve skills from both local filesystem and MCP servers using the unified `skill://` URI scheme.

### Concrete Deliverables

1. **Exception Hierarchy** (`src/agentpool/skills/exceptions.py`)
   - `SkillError` base class
   - `SkillNotFoundError` with available skills list
   - `ReferenceNotFoundError` for reference files
   - `SecurityError` for path traversal attempts
   - `ProviderError` for provider operation failures

2. **URI Resolver** (`src/agentpool/skills/uri_resolver.py`)
   - `ResolvedSkillURI` dataclass for parsing skill:// URIs
   - `SkillURIResolver` class for resolving URIs to skill content
   - Provider priority handling for skill name collisions
   - Full URI validation (RFC 3986 compliant)

3. **LocalResourceProvider** (`src/agentpool/resource_providers/local.py`)
   - Implements ResourceProvider interface for filesystem skills
   - Uses SkillsRegistry for skill discovery
   - Supports `references/` subdirectory access
   - LRU caching with TTL for skill listings
   - Path traversal protection with `Path.relative_to()`

4. **Extended MCPResourceProvider** (`src/agentpool/resource_providers/mcp_provider.py`)
   - `get_skills()` returning prompt-based + resource-based skills
   - `_get_prompt_skills()` for MCP prompt mapping
   - `_get_resource_skills()` for FastMCP Skills Provider protocol
   - `get_references()` and `read_reference()` for skill resources
   - `_on_skills_changed()` callback for change notifications

5. **AggregatingResourceProvider Extension** (`src/agentpool/resource_providers/aggregating.py`)
   - `get_skills()` override to aggregate from all child providers
   - Proper change signal propagation

6. **Updated load_skill Tool** (`src/agentpool_toolsets/builtin/skills.py`)
   - Support for skill:// URI format
   - Bare skill name resolution with provider priority
   - Reference content loading
   - Argument substitution ($1, $2, $@)

7. **AgentPool Integration** (`src/agentpool/delegation/pool.py`)
   - `skill_resolver` property with SkillURIResolver
   - `skill_provider` property with AggregatingResourceProvider
   - `_setup_skills_provider()` for initialization
   - SkillsManager extension with ResourceProvider

8. **Comprehensive Tests**
   - Unit tests for each component
   - Integration tests for full workflow
   - Mock MCP server tests for skill discovery
   - Edge case tests (encoding, traversal, empty paths)

### Definition of Done

- [ ] All 7 exception classes implemented and tested
- [ ] URI resolver handles all URI formats from RFC specification
- [ ] LocalResourceProvider passes security audit (path traversal protection)
- [ ] MCPResourceProvider discovers both prompt-based and resource-based skills
- [ ] AggregatingResourceProvider aggregates skills from all providers
- [ ] load_skill tool supports both old (name) and new (URI) formats
- [ ] All tests pass with >80% coverage
- [ ] No breaking changes to existing skill functionality

### Must Have

- Full skill:// URI scheme implementation per RFC specification
- Path traversal protection using `Path.relative_to()`
- LRU caching with TTL for skill listings (default 60s)
- Provider priority: local > MCP (registration order)
- Dual MCP skill types: prompt-based AND resource-based
- Backward compatibility with existing load_skill calls

### Must NOT Have (Guardrails)

- NO skill write operations (read-only MCP access)
- NO skill versioning or update mechanisms
- NO skill marketplace or discovery service
- NO changes to SKILL.md structure
- NO modifications to SkillsInstructionProvider (RFC-0008)
- NO VFSRegistry usage for skill:// URIs
- NO breaking changes to existing skill APIs

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed.

### Test Decision

- **Infrastructure exists**: YES (pytest, existing test patterns)
- **Automated tests**: TDD (RED-GREEN-REFACTOR for each component)
- **Framework**: pytest with TestModel for agent testing
- **Coverage target**: >80% for new code

### QA Policy

Every task MUST include agent-executed QA scenarios:

- **Unit tests**: Use pytest with assertions on return values
- **Integration tests**: Use TestModel from pydantic-ai
- **API tests**: Use Bash (curl) for MCP server endpoints
- **Security tests**: Verify path traversal protection with malicious inputs

Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation - Start Immediately):
├── Task 1: Exception hierarchy (exceptions.py)
├── Task 2: URI resolver (uri_resolver.py)
├── Task 3: AggregatingResourceProvider.get_skills()
└── Task 4: Tests for Wave 1

Wave 2 (Provider Implementations - After Wave 1):
├── Task 5: LocalResourceProvider (local.py)
├── Task 6: MCPResourceProvider skill methods
├── Task 7: Tests for Wave 2
└── Task 8: Integration tests for providers

Wave 3 (Tool & Pool Integration - After Wave 2):
├── Task 9: Update load_skill tool with URI support
├── Task 10: AgentPool integration
├── Task 11: SkillsManager extension
├── Task 12: Tests for Wave 3

Wave 4 (Documentation & Polish - After Wave 3):
├── Task 13: Update protocol bridges
├── Task 14: Documentation and examples
├── Task 15: Performance testing
└── Task 16: Security audit

Wave FINAL (Verification - After ALL tasks):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay

Critical Path: T1 → T2 → T5 → T9 → T10 → T13 → F1-F4 → user okay
Parallel Speedup: ~60% faster than sequential
Max Concurrent: 4 (Wave 1 & 2)
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|------------|--------|
| T1 | - | T4, T5, T6, T7 |
| T2 | - | T5, T9, T10 |
| T3 | - | T10 |
| T4 | T1 | - |
| T5 | T1, T2 | T7, T10 |
| T6 | T1 | T7, T10 |
| T7 | T5, T6 | - |
| T8 | T5, T6 | - |
| T9 | T2 | T12 |
| T10 | T2, T3, T5, T6 | T12 |
| T11 | T5 | T12 |
| T12 | T9, T10, T11 | - |
| T13 | T10 | T16 |
| T14 | - | T16 |
| T15 | T10 | T16 |
| T16 | T13, T14, T15 | F1-F4 |
| F1-F4 | ALL TASKS | - |

---

## TODOs

- [x] **1. Exception Hierarchy for Skills**

  **What to do**:
  Create `src/agentpool/skills/exceptions.py` with the complete exception hierarchy:
  - `SkillError` - Base exception class inheriting from `AgentPoolError`
  - `SkillNotFoundError` - Raised when skill cannot be found, with optional available skills list
  - `ReferenceNotFoundError` - Raised when skill reference file cannot be found
  - `SecurityError` - Raised on path traversal or other security violations
  - `ProviderError` - Raised when provider operation fails

  **Must NOT do**:
  - Do NOT use ToolError as base - use AgentPoolError
  - Do NOT add methods beyond __init__ and message formatting
  - Do NOT import heavy dependencies

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 4, 5, 6, 7
  - **Blocked By**: None

  **References**:
  - `src/agentpool/utils/baseregistry.py:AgentPoolError` - Base exception pattern
  - `src/agentpool/tools/exceptions.py:ToolError` - Existing exception hierarchy
  - RFC-0020 lines 276-315 - Exception specifications

  **Acceptance Criteria**:
  - [ ] All 5 exception classes created
  - [ ] Each exception has proper docstring
  - [ ] SkillNotFoundError accepts available skills list
  - [ ] All exceptions inherit from AgentPoolError

  **QA Scenarios**:
  ```
  Scenario: SkillNotFoundError with available skills
    Tool: Bash (python)
    Preconditions: exceptions.py exists
    Steps:
      1. from agentpool.skills.exceptions import SkillNotFoundError
      2. exc = SkillNotFoundError("test-skill", ["skill1", "skill2"])
      3. assert "test-skill" in str(exc)
      4. assert "skill1" in str(exc)
    Expected Result: Exception message contains skill name and available skills
    Evidence: .sisyphus/evidence/task-1-notfound.png

  Scenario: SecurityError basic
    Tool: Bash (python)
    Steps:
      1. from agentpool.skills.exceptions import SecurityError
      2. exc = SecurityError("Path traversal detected")
      3. assert "Path traversal" in str(exc)
    Expected Result: Exception properly stores and displays message
    Evidence: .sisyphus/evidence/task-1-security.png
  ```

  **Evidence to Capture**:
  - [ ] Screenshot of successful exception imports
  - [ ] Test output showing all exception types work

  **Commit**: YES
  - Message: `feat(skills): add SkillError exception hierarchy`
  - Files: `src/agentpool/skills/exceptions.py`
  - Pre-commit: `uv run ruff check src/agentpool/skills/exceptions.py`

---

- [x] **2. URI Resolver for skill:// Scheme**

  **What to do**:
  Create `src/agentpool/skills/uri_resolver.py` with:
  - `ResolvedSkillURI` dataclass (frozen) with fields: provider, skill_name, reference_path
  - `ResolvedSkillURI.parse()` classmethod for parsing skill:// URIs with validation
  - `_is_valid_provider_name()` helper function
  - `SkillURIResolver` class that resolves URIs using AggregatingResourceProvider
  - Provider priority handling for skill name collisions
  - Full URI validation (RFC 3986 compliant)

  **Must NOT do**:
  - Do NOT use VFSRegistry pattern (use ResourceProvider instead)
  - Do NOT allow path traversal (validate with ".." check)
  - Do NOT allow null bytes in paths

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 5, 9, 10
  - **Blocked By**: None

  **References**:
  - RFC-0020 lines 947-1300 - URI resolver specification
  - `src/agentpool/resource_providers/base.py` - AggregatingResourceProvider pattern
  - `src/agentpool/skills/exceptions.py` (Task 1) - Use SkillNotFoundError, SecurityError

  **Acceptance Criteria**:
  - [ ] ResolvedSkillURI.parse() handles all RFC-specified formats
  - [ ] Provider name validation (alphanumeric, hyphen, underscore, max 63 chars)
  - [ ] Path traversal detection (".." in path parts)
  - [ ] URL decoding of components
  - [ ] SkillURIResolver resolves bare names with priority

  **QA Scenarios**:
  ```
  Scenario: Parse basic skill URI
    Tool: Bash (python)
    Steps:
      1. from agentpool.skills.uri_resolver import ResolvedSkillURI
      2. parsed = ResolvedSkillURI.parse("skill://local/python-expert")
      3. assert parsed.provider == "local"
      4. assert parsed.skill_name == "python-expert"
      5. assert parsed.reference_path is None
    Expected Result: URI parsed correctly with all components extracted
    Evidence: .sisyphus/evidence/task-2-parse-basic.png

  Scenario: Parse URI with reference path
    Tool: Bash (python)
    Steps:
      1. parsed = ResolvedSkillURI.parse("skill://local/python-expert/references/guide.md")
      2. assert parsed.reference_path == "references/guide.md"
    Expected Result: Reference path correctly extracted
    Evidence: .sisyphus/evidence/task-2-parse-ref.png

  Scenario: Path traversal detection
    Tool: Bash (python)
    Steps:
      1. from agentpool.skills.exceptions import SecurityError
      2. try:
      3.     ResolvedSkillURI.parse("skill://local/skill/../../../etc/passwd")
      4.     assert False, "Should have raised SecurityError"
      5. except SecurityError as e:
      6.     assert "traversal" in str(e).lower()
    Expected Result: SecurityError raised on path traversal attempt
    Evidence: .sisyphus/evidence/task-2-traversal.png

  Scenario: URL decoding
    Tool: Bash (python)
    Steps:
      1. parsed = ResolvedSkillURI.parse("skill://local/my%20skill")
      2. assert parsed.skill_name == "my skill"
    Expected Result: URL-encoded characters properly decoded
    Evidence: .sisyphus/evidence/task-2-decode.png
  ```

  **Evidence to Capture**:
  - [ ] Screenshot of URI parsing tests passing
  - [ ] Evidence of path traversal protection working

  **Commit**: YES
  - Message: `feat(skills): add URI resolver for skill:// scheme`
  - Files: `src/agentpool/skills/uri_resolver.py`
  - Pre-commit: `uv run ruff check src/agentpool/skills/uri_resolver.py`

---

- [x] **3. AggregatingResourceProvider.get_skills()**

  **What to do**:
  Extend `src/agentpool/resource_providers/aggregating.py` to add:
  - `get_skills()` method that aggregates skills from all child providers
  - Proper deduplication based on skill name and provider
  - Change signal propagation for skills_changed

  **Must NOT do**:
  - Do NOT change existing tool/prompt/resource aggregation
  - Do NOT modify provider registration logic
  - Do NOT create new signal systems

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 10
  - **Blocked By**: None

  **References**:
  - `src/agentpool/resource_providers/aggregating.py` - Existing aggregation pattern
  - `src/agentpool/resource_providers/base.py:ResourceProvider.get_skills()` - Base method
  - RFC-0020 lines 166-167 - Architecture diagram

  **Acceptance Criteria**:
  - [ ] get_skills() aggregates from all child providers
  - [ ] Returns list[Skill] type
  - [ ] Properly handles async iteration
  - [ ] Skills from different providers with same name both included

  **QA Scenarios**:
  ```
  Scenario: Aggregate skills from multiple providers
    Tool: Bash (python)
    Preconditions: Mock providers created
    Steps:
      1. Create AggregatingResourceProvider with 2 mock providers
      2. Each mock returns 2 different skills
      3. aggregated = await provider.get_skills()
      4. assert len(aggregated) == 4
    Expected Result: All skills from all providers aggregated
    Evidence: .sisyphus/evidence/task-3-aggregate.png
  ```

  **Evidence to Capture**:
  - [ ] Test showing aggregation works
  - [ ] Coverage report for aggregating.py

  **Commit**: YES
  - Message: `feat(resource_providers): add get_skills() to AggregatingResourceProvider`
  - Files: `src/agentpool/resource_providers/aggregating.py`
  - Pre-commit: `uv run pytest tests/resource_providers/test_aggregating.py -v`

---

- [x] **4. Tests for Wave 1 Components**

  **What to do**:
  Create comprehensive tests for Wave 1 components:
  - `tests/skills/test_exceptions.py` - Test all exception classes
  - `tests/skills/test_uri_resolver.py` - Test URI parsing and resolution
  - `tests/resource_providers/test_aggregating_skills.py` - Test skill aggregation

  **Must NOT do**:
  - Do NOT use unittest.TestCase (use pytest functions)
  - Do NOT put tests in classes
  - Do NOT test implementation details, test behavior

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after T1, T2, T3)
  - **Parallel Group**: Wave 1
  - **Blocks**: None
  - **Blocked By**: Tasks 1, 2, 3

  **References**:
  - `tests/conftest.py` - Existing fixtures and patterns
  - `tests/resource_providers/test_aggregating.py` - Existing aggregating tests

  **Acceptance Criteria**:
  - [ ] All Wave 1 components have >80% test coverage
  - [ ] Tests follow existing pytest patterns
  - [ ] Tests include edge cases (empty inputs, invalid formats)

  **QA Scenarios**:
  ```
  Scenario: Run Wave 1 tests
    Tool: Bash
    Steps:
      1. uv run pytest tests/skills/test_exceptions.py tests/skills/test_uri_resolver.py -v
    Expected Result: All tests pass
    Evidence: .sisyphus/evidence/task-4-tests.png
  ```

  **Commit**: YES
  - Message: `test(skills): add tests for Wave 1 components`
  - Files: `tests/skills/test_exceptions.py`, `tests/skills/test_uri_resolver.py`, `tests/resource_providers/test_aggregating_skills.py`
  - Pre-commit: `uv run pytest tests/skills/test_exceptions.py tests/skills/test_uri_resolver.py -v`

---

- [x] **5. LocalResourceProvider for Filesystem Skills**

  **What to do**:
  Create `src/agentpool/resource_providers/local.py` implementing ResourceProvider for filesystem skills:
  - `LocalResourceProvider` class inheriting from `ResourceProvider`
  - `__init__` with name, skills_dirs, owner, cache_ttl parameters
  - `__aenter__` - discover skills, connect callbacks, start watching
  - `__aexit__` - cleanup
  - `get_skills()` - return skills with LRU caching and TTL
  - `get_skill(name)` - get specific skill by name
  - `get_skill_instructions(name)` - return SKILL.md content
  - `get_references(skill_name)` - list reference files in references/ subdirectory
  - `read_reference(skill_name, ref_path)` - read reference with path traversal protection
  - `_detect_mime_type()` - helper for MIME type detection
  - `_connect_registry_callbacks()` - connect SkillsRegistry to signals
  - `_start_watching()` - filesystem watcher (TODO for now)
  - `_invalidate_cache()` - cache invalidation

  **Must NOT do**:
  - Do NOT use VFSRegistry pattern
  - Do NOT allow path traversal (use Path.relative_to())
  - Do NOT create parallel skill systems (integrate with SkillsRegistry)
  - Do NOT implement real-time watching yet (leave as TODO)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Wave 1)
  - **Parallel Group**: Wave 2
  - **Blocks**: Tasks 7, 10, 11
  - **Blocked By**: Tasks 1, 2

  **References**:
  - RFC-0020 lines 317-576 - LocalResourceProvider specification
  - `src/agentpool/resource_providers/base.py:ResourceProvider` - Base class
  - `src/agentpool/skills/registry.py:SkillsRegistry` - Registry to use
  - `src/agentpool/skills/skill.py:Skill` - Skill model
  - `src/agentpool/skills/exceptions.py` (Task 1) - Use exceptions

  **Acceptance Criteria**:
  - [ ] Implements full ResourceProvider interface
  - [ ] Uses SkillsRegistry for discovery
  - [ ] LRU caching with configurable TTL (default 60s)
  - [ ] Path traversal protection with Path.relative_to()
  - [ ] Connects registry callbacks to skills_changed signal
  - [ ] Handles references/ subdirectory

  **QA Scenarios**:
  ```
  Scenario: LocalResourceProvider basic usage
    Tool: Bash (python)
    Preconditions: Test skill directory exists
    Steps:
      1. Create LocalResourceProvider with test skills directory
      2. async with provider:
      3.     skills = await provider.get_skills()
      4.     assert len(skills) > 0
      5.     skill = await provider.get_skill("test-skill")
      6.     assert skill is not None
    Expected Result: Provider discovers and returns skills
    Evidence: .sisyphus/evidence/task-5-basic.png

  Scenario: Path traversal protection in read_reference
    Tool: Bash (python)
    Steps:
      1. try:
      2.     await provider.read_reference("test-skill", "../../../etc/passwd")
      3.     assert False, "Should raise SecurityError"
      4. except SecurityError:
      5.     pass
    Expected Result: SecurityError raised on traversal attempt
    Evidence: .sisyphus/evidence/task-5-security.png

  Scenario: Cache invalidation on skill change
    Tool: Bash (python)
    Steps:
      1. skills1 = await provider.get_skills()
      2. # Trigger skill change
      3. skills2 = await provider.get_skills()
      4. # Check that cache was invalidated
    Expected Result: Cache properly invalidated on changes
    Evidence: .sisyphus/evidence/task-5-cache.png
  ```

  **Evidence to Capture**:
  - [ ] Screenshot of LocalResourceProvider working
  - [ ] Evidence of path traversal protection
  - [ ] Cache behavior verification

  **Commit**: YES
  - Message: `feat(resource_providers): add LocalResourceProvider for filesystem skills`
  - Files: `src/agentpool/resource_providers/local.py`
  - Pre-commit: `uv run ruff check src/agentpool/resource_providers/local.py`

---

- [x] **6. Extend MCPResourceProvider with Skill Methods**

  **What to do**:
  Extend `src/agentpool/resource_providers/mcp_provider.py` with skill support:
  - `get_skills()` - return combined prompt-based + resource-based skills
  - `_get_prompt_skills()` - map MCP prompts to skills with argument schemas
  - `_get_resource_skills()` - discover skills via skill:// URI scheme (FastMCP Skills Provider)
  - `_get_skill_manifest(skill_name)` - read _manifest resource
  - `_get_skill_description(skill_name, main_uri)` - extract from SKILL.md
  - `get_skill_instructions(name, arguments)` - get skill content (both types)
  - `_get_prompt_skill_instructions(prompt, arguments)` - render prompt-based skill
  - `_get_resource_skill_instructions(skill_name)` - read resource-based skill
  - `_format_prompt_skill_template(prompt, missing_args)` - template for prompts with required args
  - `get_references(skill_name)` - list references for a skill
  - `read_reference(skill_name, ref_path)` - read reference content
  - `_on_skills_changed()` - callback for skill changes (derive from prompt/resource changes)

  **Must NOT do**:
  - Do NOT break existing MCP tool/prompt/resource functionality
  - Do NOT create duplicate skill objects
  - Do NOT modify MCP client connection logic

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Wave 1)
  - **Parallel Group**: Wave 2
  - **Blocks**: Tasks 7, 10
  - **Blocked By**: Task 1

  **References**:
  - RFC-0020 lines 578-945 - MCPResourceProvider extension specification
  - `src/agentpool/resource_providers/mcp_provider.py` - Current implementation
  - `src/agentpool/resource_providers/resource_info.py:ResourceInfo.from_mcp_resource()` - MCP resource conversion
  - `src/agentpool/skills/skill.py:Skill` - Skill model

  **Acceptance Criteria**:
  - [ ] get_skills() returns non-empty list for MCP servers with skills
  - [ ] Prompt-based skills include argument_schema metadata
  - [ ] Resource-based skills detect skill://skill-name/SKILL.md pattern
  - [ ] FastMCP Skills Provider protocol supported
  - [ ] get_references() works for both skill types
  - [ ] read_reference() has path traversal protection

  **QA Scenarios**:
  ```
  Scenario: Discover prompt-based skills
    Tool: Bash (python)
    Preconditions: Mock MCP server with prompts
    Steps:
      1. Create MCPResourceProvider connected to mock server
      2. skills = await provider.get_skills()
      3. prompt_skills = [s for s in skills if s.metadata.get("skill_type") == "prompt"]
      4. assert len(prompt_skills) > 0
    Expected Result: Prompts converted to skills
    Evidence: .sisyphus/evidence/task-6-prompt.png

  Scenario: Discover resource-based skills
    Tool: Bash (python)
    Preconditions: Mock MCP server with skill:// resources
    Steps:
      1. Mock server exposes skill://test-skill/SKILL.md
      2. skills = await provider.get_skills()
      3. resource_skills = [s for s in skills if s.metadata.get("skill_type") == "resource"]
      4. assert len(resource_skills) > 0
    Expected Result: skill:// resources detected as skills
    Evidence: .sisyphus/evidence/task-6-resource.png

  Scenario: Get skill instructions from prompt-based skill
    Tool: Bash (python)
    Steps:
      1. instructions = await provider.get_skill_instructions("test-prompt")
      2. assert len(instructions) > 0
    Expected Result: Instructions returned as string
    Evidence: .sisyphus/evidence/task-6-instructions.png
  ```

  **Evidence to Capture**:
  - [ ] Screenshot of MCP skill discovery
  - [ ] Both prompt-based and resource-based skills working
  - [ ] Skill instructions retrieval

  **Commit**: YES
  - Message: `feat(resource_providers): extend MCPResourceProvider with skill support`
  - Files: `src/agentpool/resource_providers/mcp_provider.py`
  - Pre-commit: `uv run ruff check src/agentpool/resource_providers/mcp_provider.py`

---

- [x] **7. Tests for Wave 2 Providers**

  **What to do**:
  Create tests for Wave 2 provider implementations:
  - `tests/resource_providers/test_local_provider.py` - Test LocalResourceProvider
  - `tests/resource_providers/test_mcp_provider_skills.py` - Test MCP skill methods
  - Use mocks for MCP server interactions

  **Must NOT do**:
  - Do NOT require real MCP servers for tests
  - Do NOT test internal implementation details
  - Do NOT skip security tests

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after T5, T6)
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: Tasks 5, 6

  **References**:
  - `tests/conftest.py` - Fixtures and TestModel
  - `tests/resource_providers/test_mcp_provider.py` - Existing MCP tests

  **Acceptance Criteria**:
  - [ ] LocalResourceProvider tests >80% coverage
  - [ ] MCPResourceProvider skill tests >80% coverage
  - [ ] Mock MCP server for testing
  - [ ] Security tests for path traversal

  **QA Scenarios**:
  ```
  Scenario: Run provider tests
    Tool: Bash
    Steps:
      1. uv run pytest tests/resource_providers/test_local_provider.py -v
      2. uv run pytest tests/resource_providers/test_mcp_provider_skills.py -v
    Expected Result: All tests pass
    Evidence: .sisyphus/evidence/task-7-tests.png
  ```

  **Commit**: YES
  - Message: `test(resource_providers): add tests for LocalResourceProvider and MCP skills`
  - Files: `tests/resource_providers/test_local_provider.py`, `tests/resource_providers/test_mcp_provider_skills.py`
  - Pre-commit: `uv run pytest tests/resource_providers/test_local_provider.py tests/resource_providers/test_mcp_provider_skills.py -v`

---

- [x] **8. Integration Tests for Providers**

  **What to do**:
  Create integration tests combining multiple providers:
  - `tests/integration/test_skill_providers.py` - Test provider interactions
  - Test AggregatingResourceProvider with Local + MCP providers
  - Test skill name collision resolution
  - Test change signal propagation

  **Must NOT do**:
  - Do NOT mock AggregatingResourceProvider internals
  - Do NOT skip async context manager tests

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after T5, T6)
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: Tasks 5, 6

  **References**:
  - `tests/integration/` - Existing integration tests

  **Acceptance Criteria**:
  - [ ] Multiple providers aggregate correctly
  - [ ] Skill name collisions resolved by priority
  - [ ] Change signals propagate through chain

  **QA Scenarios**:
  ```
  Scenario: Provider aggregation
    Tool: Bash
    Steps:
      1. Create AggregatingResourceProvider with Local + Mock MCP
      2. async with provider:
      3.     skills = await provider.get_skills()
      4.     assert skills from both providers present
    Expected Result: Skills aggregated from all providers
    Evidence: .sisyphus/evidence/task-8-integration.png
  ```

  **Commit**: YES
  - Message: `test(integration): add skill provider integration tests`
  - Files: `tests/integration/test_skill_providers.py`
  - Pre-commit: `uv run pytest tests/integration/test_skill_providers.py -v`

---

- [x] **9. Update load_skill Tool with URI Support**

  **What to do**:
  Update `src/agentpool_toolsets/builtin/skills.py`:
  - Modify `load_skill()` to accept skill:// URIs or bare skill names
  - Add SKILL_USAGE_GUIDANCE constant with URI format documentation
  - Use SkillURIResolver from pool for resolution
  - Support argument substitution ($1, $2, $@, $ARGUMENTS)
  - Handle both main skill and reference content
  - Maintain backward compatibility with existing skill name usage
  - Update `list_skills()` to show URI information

  **Must NOT do**:
  - Do NOT break existing skill name loading
  - Do NOT remove existing skill metadata display
  - Do NOT change function signature (skill_name parameter)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Wave 2)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 12
  - **Blocked By**: Task 2

  **References**:
  - RFC-0020 lines 1302-1520 - load_skill specification
  - `src/agentpool_toolsets/builtin/skills.py` - Current implementation
  - `src/agentpool/skills/uri_resolver.py` (Task 2) - Use SkillURIResolver

  **Acceptance Criteria**:
  - [ ] load_skill accepts both "skill-name" and "skill://provider/skill-name"
  - [ ] Bare skill names resolve with provider priority
  - [ ] Reference paths work: "skill://provider/skill/references/file.md"
  - [ ] Argument substitution works: $1, $2, $@, $ARGUMENTS
  - [ ] Backward compatible: old calls still work

  **QA Scenarios**:
  ```
  Scenario: Load skill by bare name
    Tool: Bash (python)
    Steps:
      1. result = await load_skill(ctx, "python-expert")
      2. assert "python-expert" in result
      3. assert "# python-expert" in result
    Expected Result: Skill loaded by name with proper formatting
    Evidence: .sisyphus/evidence/task-9-barename.png

  Scenario: Load skill by URI
    Tool: Bash (python)
    Steps:
      1. result = await load_skill(ctx, "skill://local/python-expert")
      2. assert "python-expert" in result
    Expected Result: Skill loaded by URI with explicit provider
    Evidence: .sisyphus/evidence/task-9-uri.png

  Scenario: Load reference content
    Tool: Bash (python)
    Steps:
      1. result = await load_skill(ctx, "skill://local/python-expert/references/guide.md")
      2. assert "Reference:" in result
    Expected Result: Reference content loaded with header
    Evidence: .sisyphus/evidence/task-9-reference.png

  Scenario: Argument substitution
    Tool: Bash (python)
    Steps:
      1. result = await load_skill(ctx, "test-skill", "arg1 arg2")
      2. # Skill has "First: $1, Second: $2, All: $@"
      3. assert "First: arg1" in result
      4. assert "Second: arg2" in result
      5. assert "All: arg1 arg2" in result
    Expected Result: Arguments substituted in skill content
    Evidence: .sisyphus/evidence/task-9-args.png
  ```

  **Evidence to Capture**:
  - [ ] Bare name loading works
  - [ ] URI loading works
  - [ ] Reference loading works
  - [ ] Argument substitution works

  **Commit**: YES
  - Message: `feat(tools): update load_skill with URI support`
  - Files: `src/agentpool_toolsets/builtin/skills.py`
  - Pre-commit: `uv run ruff check src/agentpool_toolsets/builtin/skills.py`

---

- [x] **10. AgentPool Integration**

  **What to do**:
  Update `src/agentpool/delegation/pool.py`:
  - Add `_setup_skills_provider()` method for initialization
  - Add `_on_skills_changed()` callback to forward changes
  - Add `skill_resolver` property returning SkillURIResolver
  - Add `skill_provider` property returning AggregatingResourceProvider
  - Initialize in pool lifecycle (likely in __aenter__ or setup method)
  - Connect SkillsManager._resource_provider if SkillsManager exists

  **Must NOT do**:
  - Do NOT create parallel skill systems (integrate with SkillsManager)
  - Do NOT break existing pool initialization
  - Do NOT require skills provider if not configured

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Wave 2)
  - **Parallel Group**: Wave 3
  - **Blocks**: Tasks 12, 13
  - **Blocked By**: Tasks 2, 3, 5, 6

  **References**:
  - RFC-0020 lines 1522-1602 - AgentPool integration specification
  - `src/agentpool/delegation/pool.py` - Current implementation
  - `src/agentpool/skills/uri_resolver.py` (Task 2) - SkillURIResolver

  **Acceptance Criteria**:
  - [ ] AgentPool has skill_resolver property
  - [ ] AgentPool has skill_provider property
  - [ ] _setup_skills_provider() creates AggregatingResourceProvider
  - [ ] Local and MCP providers aggregated
  - [ ] SkillsManager._resource_provider set if SkillsManager exists

  **QA Scenarios**:
  ```
  Scenario: AgentPool skill resolver
    Tool: Bash (python)
    Steps:
      1. async with AgentPool(config) as pool:
      2.     resolver = pool.skill_resolver
      3.     assert resolver is not None
      4.     provider = pool.skill_provider
      5.     assert provider is not None
    Expected Result: Pool exposes skill resolver and provider
    Evidence: .sisyphus/evidence/task-10-pool.png

  Scenario: Skill resolution through pool
    Tool: Bash (python)
    Steps:
      1. async with AgentPool(config) as pool:
      2.     resolved = await pool.skill_resolver.resolve("python-expert")
      3.     assert resolved.content is not None
    Expected Result: Skills resolved through pool
    Evidence: .sisyphus/evidence/task-10-resolve.png
  ```

  **Evidence to Capture**:
  - [ ] AgentPool exposes skill properties
  - [ ] Skills resolved through pool

  **Commit**: YES
  - Message: `feat(delegation): add skill resolver and provider to AgentPool`
  - Files: `src/agentpool/delegation/pool.py`
  - Pre-commit: `uv run ruff check src/agentpool/delegation/pool.py`

---

- [x] **11. SkillsManager Extension**

  **What to do**:
  Update `src/agentpool/skills/manager.py`:
  - Add `__aenter__` extension to create and enter LocalResourceProvider
  - Add `resource_provider` property returning LocalResourceProvider
  - Store _resource_provider on instance
  - Ensure cleanup in __aexit__

  **Must NOT do**:
  - Do NOT change SkillsManager primary purpose
  - Do NOT break existing manager functionality
  - Do NOT require resource_provider for all operations

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Wave 2)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 12
  - **Blocked By**: Task 5

  **References**:
  - RFC-0020 lines 1604-1633 - SkillsManager extension specification
  - `src/agentpool/skills/manager.py` - Current implementation
  - `src/agentpool/resource_providers/local.py` (Task 5) - LocalResourceProvider

  **Acceptance Criteria**:
  - [ ] SkillsManager has resource_provider property
  - [ ] LocalResourceProvider created in __aenter__
  - [ ] Proper cleanup in __aexit__

  **QA Scenarios**:
  ```
  Scenario: SkillsManager resource provider
    Tool: Bash (python)
    Steps:
      1. async with SkillsManager(config) as manager:
      2.     provider = manager.resource_provider
      3.     assert provider is not None
      4.     skills = await provider.get_skills()
      5.     assert len(skills) > 0
    Expected Result: Manager exposes resource provider
    Evidence: .sisyphus/evidence/task-11-manager.png
  ```

  **Commit**: YES
  - Message: `feat(skills): add ResourceProvider interface to SkillsManager`
  - Files: `src/agentpool/skills/manager.py`
  - Pre-commit: `uv run ruff check src/agentpool/skills/manager.py`

---

- [x] **12. Tests for Wave 3 Integration**

  **What to do**:
  Create tests for Wave 3 integration:
  - `tests/integration/test_skill_resolution.py` - End-to-end skill resolution
  - `tests/toolsets/test_load_skill_uri.py` - Test load_skill with URIs
  - `tests/delegation/test_pool_skills.py` - Test AgentPool skill integration

  **Must NOT do**:
  - Do NOT test at unit level (these are integration tests)
  - Do NOT skip backward compatibility tests

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after T9, T10, T11)
  - **Parallel Group**: Wave 3
  - **Blocks**: None
  - **Blocked By**: Tasks 9, 10, 11

  **References**:
  - `tests/integration/` - Existing integration tests

  **Acceptance Criteria**:
  - [ ] End-to-end skill resolution works
  - [ ] load_skill backward compatible
  - [ ] AgentPool skill integration works

  **QA Scenarios**:
  ```
  Scenario: End-to-end skill resolution
    Tool: Bash
    Steps:
      1. uv run pytest tests/integration/test_skill_resolution.py -v
    Expected Result: All integration tests pass
    Evidence: .sisyphus/evidence/task-12-e2e.png
  ```

  **Commit**: YES
  - Message: `test(integration): add Wave 3 integration tests`
  - Files: `tests/integration/test_skill_resolution.py`, `tests/toolsets/test_load_skill_uri.py`, `tests/delegation/test_pool_skills.py`
  - Pre-commit: `uv run pytest tests/integration/test_skill_resolution.py -v`

---

- [x] **13. Update Protocol Bridges**

  **What to do**:
  Update protocol bridge implementations to use new skill system:
  - `src/agentpool_server/opencode_server/` - Update skill command handling
  - `src/agentpool_server/acp_server/` - Update skill exposure
  - Ensure slash commands work with new skill:// URIs
  - Connect SkillCommandRegistry to skill provider changes

  **Must NOT do**:
  - Do NOT break existing protocol functionality
  - Do NOT change protocol APIs

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Wave 3)
  - **Parallel Group**: Wave 4
  - **Blocks**: Task 16
  - **Blocked By**: Task 10

  **References**:
  - `src/agentpool_server/opencode_server/` - OpenCode server
  - `src/agentpool_server/acp_server/` - ACP server
  - RFC-0016 - Related slash command architecture

  **Acceptance Criteria**:
  - [ ] OpenCode server uses new skill provider
  - [ ] ACP server exposes skills correctly
  - [ ] Slash commands work with skill:// URIs

  **QA Scenarios**:
  ```
  Scenario: Protocol integration
    Tool: Bash (pytest)
    Steps:
      1. Start OpenCode server with skill config
      2. Verify skills exposed via protocol
    Expected Result: Protocol bridges use new skill system
    Evidence: .sisyphus/evidence/task-13-protocol.png
  ```

  **Commit**: YES
  - Message: `feat(servers): update protocol bridges for new skill system`
  - Files: `src/agentpool_server/opencode_server/`, `src/agentpool_server/acp_server/`
  - Pre-commit: `uv run ruff check src/agentpool_server/`

---

- [x] **14. Documentation and Examples**

  **What to do**:
  Create documentation for RFC-0020 implementation:
  - Update `docs/` with skill:// URI usage guide
  - Add examples in `site/examples/` showing:
    - Loading skills by URI
    - Creating skills with references
    - Using MCP-exposed skills
  - Update RFC-0020 status to IMPLEMENTED
  - Add migration guide if needed

  **Must NOT do**:
  - Do NOT duplicate existing skill documentation
  - Do NOT skip examples for new features

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4
  - **Blocks**: Task 16
  - **Blocked By**: None

  **References**:
  - `docs/` - Existing documentation
  - `site/examples/` - Example configurations
  - RFC-0020 - Specification to document

  **Acceptance Criteria**:
  - [ ] Documentation covers skill:// URI format
  - [ ] Examples show all use cases
  - [ ] RFC status updated to IMPLEMENTED

  **QA Scenarios**:
  ```
  Scenario: Documentation review
    Tool: Read
    Steps:
      1. Read docs/skill-uri-usage.md
      2. Verify all URI formats documented
    Expected Result: Documentation complete and accurate
    Evidence: .sisyphus/evidence/task-14-docs.png
  ```

  **Commit**: YES
  - Message: `docs: add RFC-0020 implementation documentation`
  - Files: `docs/`, `site/examples/`
  - Pre-commit: N/A (docs only)

---

- [x] **15. Performance Testing**

  **What to do**:
  Verify performance meets RFC criteria (<50ms command registration, <100ms acceptable):
  - Benchmark skill discovery time
  - Benchmark URI resolution time
  - Benchmark skill loading time
  - Verify caching effectiveness
  - Document performance characteristics

  **Must NOT do**:
  - Do NOT skip performance validation
  - Do NOT optimize prematurely (measure first)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Wave 3)
  - **Parallel Group**: Wave 4
  - **Blocks**: Task 16
  - **Blocked By**: Task 10

  **References**:
  - RFC-0020 lines 123-135 - Performance criteria
  - `tests/performance/` - Performance tests (create if needed)

  **Acceptance Criteria**:
  - [ ] Skill discovery <50ms (or <100ms acceptable)
  - [ ] URI resolution <10ms
  - [ ] Caching reduces load time significantly
  - [ ] Performance documented

  **QA Scenarios**:
  ```
  Scenario: Performance benchmark
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/performance/test_skill_performance.py -v
      2. Verify all benchmarks pass
    Expected Result: Performance meets RFC criteria
    Evidence: .sisyphus/evidence/task-15-perf.png
  ```

  **Commit**: YES
  - Message: `test(performance): add skill performance benchmarks`
  - Files: `tests/performance/test_skill_performance.py`
  - Pre-commit: `uv run pytest tests/performance/test_skill_performance.py -v`

---

- [x] **16. Security Audit**

  **What to do**:
  Conduct security audit of implementation:
  - Verify path traversal protection in all read_reference methods
  - Verify null byte handling
  - Verify symlink handling (resolve before validation)
  - Test with malicious inputs
  - Document security considerations

  **Must NOT do**:
  - Do NOT skip security tests
  - Do NOT assume security (verify with tests)

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (after T13, T14, T15)
  - **Parallel Group**: Wave 4
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 13, 14, 15

  **References**:
  - RFC-0020 lines 1701-1720 - Security considerations
  - `src/agentpool/skills/exceptions.py:SecurityError` - Security exception

  **Acceptance Criteria**:
  - [ ] Path traversal protection verified
  - [ ] Null byte handling verified
  - [ ] Malicious input tests pass
  - [ ] Security audit documented

  **QA Scenarios**:
  ```
  Scenario: Path traversal attack
    Tool: Bash (python)
    Steps:
      1. Attempt traversal with "../../../etc/passwd"
      2. Attempt traversal with URL encoding "%2e%2e%2f"
      3. Attempt null byte injection
      4. All attempts should raise SecurityError
    Expected Result: All attacks blocked
    Evidence: .sisyphus/evidence/task-16-security.png
  ```

  **Commit**: YES
  - Message: `security: add security tests and audit for skill system`
  - Files: `tests/security/test_skill_security.py`
  - Pre-commit: `uv run pytest tests/security/test_skill_security.py -v`

---

## Final Verification Wave

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists. For each "Must NOT Have": search codebase for forbidden patterns. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `tsc --noEmit` + linter + `bun test`. Review all changed files for: `as any`/`@ts-ignore`, empty catches, console.log in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state. Execute EVERY QA scenario from EVERY task. Test cross-task integration. Test edge cases: empty state, invalid input, rapid actions. Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 match. Check "Must NOT do" compliance. Detect cross-task contamination.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **Pattern**: `feat(scope): description` for features, `test(scope): description` for tests
- **Example**: `feat(skills): add SkillError exception hierarchy`
- **Pre-commit**: `uv run pytest tests/path/to/test_file.py -v`
- **Group related tasks**: Wave commits

---

## Success Criteria

### Verification Commands

```bash
# Run all tests
uv run pytest tests/skills/ tests/resource_providers/ -v

# Run with coverage
uv run pytest --cov=src/agentpool/skills --cov=src/agentpool/resource_providers --cov-report=term-missing

# Type checking
uv run --no-group docs mypy src/agentpool/skills/ src/agentpool/resource_providers/

# Lint
uv run ruff check src/agentpool/skills/ src/agentpool/resource_providers/

# Integration test
uv run pytest tests/integration/test_skill_resolution.py -v
```

### Final Checklist

- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All exception classes implemented
- [ ] URI resolver handles all RFC-specified formats
- [ ] Path traversal protection verified (security audit passed)
- [ ] MCPResourceProvider discovers both skill types
- [ ] AggregatingResourceProvider aggregates skills
- [ ] load_skill tool supports old and new formats
- [ ] All tests pass with >80% coverage
- [ ] No breaking changes to existing APIs
- [ ] Documentation updated with examples
