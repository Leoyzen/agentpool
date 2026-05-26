# RFC-0008: Dynamic Skills Injection via ResourceProvider Instructions

## TL;DR

> **Goal**: Complete RFC-0008 implementation - dynamic skills injection using ResourceProvider.get_instructions()
> 
> **Current State**: Core implementation is complete (SkillsInstructionProvider, config models, AgentPool integration)
> 
> **Gap Identified**: SkillsInstructionProvider not exported from resource_providers module
> 
> **Deliverables**:
> - Export SkillsInstructionProvider from resource_providers/__init__.py
> - Add comprehensive end-to-end tests
> - Add documentation with usage examples
> - Verify backward compatibility
>
> **Estimated Effort**: Short (2-3 hours, 4 tasks)
> **Parallel Execution**: YES - 3 independent tasks + 1 final integration
>

---

## Context

### Original Request
Implement RFC-0008 which proposes dynamic skills injection into agent system prompts via RFC-0007's ResourceProvider.get_instructions() mechanism, superseding RFC-0005's static approach.

### Current Implementation Status

| Component | Status | Notes |
|-----------|--------|-------|
| SkillsInstructionProvider | ✅ Complete | `src/agentpool/resource_providers/skills_instruction.py` |
| Configuration models | ✅ Complete | `SkillsInstructionConfig` in `agentpool_config/skills.py` |
| Toolset overrides | ✅ Complete | `SkillsToolsetConfig` in `agentpool_config/toolsets.py` |
| AgentPool integration | ✅ Complete | Provider instantiated and added to agents in pool.py |
| Unit tests | ✅ Complete | `tests/resource_providers/test_skills_instruction.py` |
| Integration tests | ✅ Partial | `tests/integration/test_skills_injection.py` exists |
| **Public export** | ❌ **MISSING** | Not in `resource_providers/__init__.__all__` |
| Documentation | ❌ Missing | Usage examples not in docs/ |
| E2E tests | ❌ Missing | Full agent run with skills injection |

### Technical Foundation
- RFC-0007 infrastructure exists (get_instructions() mechanism)
- NativeAgent already collects instructions from all providers
- XML format implemented for structured skill representation
- Three injection modes: off (default), metadata, full

---

## Work Objectives

### Core Objective
Complete RFC-0008 by exporting SkillsInstructionProvider publicly, adding documentation, and verifying end-to-end functionality.

### Concrete Deliverables
1. Export SkillsInstructionProvider from `resource_providers/__init__.py`
2. Add end-to-end test with full agent run and skills injection verification
3. Add documentation with YAML config examples to docs/
4. Verify backward compatibility (default is off, no breaking changes)

### Definition of Done
- [ ] All public API components are exported
- [ ] Tests pass: `uv run pytest tests/resource_providers/test_skills_instruction.py tests/integration/test_skills_injection.py`
- [ ] New E2E test passes showing skills in actual system prompt
- [ ] Documentation renders correctly in docs site
- [ ] No breaking changes (backward compatible)

### Must Have
- Export SkillsInstructionProvider from public API
- At least one E2E test verifying actual agent behavior
- Documentation with working YAML examples

### Must NOT Have (Guardrails)
- Do NOT change existing behavior (default off is correct)
- Do NOT remove or rename existing public APIs
- Do NOT add dependencies beyond what's already in use

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES - pytest with existing test patterns
- **Automated tests**: YES (tests-after, not TDD since implementation exists)
- **Framework**: pytest

### QA Policy
Every task includes Agent-Executed QA Scenarios:
- **Code quality**: Use Bash (ruff, mypy) to verify no lint/type errors
- **Tests**: Use Bash (pytest) to verify tests pass
- **Imports**: Use Bash (python -c) to verify public exports work

---

## Execution Strategy

### Parallel Execution Waves

**Wave 1: Public API Exposure (Independent)**
- Task 1: Export SkillsInstructionProvider from resource_providers/__init__.py
- Task 2: Add end-to-end test with actual agent run
- Task 3: Add documentation with YAML examples

**Wave 2: Integration & Verification (After Wave 1)**
- Task 4: Run full test suite and verify backward compatibility

```
Critical Path: Task 1, 2, 3 → Task 4
Parallel Speedup: 75% faster than sequential
Max Concurrent: 3 (Wave 1)
```

### Agent Dispatch Summary

- **Wave 1**: 3 tasks → all `quick` (single file changes)
- **Wave 2**: 1 task → `quick` (test execution)

---

## TODOs

