# RFC-0021: Agent Concurrent Execution Safety - Implementation Plan

## TL;DR

> **Quick Summary**: Implement per-call execution context (`AgentRunContext`) to isolate mutable state (`_cancelled`, `_current_stream_task`, `_event_queue`, `_injection_manager`) for safe concurrent `run_stream()` calls to the same agent instance.
>
> **Deliverables**:
> - `AgentRunContext` dataclass for per-call state isolation
> - Migrated BaseAgent with context-based state management
> - Fixed `finally` block bug in NativeAgent (line 917)
> - Passing concurrent safety test suite (100% pass rate)
> - Updated documentation and migration guide
>
> **Estimated Effort**: Medium (5-7 days)
> **Parallel Execution**: YES - 4 waves with parallel tasks in Waves 1-2
> **Critical Path**: Phase 0 Bug Fix → Wave 1 Context Creation → Wave 2 State Migration → Wave 3 Testing → Final Verification

---

## Context

### Original Request
Implement RFC-0021 to enable safe concurrent calls to the same agent instance by moving mutable state from instance-level to per-call execution context.

### Problem Statement
Current implementation shares instance-level mutable state across concurrent `run_stream()` calls, causing:
- 56% failure rate in concurrent scenarios
- Race conditions on `_cancelled` flag
- Premature task termination
- Event queue cross-contamination

### RFC Decision Summary
**Selected Option**: Option 2 - Per-Call Execution Context
**Rationale**: Best balance of safety, maintainability, and performance

### Key Findings from Pre-Flight Analysis
| State Field | Access Count | Risk Level | Migration Priority |
|-------------|--------------|------------|-------------------|
| `_cancelled` | 15+ locations | **Critical** | P0 |
| `_current_stream_task` | 8 locations | **Critical** | P0 |
| `_event_queue` | 6 locations | **Critical** | P1 |
| `_injection_manager` | 9 locations | **High** | P1 |
| `_background_task` | 4 locations | Medium | P2 |

### Must NOT Migrate (Intentionally Shared)
- `_formatted_system_prompt`: Represents agent's shared personality
- `_internal_fs`: Shared filesystem is a design feature

---

## Work Objectives

### Core Objective
Enable safe concurrent `run_stream()` calls to the same agent instance by isolating per-execution mutable state in `AgentRunContext`.

### Concrete Deliverables
1. `src/agentpool/agents/context.py` - Extended with `AgentRunContext`
2. `src/agentpool/agents/base_agent.py` - Migrated to use per-call context
3. `src/agentpool/agents/native_agent/agent.py` - Fixed finally block bug
4. `tests/agents/test_concurrent_safety.py` - All tests passing
5. Updated subclass migration guide

### Definition of Done
- [ ] All 3+ concurrent `run_stream()` calls complete successfully
- [ ] No shared state pollution (verified by tests)
- [ ] Serial execution performance unchanged (±5%)
- [ ] All existing tests pass without modification
- [ ] New concurrent safety tests added and passing

### Must Have
- P0 state fields migrated (`_cancelled`, `_current_stream_task`)
- P1 state fields migrated (`_event_queue`, `_injection_manager`)
- Finally block bug fixed
- Backward compatibility maintained

### Must NOT Have (Guardrails)
- DO NOT migrate `_formatted_system_prompt` (shared personality)
- DO NOT migrate `_internal_fs` (intentional shared feature)
- DO NOT change public API (`run_stream()` signature)
- DO NOT break existing serial execution behavior

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest already configured)
- **Automated tests**: Tests-after (existing tests first, new tests added)
- **Framework**: pytest with pytest-asyncio

### QA Policy
Every task MUST include agent-executed QA scenarios.

- **Python/Unit Tests**: Use Bash (`uv run pytest`)
- **Type Checking**: Use Bash (`uv run mypy`)
- **Linting**: Use Bash (`uv run ruff check`)

---

## Execution Strategy

### Phase-Based Execution Waves