- [ ] 1. Export SkillsInstructionProvider from resource_providers module

  **What to do**:
  - Add `SkillsInstructionProvider` import to `src/agentpool/resource_providers/__init__.py`
  - Add `"SkillsInstructionProvider"` to `__all__` list
  - Verify import works: `from agentpool.resource_providers import SkillsInstructionProvider`

  **Must NOT do**:
  - Do NOT move the file
  - Do NOT change the class interface

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - Reason: Simple export addition, single file
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Task 4
  - **Blocked By**: None

  **References**:
  - File to edit: `src/agentpool/resource_providers/__init__.py`
  - Class to export: `src/agentpool/resource_providers/skills_instruction.py:SkillsInstructionProvider`
  - Pattern to follow: Other exports in `__init__.py` (ResourceProvider, StaticResourceProvider, etc.)

  **Acceptance Criteria**:
  - [ ] `SkillsInstructionProvider` added to imports
  - [ ] `SkillsInstructionProvider` added to `__all__`
  - [ ] Import test passes: `python -c "from agentpool.resource_providers import SkillsInstructionProvider; print('OK')"`

  **QA Scenarios**:

  ```
  Scenario: Verify public export works
    Tool: Bash
    Precondition: Changes applied to __init__.py
    Steps:
      1. Run: python -c "from agentpool.resource_providers import SkillsInstructionProvider; print(SkillsInstructionProvider.__name__)"
    Expected Result: Output contains "SkillsInstructionProvider"
    Evidence: .sisyphus/evidence/task-1-export-verification.txt
  ```

  **Commit**: YES
  - Message: `feat(resource_providers): export SkillsInstructionProvider from public API`
  - Files: `src/agentpool/resource_providers/__init__.py`

---

- [ ] 2. Add end-to-end test for skills injection in agent runs

  **What to do**:
  - Create test in `tests/integration/test_skills_injection_e2e.py`
  - Test full workflow: config → pool → agent → run → verify skills in prompt
  - Test all three modes: off, metadata, full
  - Verify structure: XML format with correct elements

  **Must NOT do**:
  - Do NOT mock AgentPool or PydanticAgent (use real integration)
  - Do NOT skip actual skill discovery (create temp skills dir)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - Reason: Test file creation, existing patterns to follow
  - **Skills**: []
  - **Note**: Look at existing `tests/integration/test_skills_injection.py` as template

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: Task 4
  - **Blocked By**: None

  **References**:
  - Pattern file: `tests/integration/test_skills_injection.py`
  - Provider class: `src/agentpool/resource_providers/skills_instruction.py`
  - Config models: `src/agentpool_config/skills.py`
  - E2E patterns: `tests/integration/` directory

  **Acceptance Criteria**:
  - [ ] Test file created at `tests/integration/test_skills_injection_e2e.py`
  - [ ] Test passes: `uv run pytest tests/integration/test_skills_injection_e2e.py -v`
  - [ ] Covers all 3 modes: off, metadata, full
  - [ ] Verifies XML structure in generated instructions
  - [ ] Uses real AgentPool (not mocked)

  **QA Scenarios**:

  ```
  Scenario: Run E2E test and verify passes
    Tool: Bash
    Precondition: Test file created
    Steps:
      1. Run: uv run pytest tests/integration/test_skills_injection_e2e.py -v
    Expected Result: All tests pass, no failures
    Evidence: .sisyphus/evidence/task-2-test-output.txt

  Scenario: Verify test covers all injection modes
    Tool: Bash
    Precondition: Test file exists
    Steps:
      1. grep -E "(off|metadata|full)" tests/integration/test_skills_injection_e2e.py | head -20
    Expected Result: All three modes mentioned in test
    Evidence: .sisyphus/evidence/task-2-coverage.txt
  ```

  **Commit**: YES
  - Message: `test(integration): add e2e test for dynamic skills injection`
  - Files: `tests/integration/test_skills_injection_e2e.py`

---

- [ ] 3. Add documentation with YAML configuration examples

  **What to do**:
  - Create or update documentation file in `docs/` or `docs/skills/`
  - Include:
    - Overview of RFC-0008 feature
    - YAML config examples for all modes (off, metadata, full)
    - Per-agent override examples
    - XML output format example
    - Migration note from RFC-0005
  - Reference: RFC-0008 "Configuration Examples" section

  **Must NOT do**:
  - Do NOT duplicate existing README content
  - Do NOT use markdown features not supported by docs framework

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - Reason: Documentation writing with technical content
  - **Skills**: []
  - **Note**: Check docs/ structure to understand format

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Task 4 (for final docs verification)
  - **Blocked By**: None

  **References**:
  - RFC content: `docs/rfcs/accepted/RFC-0008-dynamic-skills-injection.md`
  - Config examples from RFC: lines 795-869
  - Existing docs: `docs/` directory structure
  - Markdown format: Check if using MkDocs-compatible markdown

  **Acceptance Criteria**:
  - [ ] Documentation file created (e.g., `docs/skills/dynamic-injection.md`)
  - [ ] Includes YAML examples for all 3 modes
  - [ ] Includes per-agent override example
  - [ ] Includes XML output format example
  - [ ] References RFC-0008 and explains feature purpose

  **QA Scenarios**:

  ```
  Scenario: Verify docs file exists and has yaml examples
    Tool: Bash
    Precondition: Docs file created
    Steps:
      1. ls -la docs/skills/*.md 2>/dev/null || ls -la docs/*.md | grep skill
      2. grep -c "```yaml" docs/skills/dynamic-injection.md 2>/dev/null || grep -c "```yaml" docs/**/*.md
    Expected Result: Docs file exists with yaml code blocks
    Evidence: .sisyphus/evidence/task-3-docs-exist.txt

  Scenario: Verify all modes documented
    Tool: Bash
    Precondition: Docs file exists
    Steps:
      1. grep -E "(mode:\s*(off|metadata|full))" docs/skills/dynamic-injection.md 2>/dev/null | wc -l
    Expected Result: Found references to all three modes
    Evidence: .sisyphus/evidence/task-3-modes-covered.txt
  ```

  **Commit**: YES
  - Message: `docs(skills): add documentation for dynamic skills injection (RFC-0008)`
  - Files: `docs/skills/dynamic-injection.md` (or appropriate path)