```
Phase 0: Pre-Flight Bug Fix (Foundation - MUST complete first)
└── Task 0.1: Fix finally block bug in NativeAgent

Wave 1: Core Context Creation (Days 1-2, MAX PARALLEL)
├── Task 1.1: Create AgentRunContext dataclass
├── Task 1.2: Update context.py with AgentRunContext
├── Task 1.3: Add context parameter to BaseAgent internal methods
├── Task 1.4: Migrate _cancelled to context
└── Task 1.5: Migrate _current_stream_task to context

Wave 2: Full State Migration (Days 3-4, MAX PARALLEL)
├── Task 2.1: Migrate _event_queue to context
├── Task 2.2: Migrate _injection_manager to context
├── Task 2.3: Update NativeAgent for context compatibility
├── Task 2.4: Update event emitter for context access
└── Task 2.5: Ensure proper cleanup in finally blocks

Wave 3: Testing & Validation (Days 5-7)
├── Task 3.1: Run concurrent safety test suite
├── Task 3.2: Run full existing test suite (regression check)
├── Task 3.3: Performance benchmarks
├── Task 3.4: Subclass compatibility verification
└── Task 3.5: Documentation updates

Wave FINAL: Verification & Handoff
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review
├── Task F3: Full test suite verification
└── Task F4: Documentation review
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|------------|--------|
| 0.1 | - | 1.1-1.5 |
| 1.1 | - | 1.2, 1.3, 1.4, 1.5 |
| 1.2 | 1.1 | 2.1, 2.2, 2.4 |
| 1.3 | 1.1 | 1.4, 1.5, 2.3 |
| 1.4 | 1.1, 1.3 | 2.1, 3.1 |
| 1.5 | 1.1, 1.3 | 2.2 |
| 2.1 | 1.2, 1.4 | 2.4, 3.1 |
| 2.2 | 1.2, 1.5 | 2.3, 3.1 |
| 2.3 | 1.3, 2.2 | 2.5, 3.1 |
| 2.4 | 1.2, 2.1 | 3.1 |
| 2.5 | 2.1, 2.2, 2.3, 2.4 | 3.1 |
| 3.1 | 2.5 | 3.2, 3.3 |
| 3.2 | 3.1 | 3.4 |
| 3.3 | 3.1 | 3.4 |
| 3.4 | 3.2, 3.3 | 3.5 |
| 3.5 | 3.4 | F1-F4 |

### Agent Dispatch Summary

- **Phase 0**: 1 task → `quick` (bug fix)
- **Wave 1**: 5 tasks → `unspecified-high` (core implementation)
- **Wave 2**: 5 tasks → `unspecified-high` (state migration)
- **Wave 3**: 5 tasks → `quick` (testing)
- **FINAL**: 4 tasks → `oracle`, `unspecified-high`, `deep` (verification)

---

## TODOs

### Phase 0: Pre-Flight Bug Fix (Foundation)

- [x] 0.1. Fix finally block bug in NativeAgent

  **What to do**:
  Fix the semantic bug in `src/agentpool/agents/native_agent/agent.py:917` where `_cancelled = True` is always set in the finally block, even on normal completion.

  **Code Change**:
  ```python
  # Before (BUG - line 914-917):
  finally:
      iteration_done.set()
      self._cancelled = True  # Always sets cancelled!

  # After (FIX):
  finally:
      iteration_done.set()
      # Only set cancelled if the iteration task was actually cancelled
      if iteration_task.cancelled():
          self._cancelled = True
  ```

  **Must NOT do**:
  - Do NOT change any other logic in the method
  - Do NOT modify the `_cancelled` checks elsewhere yet (that comes in Wave 1)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: Simple, isolated bug fix with clear before/after pattern

  **Parallelization**:
  - **Can Run In Parallel**: NO (must complete before Wave 1)
  - **Parallel Group**: Phase 0 only
  - **Blocks**: Tasks 1.1-1.5
  - **Blocked By**: None

  **References**:
  - `src/agentpool/agents/native_agent/agent.py:914-917` - The finally block to fix
  - RFC-0021 Section 9.1 - Phase 0 description with exact fix
  - `tests/agents/test_concurrent_safety.py` - Run after fix to verify

  **Acceptance Criteria**:
  - [ ] Bug fix applied correctly (line 917 modified as specified)
  - [ ] `uv run pytest tests/agents/test_concurrent_safety.py::test_single_call_completion -v` passes
  - [ ] All existing tests still pass: `uv run pytest tests/ -x -q`

  **QA Scenarios**:

  ```
  Scenario: Bug fix verification
    Tool: Bash
    Preconditions: Code modified as specified
    Steps:
      1. Run: uv run pytest tests/agents/test_concurrent_safety.py::test_single_call_completion -v
      2. Check: Test passes with no errors
    Expected Result: pytest output shows "PASSED"
    Evidence: .sisyphus/evidence/task-0-1-bug-fix.log

  Scenario: Regression check
    Tool: Bash
    Preconditions: Bug fix applied
    Steps:
      1. Run: uv run pytest tests/agents/ -x -q --tb=short
      2. Check: No test failures
    Expected Result: All agent tests pass
    Evidence: .sisyphus/evidence/task-0-1-regression.log
  ```

  **Commit**: YES
  - Message: `fix(agents): correct finally block to only set cancelled on actual cancellation`
  - Files: `src/agentpool/agents/native_agent/agent.py`

---

### Wave 1: Core Context Creation (Days 1-2)

- [x] 1.1. Create AgentRunContext dataclass

  **What to do**:
  Create the `AgentRunContext` dataclass in a new file or extend existing context.py with per-execution isolated state container.

  **Implementation**:
  ```python
  @dataclass
  class AgentRunContext:
      """Per-execution isolated context for concurrent safety.
      
      Each run_stream() call creates a new AgentRunContext instance,
      ensuring no shared mutable state between concurrent calls.
      """
      # Cancellation state
      cancelled: bool = False
      
      # Task reference
      current_task: asyncio.Task | None = None
      
      # Event queue (isolated per call)
      event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
      
      # Prompt injection state (isolated per call)
      injection_manager: PromptInjectionManager = field(
          default_factory=PromptInjectionManager
      )
      
      # Session identification
      session_id: str = field(default_factory=lambda: str(uuid4()))
      
      # Dependencies passed to run_stream()
      deps: Any = None
      
      # Additional per-call state as needed
      start_time: float = field(default_factory=time.perf_counter)
  ```

  **Must NOT do**:
  - Do NOT modify BaseAgent yet (separate task)
  - Do NOT remove existing instance fields yet (they'll be migrated gradually)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: Creating a new dataclass with no dependencies on existing code

  **Parallelization**:
  - **Can Run In Parallel**: YES (within Wave 1)
  - **Parallel Group**: Wave 1 (with 1.2, 1.3, 1.4, 1.5)
  - **Blocks**: 1.2, 1.3, 1.4, 1.5, 2.1, 2.2
  - **Blocked By**: 0.1

  **References**:
  - RFC-0021 Section 8.2 - Data Model specification
  - `src/agentpool/agents/context.py` - Existing context module to extend
  - `src/agentpool/agents/prompt_injection.py` - PromptInjectionManager import

  **Acceptance Criteria**:
  - [ ] `AgentRunContext` dataclass defined in `src/agentpool/agents/context.py`
  - [ ] All fields from RFC-0021 Section 8.2 present
  - [ ] Dataclass is importable: `from agentpool.agents.context import AgentRunContext`
  - [ ] Type checking passes: `uv run mypy src/agentpool/agents/context.py`

  **QA Scenarios**:

  ```
  Scenario: Dataclass creation
    Tool: Bash
    Preconditions: New code added to context.py
    Steps:
      1. Run: uv run python -c "from agentpool.agents.context import AgentRunContext; print('OK')"
      2. Check: No ImportError
    Expected Result: Prints "OK"
    Evidence: .sisyphus/evidence/task-1-1-import.log

  Scenario: Type checking
    Tool: Bash
    Preconditions: Code added
    Steps:
      1. Run: uv run mypy src/agentpool/agents/context.py
      2. Check: No type errors
    Expected Result: Success or no relevant errors
    Evidence: .sisyphus/evidence/task-1-1-mypy.log
  ```

  **Commit**: NO (groups with Wave 1)

---

- [x] 1.2. Update context.py with AgentRunContext

  **What to do**:
  Add the `AgentRunContext` dataclass to `src/agentpool/agents/context.py` with proper imports and exports.

  **Implementation Details**:
  1. Add imports: `uuid`, `time`, `asyncio`, `dataclass`
  2. Define `AgentRunContext` dataclass (see Task 1.1 for structure)
  3. Ensure `PromptInjectionManager` import works
  4. Add to `__all__` if module uses export pattern

  **Must NOT do**:
  - Do NOT change existing `AgentContext` class (separate concerns)
  - Do NOT break existing imports

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: File modification with clear scope

  **Parallelization**:
  - **Can Run In Parallel**: YES (with 1.1, 1.3, 1.4, 1.5)
  - **Parallel Group**: Wave 1
  - **Blocks**: 2.1, 2.2, 2.4
  - **Blocked By**: 0.1, 1.1

  **References**:
  - `src/agentpool/agents/context.py` - File to modify
  - RFC-0021 Section 8.2 - Exact field specifications

  **Acceptance Criteria**:
  - [ ] `AgentRunContext` defined in context.py
  - [ ] Module imports work without errors
  - [ ] No circular import issues
  - [ ] Existing `AgentContext` still works

  **QA Scenarios**:

  ```
  Scenario: Module import
    Tool: Bash
    Steps:
      1. Run: uv run python -c "from agentpool.agents.context import AgentContext, AgentRunContext; print('Both OK')"
    Expected Result: "Both OK" printed
    Evidence: .sisyphus/evidence/task-1-2-import.log
  ```

  **Commit**: NO (groups with Wave 1)

---

- [x] 1.3. Add context parameter to BaseAgent internal methods

  **What to do**:
  Add `run_ctx: AgentRunContext` parameter to internal methods in `BaseAgent` that will need to access per-call state.

  **Methods to Update** (from Pre-Flight Analysis):
  - `_run_stream_once()` - Main execution method
  - `_stream_events()` - Event streaming
  - `interrupt()` - Cancellation handling
  - `_emit_event()` - Event emission
  - Any other internal methods that access mutable state

  **Implementation Pattern**:
  ```python
  # Before:
  async def _run_stream_once(self, prompts, ...):
      self._cancelled = False

  # After:
  async def _run_stream_once(self, run_ctx: AgentRunContext, prompts, ...):
      run_ctx.cancelled = False
  ```

  **Must NOT do**:
  - Do NOT change public `run_stream()` signature (backward compatibility)
  - Do NOT migrate actual state usage yet (just add parameter)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Requires understanding method signatures and call chains

  **Parallelization**:
  - **Can Run In Parallel**: YES (with 1.1, 1.2, 1.4, 1.5)
  - **Parallel Group**: Wave 1
  - **Blocks**: 1.4, 1.5, 2.3
  - **Blocked By**: 0.1, 1.1

  **References**:
  - `src/agentpool/agents/base_agent.py` - BaseAgent class
  - RFC-0021 Pre-Flight Analysis - Method usage counts

  **Acceptance Criteria**:
  - [ ] `run_ctx` parameter added to all internal methods that need it
  - [ ] Methods still pass existing tests (just signature changes)
  - [ ] No type errors from mypy

  **QA Scenarios**:

  ```
  Scenario: Type check after signature changes
    Tool: Bash
    Steps:
      1. Run: uv run mypy src/agentpool/agents/base_agent.py
      2. Check: No type errors in modified methods
    Expected Result: Clean type check
    Evidence: .sisyphus/evidence/task-1-3-mypy.log
  ```

  **Commit**: NO (groups with Wave 1)

---

- [x] 1.4. Migrate _cancelled to context

  **What to do**:
  Migrate all usages of `_cancelled` from instance-level to `run_ctx.cancelled`.

  **Locations to Update** (from Pre-Flight Analysis):
  - `native_agent.py`: Lines 767, 835, 848, 858, 906, 917
  - `base_agent.py`: Lines 229, 486, 494, 618, 998
  - `claude_code_agent.py`: Line ~120

  **Implementation Pattern**:
  ```python
  # Before:
  if self._cancelled:
      break

  # After:
  if run_ctx.cancelled:
      break
  ```

  **Must NOT do**:
  - Do NOT change other instance fields yet (separate tasks)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Cross-file changes with precise replacements needed

  **Parallelization**:
  - **Can Run In Parallel**: YES (with 1.1, 1.2, 1.3, 1.5)
  - **Parallel Group**: Wave 1
  - **Blocks**: 2.1, 3.1
  - **Blocked By**: 0.1, 1.1, 1.3

  **References**:
  - RFC-0021 Pre-Flight Analysis - _cancelled usage locations
  - `grep -n "_cancelled" src/agentpool/agents/*.py` - Find all occurrences

  **Acceptance Criteria**:
  - [ ] All `_cancelled` usages migrated to `run_ctx.cancelled`
  - [ ] No instance-level `_cancelled` assignments remain
  - [ ] Tests pass: `uv run pytest tests/agents/ -x`

  **QA Scenarios**:

  ```
  Scenario: Migration verification
    Tool: Bash
    Steps:
      1. Run: grep -r "self._cancelled" src/agentpool/agents/
      2. Check: No results (or only in __init__ for backward compat)
    Expected Result: Empty output
    Evidence: .sisyphus/evidence/task-1-4-grep.log

  Scenario: Test after migration
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/agents/test_concurrent_safety.py::test_single_call_completion -v
    Expected Result: Test passes
    Evidence: .sisyphus/evidence/task-1-4-test.log
  ```

  **Commit**: NO (groups with Wave 1)

---

- [x] 1.5. Migrate _current_stream_task to context

  **What to do**:
  Migrate all usages of `_current_stream_task` from instance-level to `run_ctx.current_task`.

  **Locations to Update**:
  - `native_agent.py`: Lines 619, 647, 959
  - `base_agent.py`: Lines 230, 619, 647

  **Implementation Pattern**:
  ```python
  # Before:
  self._current_stream_task = asyncio.current_task()

  # After:
  run_ctx.current_task = asyncio.current_task()
  ```

  **Must NOT do**:
  - Do NOT change interrupt logic yet (that comes in Wave 2)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Cross-file changes affecting task management

  **Parallelization**:
  - **Can Run In Parallel**: YES (with 1.1, 1.2, 1.3, 1.4)
  - **Parallel Group**: Wave 1
  - **Blocks**: 2.2
  - **Blocked By**: 0.1, 1.1, 1.3

  **References**:
  - RFC-0021 Pre-Flight Analysis - _current_stream_task usage

  **Acceptance Criteria**:
  - [ ] All `_current_stream_task` usages migrated to `run_ctx.current_task`
  - [ ] Task assignment and cancellation work correctly
  - [ ] Tests pass

  **QA Scenarios**:

  ```
  Scenario: Task assignment verification
    Tool: Bash
    Steps:
      1. Run: grep -r "_current_stream_task" src/agentpool/agents/
      2. Check: Only in context or removed
    Expected Result: No instance-level usage
    Evidence: .sisyphus/evidence/task-1-5-grep.log
  ```

  **Commit**: YES (Wave 1 complete)
  - Message: `refactor(agents): migrate _cancelled and _current_stream_task to AgentRunContext`
  - Files: All modified files in Wave 1

---

### Wave 2: Full State Migration (Days 3-4)

- [x] 2.1. Migrate _event_queue to context

  **What to do**:
  Migrate `_event_queue` from instance-level to `run_ctx.event_queue`. This is the most complex migration as it's cross-cutting (used by AgentContext, StreamEventEmitter).

  **Locations to Update**:
  - `base_agent.py`: Lines 213 (init), 350 (emit)
  - `context.py`: Line 71 (report_progress)
  - `event_emitter.py`: Line 350 (_emit)

  **Implementation Challenge**:
  Event emitters need access to the run context. Options:
  1. Pass run_ctx through event emission chain
  2. Store run_ctx reference in AgentContext
  3. Use contextvars for implicit access

  **Recommended Approach**: Pass run_ctx explicitly through the call chain.

  **Must NOT do**:
  - Do NOT use contextvars (rejected in RFC as too implicit)
  - Do NOT break event emission for non-concurrent scenarios

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []
  - Reason: Complex cross-cutting changes requiring careful design

  **Parallelization**:
  - **Can Run In Parallel**: YES (with 2.2, 2.3, 2.4, 2.5)
  - **Parallel Group**: Wave 2
  - **Blocks**: 2.4, 3.1
  - **Blocked By**: 1.2, 1.4

  **References**:
  - `src/agentpool/agents/events/event_emitter.py` - EventEmitter class
  - `src/agentpool/agents/context.py` - AgentContext.report_progress
  - RFC-0021 Section 2.3 Pre-Flight Analysis - Cross-cutting dependencies

  **Acceptance Criteria**:
  - [ ] `_event_queue` migrated to `run_ctx.event_queue`
  - [ ] Event emitters can access per-call queue
  - [ ] `test_concurrent_event_isolation` passes

  **QA Scenarios**:

  ```
  Scenario: Event queue isolation
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/agents/test_concurrent_safety.py::test_concurrent_event_isolation -v
    Expected Result: Test passes
    Evidence: .sisyphus/evidence/task-2-1-isolation.log

  Scenario: Event queue cross-contamination
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/agents/test_concurrent_safety.py::test_concurrent_event_queue_isolation -v
    Expected Result: Test passes
    Evidence: .sisyphus/evidence/task-2-1-queue.log
  ```

  **Commit**: NO (groups with Wave 2)

---

- [x] 2.2. Migrate _injection_manager to context

  **What to do**:
  Migrate `_injection_manager` from instance-level to `run_ctx.injection_manager`.

  **Locations to Update**:
  - `base_agent.py`: Lines 231, 532, 551, 555, 559, 563, 621, 625, 645, 648

  **Implementation Pattern**:
  ```python
  # Before:
  self._injection_manager.add_prompt(...)

  # After:
  run_ctx.injection_manager.add_prompt(...)
  ```

  **Must NOT do**:
  - Do NOT change PromptInjectionManager behavior

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Localized changes but many call sites

  **Parallelization**:
  - **Can Run In Parallel**: YES (with 2.1, 2.3, 2.4, 2.5)
  - **Parallel Group**: Wave 2
  - **Blocks**: 2.3, 3.1
  - **Blocked By**: 1.2, 1.5

  **References**:
  - RFC-0021 Pre-Flight Analysis - _injection_manager usage

  **Acceptance Criteria**:
  - [ ] All `_injection_manager` usages migrated
  - [ ] Prompt injection still works correctly
  - [ ] No shared injection state between concurrent calls

  **QA Scenarios**:

  ```
  Scenario: Injection manager isolation
    Tool: Bash
    Steps:
      1. Run: grep -r "_injection_manager" src/agentpool/agents/
      2. Check: No instance-level usage
    Expected Result: Only context-level usage
    Evidence: .sisyphus/evidence/task-2-2-grep.log
  ```

  **Commit**: NO (groups with Wave 2)

---

- [x] 2.3. Update NativeAgent for context compatibility

  **What to do**:
  Ensure `NativeAgent` subclass properly uses the context-based state from `BaseAgent`.

  **Changes Needed**:
  1. Update any direct `_cancelled` access to use context
  2. Update any direct `_current_stream_task` access
  3. Ensure `run_stream()` creates and passes run_ctx

  **Must NOT do**:
  - Do NOT duplicate state in NativeAgent
  - Do NOT break NativeAgent-specific functionality

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Subclass-specific adaptation required

  **Parallelization**:
  - **Can Run In Parallel**: YES (with 2.1, 2.2, 2.4, 2.5)
  - **Parallel Group**: Wave 2
  - **Blocks**: 2.5, 3.1
  - **Blocked By**: 1.3, 2.2

  **References**:
  - `src/agentpool/agents/native_agent/agent.py` - NativeAgent implementation
  - RFC-0021 Pre-Flight Analysis - NativeAgent risk assessment

  **Acceptance Criteria**:
  - [ ] NativeAgent uses context-based state
  - [ ] `test_native_agent_concurrent` passes
  - [ ] No regression in NativeAgent features

  **QA Scenarios**:

  ```
  Scenario: NativeAgent concurrent test
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/agents/test_concurrent_safety.py::test_native_agent_concurrent -v
    Expected Result: Test passes
    Evidence: .sisyphus/evidence/task-2-3-native.log
  ```

  **Commit**: NO (groups with Wave 2)

---

- [x] 2.4. Update event emitter for context access

  **What to do**:
  Update `StreamEventEmitter` to use per-call event queue from context.

  **Implementation**:
  Modify `_emit()` method to use `run_ctx.event_queue` instead of `self._context.agent._event_queue`.

  **Pattern**:
  ```python
  # Before:
  await self._context.agent._event_queue.put(event)

  # After:
  await run_ctx.event_queue.put(event)
  ```

  **Must NOT do**:
  - Do NOT change event emission semantics

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Core event system modification

  **Parallelization**:
  - **Can Run In Parallel**: YES (with 2.1, 2.2, 2.3, 2.5)
  - **Parallel Group**: Wave 2
  - **Blocks**: 3.1
  - **Blocked By**: 1.2, 2.1

  **References**:
  - `src/agentpool/agents/events/event_emitter.py` - EventEmitter._emit

  **Acceptance Criteria**:
  - [ ] Event emitter uses context queue
  - [ ] Events correctly routed to per-call queue
  - [ ] No event loss or cross-contamination

  **QA Scenarios**:

  ```
  Scenario: Event emission test
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/agents/test_concurrent_safety.py::test_concurrent_event_isolation -v
    Expected Result: Test passes
    Evidence: .sisyphus/evidence/task-2-4-emitter.log
  ```

  **Commit**: NO (groups with Wave 2)

---

- [x] 2.5. Ensure proper cleanup in finally blocks

  **What to do**:
  Review and update all `finally` blocks to properly clean up per-call context without affecting other concurrent calls.

  **Key Areas**:
  - NativeAgent finally block (already partially fixed in Phase 0)
  - BaseAgent cleanup
  - Event queue cleanup

  **Must do**:
  - Ensure context is cleaned up after each call
  - Do NOT set shared state in finally blocks
  - Handle cancellation properly

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Cleanup logic critical for stability

  **Parallelization**:
  - **Can Run In Parallel**: YES (with 2.1, 2.2, 2.3, 2.4)
  - **Parallel Group**: Wave 2
  - **Blocks**: 3.1
  - **Blocked By**: 2.1, 2.2, 2.3, 2.4

  **References**:
  - All finally blocks in modified files

  **Acceptance Criteria**:
  - [ ] All finally blocks reviewed and updated
  - [ ] No shared state pollution from cleanup
  - [ ] Cancellation isolation works

  **QA Scenarios**:

  ```
  Scenario: Cancellation isolation
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/agents/test_concurrent_safety.py::test_concurrent_cancellation_isolation -v
    Expected Result: Test passes
    Evidence: .sisyphus/evidence/task-2-5-cancellation.log
  ```

  **Commit**: YES (Wave 2 complete)
  - Message: `refactor(agents): migrate _event_queue and _injection_manager to AgentRunContext`
  - Files: All Wave 2 modified files

---

### Wave 3: Testing & Validation (Days 5-7)

- [x] 3.1. Run concurrent safety test suite

  **What to do**:
  Run the complete concurrent safety test suite to validate all migrations.

  **Test Commands**:
  ```bash
  uv run pytest tests/agents/test_concurrent_safety.py -v
  ```

  **Expected Results**:
  - All baseline tests pass
  - All concurrent isolation tests pass
  - All stress tests pass

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: Running existing test suite

  **Parallelization**:
  - **Can Run In Parallel**: NO (sequential testing)
  - **Parallel Group**: Wave 3
  - **Blocks**: 3.2, 3.3
  - **Blocked By**: 2.5

  **References**:
  - `tests/agents/test_concurrent_safety.py` - Full test suite

  **Acceptance Criteria**:
  - [ ] All concurrent safety tests pass
  - [ ] Success rate: 100% for concurrent calls

  **QA Scenarios**:

  ```
  Scenario: Full concurrent test suite
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/agents/test_concurrent_safety.py -v
      2. Check: All tests pass
    Expected Result: 100% pass rate
    Evidence: .sisyphus/evidence/task-3-1-full-suite.log

  Scenario: Concurrent calls completion
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/agents/test_concurrent_safety.py::test_concurrent_calls_complete -v
    Expected Result: Test passes
    Evidence: .sisyphus/evidence/task-3-1-completion.log
  ```

  **Commit**: NO (testing phase)

---

- [x] 3.2. Run full existing test suite (regression check)

  **What to do**:
  Run the complete existing test suite to ensure no regressions from the refactoring.

  **Test Commands**:
  ```bash
  uv run pytest tests/ -x --tb=short
  ```

  **Expected Results**:
  - All existing tests pass
  - No new failures introduced

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: Regression testing

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocks**: 3.4
  - **Blocked By**: 3.1

  **References**:
  - `tests/` - Full test directory

  **Acceptance Criteria**:
  - [ ] All existing tests pass
  - [ ] No regressions in agent functionality

  **QA Scenarios**:

  ```
  Scenario: Full regression test
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/ -x --tb=short -q
      2. Check: No failures
    Expected Result: All tests pass
    Evidence: .sisyphus/evidence/task-3-2-regression.log
  ```

  **Commit**: NO

---

- [x] 3.3. Performance benchmarks

  **What to do**:
  Run performance benchmarks to verify:
  1. Serial execution performance unchanged (±5%)
  2. Concurrent execution shows speedup (>1.5x for 3 parallel tasks)

  **Test Commands**:
  ```bash
  uv run pytest tests/agents/test_concurrent_safety.py::test_serial_performance_baseline -v -s
  uv run pytest tests/agents/test_concurrent_safety.py::test_concurrent_performance -v -s
  ```

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: Running benchmark tests

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocks**: 3.4
  - **Blocked By**: 3.1

  **References**:
  - RFC-0021 Section 4.3 - Success Criteria

  **Acceptance Criteria**:
  - [ ] Serial performance within ±5% of baseline
  - [ ] Concurrent performance shows >1.5x speedup

  **QA Scenarios**:

  ```
  Scenario: Performance benchmark
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/agents/test_concurrent_safety.py::test_concurrent_performance -v -s
      2. Check: Speedup > 1.5x reported
    Expected Result: Speedup >= 1.5x
    Evidence: .sisyphus/evidence/task-3-3-performance.log
  ```

  **Commit**: NO

---

- [x] 3.4. Subclass compatibility verification

  **What to do**:
  Verify that agent subclasses (ACPAgent, AGUIAgent, ClaudeCodeAgent) work correctly with the new context system.

  **Subclasses to Test**:
  - ACPAgent
  - AGUIAgent
  - ClaudeCodeAgent

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Cross-subclass verification

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocks**: 3.5
  - **Blocked By**: 3.2, 3.3

  **References**:
  - RFC-0021 Pre-Flight Analysis - Subclass audit
  - `src/agentpool/agents/acp_agent/` - ACPAgent
  - `src/agentpool/agents/agui_agent/` - AGUIAgent
  - `src/agentpool/agents/claude_code_agent/` - ClaudeCodeAgent

  **Acceptance Criteria**:
  - [ ] All subclasses compile without errors
  - [ ] Subclass-specific tests pass
  - [ ] No subclass regressions

  **QA Scenarios**:

  ```
  Scenario: Subclass type check
    Tool: Bash
    Steps:
      1. Run: uv run mypy src/agentpool/agents/acp_agent/ src/agentpool/agents/agui_agent/ src/agentpool/agents/claude_code_agent/
    Expected Result: No type errors
    Evidence: .sisyphus/evidence/task-3-4-mypy.log

  Scenario: Subclass tests
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/agents/acp_agent/ tests/agents/agui_agent/ tests/agents/claude_code_agent/ -x --tb=short
    Expected Result: All tests pass
    Evidence: .sisyphus/evidence/task-3-4-tests.log
  ```

  **Commit**: NO

---

- [x] 3.5. Documentation updates

  **What to do**:
  Update documentation to reflect the new concurrent safety features.

  **Documentation to Update**:
  1. RFC-0021 status change (DRAFT → ACCEPTED)
  2. Add migration guide for custom subclasses
  3. Update agent usage documentation

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Skills**: []
  - Reason: Documentation writing

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocks**: F1-F4
  - **Blocked By**: 3.4

  **References**:
  - RFC-0021 Appendix A - Migration Guide template

  **Acceptance Criteria**:
  - [ ] RFC-0021 status updated
  - [ ] Migration guide added
  - [ ] Documentation reflects new capabilities

  **QA Scenarios**:

  ```
  Scenario: Documentation review
    Tool: Read
    Steps:
      1. Read: docs/rfcs/draft/RFC-0021-agent-concurrent-execution-safety.md
      2. Check: Status shows ACCEPTED
      3. Check: Migration guide present
    Expected Result: Documentation complete
    Evidence: .sisyphus/evidence/task-3-5-docs.md
  ```

  **Commit**: YES
  - Message: `docs(rfc): update RFC-0021 status and add migration guide`
  - Files: All documentation files

---

## Final Verification Wave (After ALL implementation)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [x] F1. **Plan Compliance Audit** — `oracle`

  Read the RFC-0021 end-to-end. For each "Must Have": verify implementation exists (read file, check method signatures, verify state migration). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check that all P0 and P1 state fields are migrated. Compare deliverables against plan.

  **Output Format**:
  ```
  Must Have [N/N] | Must NOT Have [N/N] | P0 Fields Migrated [Y/N] | P1 Fields Migrated [Y/N] | VERDICT: APPROVE/REJECT
  ```

  **QA Scenarios**:

  ```
  Scenario: Compliance verification
    Tool: Grep + Read
    Steps:
      1. Grep for "_cancelled" instance usage
      2. Grep for "_current_stream_task" instance usage  
      3. Grep for "_event_queue" instance usage
      4. Grep for "_injection_manager" instance usage
      5. Read: src/agentpool/agents/context.py for AgentRunContext
      6. Read: src/agentpool/agents/base_agent.py for context usage
    Expected Result: All P0/P1 fields migrated, no forbidden patterns
    Evidence: .sisyphus/evidence/f1-compliance.log
  ```

---

- [x] F2. **Code Quality Review** — `unspecified-high`

  Run full quality checks:
  ```bash
  uv run ruff check src/agentpool/agents/
  uv run mypy src/agentpool/agents/
  uv run ruff format --check src/agentpool/agents/
  ```

  Review all changed files for:
  - `as any` / `@ts-ignore` type escapes
  - Empty except blocks
  - Unused imports
  - Overly complex functions
  - AI slop patterns (excessive comments, generic names)

  **Output Format**:
  ```
  Lint [PASS/FAIL] | Type Check [PASS/FAIL] | Format [PASS/FAIL] | Code Issues [N] | VERDICT
  ```

  **QA Scenarios**:

  ```
  Scenario: Quality checks
    Tool: Bash
    Steps:
      1. Run: uv run ruff check src/agentpool/agents/
      2. Run: uv run mypy src/agentpool/agents/
      3. Run: uv run ruff format --check src/agentpool/agents/
    Expected Result: All checks pass
    Evidence: .sisyphus/evidence/f2-quality.log
  ```

---

- [x] F3. **Full Test Suite Verification** — `unspecified-high`

  Execute comprehensive test suite:
  ```bash
  # Concurrent safety tests
  uv run pytest tests/agents/test_concurrent_safety.py -v

  # All agent tests
  uv run pytest tests/agents/ -v --tb=short

  # Full test suite
  uv run pytest tests/ -x --tb=short
  ```

  **Output Format**:
  ```
  Concurrent Tests [N/N] | Agent Tests [N/N] | Full Suite [N/N] | Coverage [%] | VERDICT
  ```

  **QA Scenarios**:

  ```
  Scenario: Full test verification
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/agents/test_concurrent_safety.py -v
      2. Run: uv run pytest tests/ -x --tb=short -q
    Expected Result: 100% pass rate
    Evidence: .sisyphus/evidence/f3-tests.log
  ```

---

- [x] F4. **Scope Fidelity Check** — `deep`

  Verify the implementation matches the RFC exactly:

  1. **State Migration Verification**:
     - Check all P0 fields migrated: `_cancelled`, `_current_stream_task`
     - Check all P1 fields migrated: `_event_queue`, `_injection_manager`

  2. **API Compatibility**:
     - Verify `run_stream()` signature unchanged
     - Verify backward compatibility maintained

  3. **Architecture Compliance**:
     - Verify `AgentRunContext` matches RFC specification
     - Verify context passing pattern used consistently

  4. **No Scope Creep**:
     - Verify `_formatted_system_prompt` NOT migrated
     - Verify `_internal_fs` NOT migrated

  **Output Format**:
  ```
  P0 Migration [Y/N] | P1 Migration [Y/N] | API Compatible [Y/N] | No Creep [Y/N] | VERDICT
  ```

  **QA Scenarios**:

  ```
  Scenario: Architecture verification
    Tool: Read + Grep
    Steps:
      1. Read: src/agentpool/agents/context.py - verify AgentRunContext structure
      2. Grep: "run_stream" signature in base_agent.py - verify unchanged
      3. Grep: "_formatted_system_prompt" - verify still instance-level
      4. Grep: "_internal_fs" - verify still instance-level
    Expected Result: All checks pass
    Evidence: .sisyphus/evidence/f4-fidelity.log
  ```

---

## Commit Strategy

### Phase Commits

| Phase | Commit Message | Files |
|-------|---------------|-------|
| 0.1 | `fix(agents): correct finally block to only set cancelled on actual cancellation` | `native_agent.py` |
| 1.1-1.5 | `refactor(agents): migrate _cancelled and _current_stream_task to AgentRunContext` | `context.py`, `base_agent.py` |
| 2.1-2.5 | `refactor(agents): migrate _event_queue and _injection_manager to AgentRunContext` | `base_agent.py`, `native_agent.py`, `event_emitter.py` |
| 3.5 | `docs(rfc): update RFC-0021 status and add migration guide` | `docs/rfcs/` |

### Pre-Commit Checks
Each commit must pass:
```bash
uv run ruff check src/
uv run mypy src/agentpool/agents/
uv run pytest tests/agents/ -x -q
```

---

## Success Criteria

### Functional Criteria
- [ ] 3+ concurrent `run_stream()` calls to same agent complete successfully
- [ ] No shared state pollution (verified by tests)
- [ ] Cancellation isolation works (one call cancelled doesn't affect others)
- [ ] Event queue isolation works (no cross-contamination)

### Performance Criteria
- [ ] Serial execution performance unchanged (±5%)
- [ ] Concurrent execution shows speedup >1.5x for 3 parallel tasks

### Quality Criteria
- [ ] All existing tests pass without modification
- [ ] New concurrent safety tests passing
- [ ] Type checking passes (mypy)
- [ ] Linting passes (ruff)

### Documentation Criteria
- [ ] RFC-0021 status updated to ACCEPTED
- [ ] Migration guide for subclasses added
- [ ] Code comments explain context usage

### Verification Commands
```bash
# Quick validation
uv run pytest tests/agents/test_concurrent_safety.py -v

# Full validation
uv run pytest tests/ -x --tb=short

# Quality checks
uv run ruff check src/ && uv run mypy src/agentpool/agents/
```

### Final Checklist
- [ ] All "Must Have" present in implementation
- [ ] All "Must NOT Have" absent from implementation
- [ ] All P0 state fields migrated (`_cancelled`, `_current_stream_task`)
- [ ] All P1 state fields migrated (`_event_queue`, `_injection_manager`)
- [ ] Finally block bug fixed
- [ ] Backward compatibility maintained
- [ ] All tests passing
- [ ] Documentation updated

---

## Appendix: Quick Reference

### File Locations
| File | Purpose |
|------|---------|
| `src/agentpool/agents/context.py` | AgentRunContext definition |
| `src/agentpool/agents/base_agent.py` | BaseAgent with context migration |
| `src/agentpool/agents/native_agent/agent.py` | NativeAgent fixes |
| `src/agentpool/agents/events/event_emitter.py` | Event emitter updates |
| `tests/agents/test_concurrent_safety.py` | Test suite |

### Key State Fields
| Field | Priority | Migration Target |
|-------|----------|------------------|
| `_cancelled` | P0 | `run_ctx.cancelled` |
| `_current_stream_task` | P0 | `run_ctx.current_task` |
| `_event_queue` | P1 | `run_ctx.event_queue` |
| `_injection_manager` | P1 | `run_ctx.injection_manager` |
| `_formatted_system_prompt` | DO NOT | Keep instance-level |
| `_internal_fs` | DO NOT | Keep instance-level |

### Test Commands
```bash
# Run specific test
uv run pytest tests/agents/test_concurrent_safety.py::test_concurrent_calls_complete -v

# Run all concurrent tests
uv run pytest tests/agents/test_concurrent_safety.py -v

# Run with coverage
uv run pytest tests/agents/test_concurrent_safety.py --cov=src/agentpool/

# Quick validation script
python tests/agents/run_concurrent_tests.py
```

---

*Plan generated for RFC-0021: Agent Concurrent Execution Safety*
*Status: READY FOR EXECUTION*
*Run `/start-work` to begin implementation*