---

- [ ] 4. Run full test suite and verify backward compatibility

  **What to do**:
  - Run complete test suite: `uv run pytest`
  - Verify no regressions in existing tests
  - Verify backward compatibility:
    - Config without instruction field still works (default off)
    - Existing agent configs continue to work
    - No public API changes break existing code
  - Run linting: `uv run ruff check src/`
  - Run type checking: `uv run mypy src/`

  **Must NOT do**:
  - Do NOT ignore failing tests
  - Do NOT skip type checking

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - Reason: Test execution and verification
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (after Tasks 1, 2, 3)
  - **Blocks**: None (final task)
  - **Blocked By**: Tasks 1, 2, 3

  **References**:
  - Test command patterns in AGENTS.md (README: Testing section)
  - CI patterns if visible in `.github/workflows/`

  **Acceptance Criteria**:
  - [ ] `uv run pytest` passes (or existing failures unchanged)
  - [ ] `uv run ruff check src/` passes with no new errors
  - [ ] `uv run mypy src/` passes with no new errors
  - [ ] Backward compatibility verified (configs without injection work)

  **QA Scenarios**:

  ```
  Scenario: Run full test suite
    Tool: Bash
    Precondition: All previous tasks complete
    Steps:
      1. Run: uv run pytest -x --tb=short 2>&1 | tail -50
    Expected Result: Test suite completes without new failures
    Evidence: .sisyphus/evidence/task-4-test-suite.txt

  Scenario: Run linting and type checking
    Tool: Bash
    Precondition: Code changes applied
    Steps:
      1. Run: uv run ruff check src/agentpool/resource_providers/__init__.py
      2. Run: uv run --no-group docs mypy src/agentpool/resource_providers/__init__.py
    Expected Result: No new lint or type errors in changed files
    Evidence: .sisyphus/evidence/task-4-lint-type.txt

  Scenario: Verify backward compatibility
    Tool: Bash
    Precondition: Test exists for legacy configs
    Steps:
      1. grep -r "instruction:" tests/ | grep skills | wc -l
      2. Check tests pass without injection config
    Expected Result: Existing configs without instruction field work
    Evidence: .sisyphus/evidence/task-4-backward-compat.txt
  ```

  **Commit**: NO (verification task, no code changes)

---

## Commit Strategy

- **Task 1**: `feat(resource_providers): export SkillsInstructionProvider from public API`
- **Task 2**: `test(integration): add e2e test for dynamic skills injection`
- **Task 3**: `docs(skills): add documentation for dynamic skills injection (RFC-0008)`
- **Task 4**: (No commit - verification only)

---

## Success Criteria

### Verification Commands
```bash
# Verify public export
python -c "from agentpool.resource_providers import SkillsInstructionProvider; print('Export OK')"

# Run related tests
uv run pytest tests/resource_providers/test_skills_instruction.py tests/integration/test_skills_injection.py -v

# Full test suite
uv run pytest

# Quality checks
uv run ruff check src/agentpool/resource_providers/__init__.py
uv run --no-group docs mypy src/agentpool/resource_providers/__init__.py
```

### Final Checklist
- [x] SkillsInstructionProvider exists (already implemented)
- [x] Configuration models exist (already implemented)
- [x] AgentPool integration exists (already implemented)
- [ ] SkillsInstructionProvider exported from public API (Task 1)
- [ ] E2E test added (Task 2)
- [ ] Documentation added (Task 3)
- [ ] Full test suite passes (Task 4)
- [ ] Backward compatibility verified (Task 4)

---

## Notes

### Current Implementation Quality
The core RFC-0008 implementation is **already complete and functional**:
- SkillsInstructionProvider properly implements get_instructions()
- XML format matches RFC specification
- Configuration models support all features
- Integration with AgentPool works correctly
- Unit and integration tests exist

### What's Missing
Only minor "finishing touches" remain:
1. **Public API exposure** - The class isn't exported from the package
2. **E2E test** - No test with full agent.run() cycle
3. **Documentation** - No user-facing docs with examples

### Risk Assessment
- **Low risk** - Implementation exists and works
- **Backward compatible** - Default is "off", no breaking changes
- **Test coverage** - Existing tests provide safety net
