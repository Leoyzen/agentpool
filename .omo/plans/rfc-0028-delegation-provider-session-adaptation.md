# RFC-0028 Delegation Provider Session Adaptation Work Plan

## TL;DR

> **Quick Summary**: Implement RFC-0028 by making child session creation flow through `SessionManager.create_child_session()` while preserving provider-owned `SpawnSessionStart` emission and preventing OpenCode `ensure_session()` from overwriting proactively persisted session data.
>
> **Deliverables**:
> - Store-first OpenCode `ensure_session()` safety gate.
> - Depth propagation via `BaseAgent.run_stream(depth=...)` and `AgentRunContext.depth`.
> - `AgentContext.create_child_session()` convenience API.
> - Session-aware `SubagentTools`, `WorkersTools`, `Team`, `TeamRun`, and ACP child sessions.
> - Type-safe `agent_type` / `source_type` helpers and depth overflow guard.
> - Tests covering RFC TG-1 through TG-32 where in scope.
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 4 waves + final verification
> **Critical Path**: T1/T2 → T7 → T8 → T9/T10/T11/T12/T13/T14 → T15 → Final Verification

---

## Context

### Original Request
User requested: `make a plan for @docs/rfcs/draft/RFC-0028-delegation-provider-session-adaptation.md`.

### Interview Summary
**Key Discussions**:
- The RFC is the source of truth for desired behavior.
- Use RFC Option 1: providers call `SessionManager.create_child_session()` and still emit lifecycle events themselves.
- Existing pytest infrastructure should be used; tests-after is the selected strategy for this refactor.

**Research Findings**:
- `src/agentpool/sessions/manager.py:SessionManager.create_child_session()` already exists and persists inherited `project_id` / `cwd`.
- `src/agentpool/agents/context.py:AgentRunContext` currently has no `depth`; `BaseAgent.run_stream()` constructs it with `AgentRunContext(deps=deps)` only.
- `src/agentpool_toolsets/builtin/subagent_tools.py` emits `SpawnSessionStart` from both `task()` and `_stream_task()` and uses nonexistent `ctx.current_depth` through `getattr`.
- `src/agentpool_toolsets/builtin/workers.py` hardcodes `depth=1`.
- `src/agentpool/delegation/team.py` and `src/agentpool/delegation/teamrun.py` wrap events but do not emit `SpawnSessionStart` or propagate session IDs.
- `src/agentpool_server/opencode_server/state.py:ensure_session()` can overwrite `SessionData`; store-first behavior is a prerequisite before provider adaptation.
- `tests/sessions/test_session_hierarchy.py` is currently skipped and must be unblocked early.

### Metis / Oracle Review
**Identified Gaps** (addressed):
- `ensure_session()` must move earlier than provider adaptation to prevent data loss.
- `Team.run()` / `TeamRun.run()` non-streaming paths are not covered by the RFC and must be explicitly out of scope.
- `EventManager._forward_to_parent()` and `agentpool_commands/pool.py` depth behavior are related but out of scope.
- Team/TeamRun must pop `session_id` and `depth` from `**kwargs` before forwarding to avoid duplicate keyword errors.
- Session ID format changes from `identifier.ascending("session")` to `generate_session_id()` are accepted because session IDs are opaque.

---

## Work Objectives

### Core Objective
Unify child session lifecycle for delegation providers by proactively persisting child `SessionData` through `SessionManager.create_child_session()` and ensuring all streamed delegation events carry consistent session IDs and depth.

### Concrete Deliverables
- Updated session/depth context primitives.
- Safe OpenCode store-first session materialization.
- Adapted SubagentTools, WorkersTools, Team, TeamRun, and ACP session manager child paths.
- Targeted tests for session hierarchy, event ordering, depth propagation, and store overwrite prevention.

### Definition of Done
- [x] `uv run pytest tests/sessions/ tests/servers/opencode_server/ tests/tools/test_workers.py tests/toolsets/test_subagent_async.py tests/teams/ tests/servers/acp_server/ tests/messaging/ tests/test_events.py -v` passes.
- [x] `uv run --no-group docs mypy src/` passes.
- [x] `duty lint` passes.
- [x] `pool.sessions.get_child_sessions(parent_id)` returns child sessions for adapted streamed delegation paths.

### Must Have
- Providers call `create_child_session()` for child session creation when a pool/session store is available.
- `SpawnSessionStart` remains emitted by providers, not `SessionManager`.
- `ensure_session()` does not overwrite `SessionData` created by `create_child_session()`.
- Team/TeamRun streamed members emit `SpawnSessionStart` and preserve child/parent session IDs in `SubAgentEvent` wrappers.
- Depth increments from `AgentRunContext.depth` / `run_stream(depth=...)`, not instance state or `getattr`.

### Must NOT Have (Guardrails)
- No changes to `Team.run()` / `TeamRun.run()` / `BaseTeam.execute()` non-streaming paths.
- No changes to `EventManager._forward_to_parent()`.
- No changes to `agentpool_commands/pool.py` CLI depth hardcode.
- No `DelegationProvider` base class.
- No `SessionManager.create_top_level_session()`.
- No session schema migration, cleanup/eviction, `SpawnSessionEnd`, MCP server isolation, or OpenCode protocol changes.
- No `SpawnSessionStart` emission from `SessionManager.create_child_session()`.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — all verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Tests-after
- **Framework**: pytest via `uv run pytest`
- **Agent-Executed QA**: ALWAYS; each task captures evidence under `.sisyphus/evidence/`.

### QA Policy
- **Backend/module**: Use Bash with `uv run pytest`, `uv run --no-group docs mypy`, and targeted Python assertions.
- **Server/session behavior**: Use pytest fixtures under `tests/servers/opencode_server/` and `tests/servers/acp_server/`.
- **Evidence**: Save command outputs to `.sisyphus/evidence/task-{N}-{scenario}.txt`.

---

## Execution Strategy

### Parallel Execution Waves

```text
Wave 0 (Foundation, parallel):
├── T1 AgentRunContext/BaseAgent depth plumbing
├── T2 Delegation depth exception and cap
├── T3 Type-safe node/source helpers
├── T4 Unblock session hierarchy tests
├── T5 AgentContext child-session API
└── T6 Session ID format dependency audit

Wave 1 (Safety gate, parallel but must finish before Wave 2):
├── T7 OpenCode ensure_session store-first path
└── T8 AgentRunContext.session_id deprecation guard

Wave 2 (Provider adaptation, max parallel after Wave 1):
├── T9 SubagentTools adaptation
├── T10 WorkersTools adaptation
├── T11 Team streamed session adaptation
├── T12 TeamRun streamed session adaptation
├── T13 ACPSessionManager child-session path
└── T14 Cross-provider event/depth tests

Wave 3 (Cleanup + integration):
├── T15 Remove legacy provider session/depth patterns
└── T16 Broad validation and regression sweep

Wave FINAL:
├── F1 Plan compliance audit (oracle)
├── F2 Code quality review (unspecified-high)
├── F3 Real QA scenario execution (unspecified-high)
└── F4 Scope fidelity check (deep)
```

### Dependency Matrix

| Task | Depends On | Blocks |
|---|---|---|
| T1 | None | T9, T10, T11, T12, T14 |
| T2 | None | T9, T10, T11, T12, T14 |
| T3 | None | T11, T12, T14 |
| T4 | None | T16 |
| T5 | T1 | T9, T10 |
| T6 | None | T15 |
| T7 | None | T9, T10, T11, T12, T13, T14 |
| T8 | None | T16 |
| T9 | T1, T2, T5, T7 | T14, T15, T16 |
| T10 | T1, T2, T5, T7 | T14, T15, T16 |
| T11 | T1, T2, T3, T7 | T14, T15, T16 |
| T12 | T1, T2, T3, T7 | T14, T15, T16 |
| T13 | T7 | T14, T16 |
| T14 | T9, T10, T11, T12, T13 | T16 |
| T15 | T9, T10, T11, T12, T13, T6 | T16 |
| T16 | T4, T8, T14, T15 | Final |

### Agent Dispatch Summary
- **Wave 0**: 6 tasks — T1/T2/T3/T5/T6 `quick`, T4 `unspecified-high`
- **Wave 1**: 2 tasks — T7 `deep`, T8 `unspecified-high`
- **Wave 2**: 6 tasks — T9/T10 `unspecified-high`, T11/T12 `deep`, T13 `unspecified-high`, T14 `deep`
- **Wave 3**: 2 tasks — T15 `quick`, T16 `unspecified-high`

---

## TODOs

> Implementation + tests belong in the same task. Every task includes mandatory agent-executed QA.

- [x] 1. Add depth plumbing to `AgentRunContext` and `BaseAgent.run_stream()`

  **What to do**:
  - Add `depth: int = 0` to `AgentRunContext` in `src/agentpool/agents/context.py`.
  - Add explicit `depth: int = 0` parameter to `BaseAgent.run_stream()` in `src/agentpool/agents/base_agent.py`.
  - Construct `AgentRunContext(deps=deps, depth=depth)`.
  - Add tests confirming agent subclasses accept `depth=` through inherited `run_stream()`.

  **Must NOT do**:
  - Do not add `**kwargs` catch-all to `BaseAgent.run_stream()`.
  - Do not change `_run_stream_once()` signatures unless tests prove it is required.

  **Recommended Agent Profile**:
  - **Category**: `quick` — focused two-file API wiring.
  - **Skills**: []
  - **Skills Evaluated but Omitted**: `systematic-debugging` — no bug yet, this is planned wiring.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 0
  - **Blocks**: T5, T9, T10, T11, T12, T14
  - **Blocked By**: None

  **References**:
  - `src/agentpool/agents/context.py:AgentRunContext` — add depth field without breaking existing fields.
  - `src/agentpool/agents/base_agent.py:BaseAgent.run_stream` — explicit typed signature and `AgentRunContext(deps=deps)` construction.
  - `docs/rfcs/draft/RFC-0028-delegation-provider-session-adaptation.md:528-578` — RFC depth signature design.
  - `tests/agents/` — existing agent run_stream patterns.

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/agents/ -k "run_stream or context" -v` passes.
  - [ ] New/updated test proves `agent.run_stream("test", depth=1)` does not raise `TypeError`.

  **QA Scenarios**:
  ```text
  Scenario: depth parameter accepted by run_stream
    Tool: Bash
    Preconditions: implementation complete
    Steps:
      1. Run `uv run pytest tests/agents/ -k "depth or run_stream" -v`.
      2. Save complete output.
    Expected Result: pytest exits 0 and includes a test proving `depth=1` is accepted.
    Failure Indicators: TypeError mentioning `depth`, failing context construction, or non-zero exit.
    Evidence: .sisyphus/evidence/task-1-depth-run-stream.txt

  Scenario: default depth remains zero
    Tool: Bash
    Preconditions: implementation complete
    Steps:
      1. Run targeted context test that instantiates `AgentRunContext(deps=None)`.
      2. Assert `ctx.depth == 0`.
    Expected Result: assertion passes.
    Failure Indicators: missing attribute, non-zero exit, or default not 0.
    Evidence: .sisyphus/evidence/task-1-depth-default.txt
  ```

  **Commit**: YES
  - Message: `feat(sessions): add delegation depth plumbing`

- [x] 2. Add delegation depth guard primitives

  **What to do**:
  - Add `DelegationDepthError` and `MAX_DELEGATION_DEPTH: int = 10` to `src/agentpool/agents/exceptions.py`.
  - Add focused tests for importability and raising behavior.

  **Must NOT do**:
  - Do not wire the guard into providers in this task.

  **Recommended Agent Profile**:
  - **Category**: `quick` — one small module plus test.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 0
  - **Blocks**: T9, T10, T11, T12, T14
  - **Blocked By**: None

  **References**:
  - `src/agentpool/agents/exceptions.py` — existing agent-specific exceptions.
  - RFC lines 1121-1152 — depth overflow guard design.

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/agents/ -k "depth_overflow or exceptions" -v` passes.

  **QA Scenarios**:
  ```text
  Scenario: depth guard primitives import
    Tool: Bash
    Preconditions: exception and constant added
    Steps:
      1. Run `uv run python -c "from agentpool.agents.exceptions import DelegationDepthError, MAX_DELEGATION_DEPTH; assert MAX_DELEGATION_DEPTH == 10; raise DelegationDepthError('x')"`.
    Expected Result: command exits non-zero due to intentional exception and traceback includes `DelegationDepthError`.
    Failure Indicators: ImportError or wrong constant value.
    Evidence: .sisyphus/evidence/task-2-depth-guard-import.txt

  Scenario: tests validate guard
    Tool: Bash
    Preconditions: test added
    Steps:
      1. Run `uv run pytest tests/agents/ -k "DelegationDepthError or MAX_DELEGATION_DEPTH" -v`.
    Expected Result: pytest exits 0.
    Failure Indicators: missing test, missing symbol, or wrong cap.
    Evidence: .sisyphus/evidence/task-2-depth-guard-tests.txt
  ```

  **Commit**: YES
  - Message: `feat(agents): add delegation depth guard`

- [x] 3. Add type-safe agent/source type helpers

  **What to do**:
  - Add `MessageNode.agent_type` property for persistence-domain values.
  - Add `SourceType` alias and `get_source_type(node)` helper returning only `"agent"`, `"team_parallel"`, or `"team_sequential"`.
  - Fix broken `TYPE_CHECKING` imports in `team.py` and `teamrun.py` to use `SourceType`.
  - Add warning behavior for unknown node types defaulting to `"agent"`.

  **Must NOT do**:
  - Do not add a `source_type` ClassVar to every node class.
  - Do not change `SessionData.agent_type` schema.

  **Recommended Agent Profile**:
  - **Category**: `quick` — type helper plus tests.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 0
  - **Blocks**: T11, T12, T14
  - **Blocked By**: None

  **References**:
  - `src/agentpool/messaging/messagenode.py` — `MessageNode` base class.
  - `src/agentpool/delegation/team.py` and `src/agentpool/delegation/teamrun.py` — current match/source type logic and broken `SubAgentType` import.
  - `src/agentpool/agents/events/events.py:SpawnSessionStart, SubAgentEvent` — valid `source_type` literal domain.
  - RFC lines 1000-1082 — two-domain type design.

  **Acceptance Criteria**:
  - [ ] Tests verify native agent / Team / TeamRun return correct `agent_type` and `get_source_type()` values.
  - [ ] Unknown `MessageNode` subclass logs or warns and returns `"agent"`.

  **QA Scenarios**:
  ```text
  Scenario: helper returns valid domains
    Tool: Bash
    Preconditions: helpers implemented
    Steps:
      1. Run `uv run pytest tests/messaging/ -k "agent_type or source_type" -v`.
    Expected Result: pytest exits 0 and asserts Team source type is `team_parallel`, TeamRun is `team_sequential`.
    Failure Indicators: `native` used as source_type, ImportError, or broken circular import.
    Evidence: .sisyphus/evidence/task-3-source-type-tests.txt

  Scenario: circular import safety
    Tool: Bash
    Preconditions: `MessageNode.agent_type` implemented with local imports
    Steps:
      1. Run `uv run python -c "import importlib, agentpool.messaging.messagenode as m; importlib.reload(m)"`.
    Expected Result: command exits 0.
    Failure Indicators: ImportError or circular import traceback.
    Evidence: .sisyphus/evidence/task-3-circular-import.txt
  ```

  **Commit**: YES
  - Message: `feat(delegation): split agent and source type helpers`

- [x] 4. Unblock and modernize session hierarchy tests

  **What to do**:
  - Inspect `tests/sessions/test_session_hierarchy.py` and remove obsolete skip pattern where `SessionManager = None`.
  - Update fixtures/imports to use current `SessionManager`, `SessionData`, and stores.
  - Ensure parent/child/nested hierarchy tests actually run.

  **Must NOT do**:
  - Do not weaken assertions to make tests pass.
  - Do not delete skipped tests; revive them.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — test resurrection may require fixture understanding.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 0
  - **Blocks**: T16
  - **Blocked By**: None

  **References**:
  - `tests/sessions/test_session_hierarchy.py` — skipped hierarchy tests to revive.
  - `tests/sessions/test_session_manager.py` — current SessionManager test patterns.
  - `src/agentpool/sessions/manager.py` and `src/agentpool/sessions/models.py` — current APIs.

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/sessions/test_session_hierarchy.py -v` runs real tests, not all skipped.
  - [ ] Parent, child, and nested hierarchy assertions pass.

  **QA Scenarios**:
  ```text
  Scenario: hierarchy tests are unskipped
    Tool: Bash
    Preconditions: test file updated
    Steps:
      1. Run `uv run pytest tests/sessions/test_session_hierarchy.py -v`.
      2. Inspect output for passed tests and absence of `skipped` for the whole file.
    Expected Result: pytest exits 0 with hierarchy tests passing.
    Failure Indicators: all tests skipped, import errors, or weakened empty assertions.
    Evidence: .sisyphus/evidence/task-4-hierarchy-tests.txt

  Scenario: missing parent edge case remains explicit
    Tool: Bash
    Preconditions: hierarchy tests revived
    Steps:
      1. Run `uv run pytest tests/sessions/test_session_manager.py -k "child or parent" -v`.
    Expected Result: tests cover parent inheritance and missing parent behavior.
    Failure Indicators: no tests selected or behavior undefined.
    Evidence: .sisyphus/evidence/task-4-parent-edge.txt
  ```

  **Commit**: YES
  - Message: `test(sessions): revive session hierarchy coverage`

- [x] 5. Add `AgentContext.create_child_session()` convenience API

  **What to do**:
  - Add async method to `AgentContext` using `self.node.agent_pool.sessions.create_child_session()` when available.
  - Accept `agent_name`, `agent_type`, and optional `parent_session_id`.
  - Fall back to `generate_session_id()` without persistence if no pool is available.
  - Test pool-backed persistence and out-of-pool fallback.

  **Must NOT do**:
  - Do not use `getattr` / `hasattr`.
  - Do not emit any events from this method.

  **Recommended Agent Profile**:
  - **Category**: `quick` — focused additive method.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES after T1
  - **Parallel Group**: Wave 0
  - **Blocks**: T9, T10
  - **Blocked By**: T1

  **References**:
  - `src/agentpool/agents/context.py:AgentContext` — method home.
  - `src/agentpool/sessions/manager.py:create_child_session` — delegated canonical behavior.
  - RFC lines 406-446 — convenience method design.

  **Acceptance Criteria**:
  - [ ] Test verifies child `SessionData.parent_id`, `project_id`, `cwd`, `agent_name`, and `agent_type` when pool exists.
  - [ ] Test verifies fallback returns a non-empty generated ID when pool is absent.

  **QA Scenarios**:
  ```text
  Scenario: pool-backed child session persists
    Tool: Bash
    Preconditions: method implemented and test added
    Steps:
      1. Run `uv run pytest tests/agents/ tests/sessions/ -k "create_child_session" -v`.
    Expected Result: pytest exits 0 and child data inherits parent fields.
    Failure Indicators: child not saved, parent_id missing, project_id/cwd lost.
    Evidence: .sisyphus/evidence/task-5-agent-context-child-session.txt

  Scenario: no-pool fallback does not crash
    Tool: Bash
    Preconditions: fallback test added
    Steps:
      1. Run targeted test for `AgentContext.create_child_session()` with `node.agent_pool is None`.
    Expected Result: returns generated session id without persistence attempt.
    Failure Indicators: AttributeError, None return, or event emission side effect.
    Evidence: .sisyphus/evidence/task-5-no-pool-fallback.txt
  ```

  **Commit**: YES
  - Message: `feat(agents): add child session context helper`

- [x] 6. Audit session ID format dependency before provider switch

  **What to do**:
  - Search for assumptions that session IDs are `identifier.ascending("session")` style.
  - Document findings in tests or comments if no dependencies exist.
  - Add a regression test that treats session IDs as opaque strings.

  **Must NOT do**:
  - Do not add compatibility shims for old sequential IDs unless a real dependency is found.

  **Recommended Agent Profile**:
  - **Category**: `quick` — read-only audit plus small test.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 0
  - **Blocks**: T15
  - **Blocked By**: None

  **References**:
  - `src/agentpool/utils/identifier.py` or current identifier usage — old format source.
  - `src/agentpool/sessions/manager.py:create_child_session` — new format source.
  - `tests/servers/opencode_server/` and `tests/servers/acp_server/` — session lookup behavior.

  **Acceptance Criteria**:
  - [ ] No production code parses session ID counters.
  - [ ] Test ensures OpenCode/ACP lookups use full opaque ID strings.

  **QA Scenarios**:
  ```text
  Scenario: session IDs treated as opaque
    Tool: Bash
    Preconditions: audit complete
    Steps:
      1. Run `uv run pytest tests/sessions/ tests/servers/opencode_server/ -k "session_id" -v`.
    Expected Result: tests pass without assuming sequential format.
    Failure Indicators: regex/parser expecting `session_\d+` or failed UUID-like IDs.
    Evidence: .sisyphus/evidence/task-6-session-id-opaque.txt

  Scenario: old generator references isolated to allowed code
    Tool: Bash
    Preconditions: provider switch not yet done
    Steps:
      1. Run a repository search for `identifier.ascending("session")`.
      2. Save results showing only legacy provider sites pending T15.
    Expected Result: no non-provider consumers depend on sequential IDs.
    Failure Indicators: UI/parser/analytics code parses sequential IDs.
    Evidence: .sisyphus/evidence/task-6-ascending-audit.txt
  ```

  **Commit**: YES
  - Message: `test(sessions): assert session ids are opaque`

- [x] 7. Make OpenCode `ensure_session()` store-first and non-overwriting

  **What to do**:
  - Add `_session_from_session_data()` mapping in `src/agentpool_server/opencode_server/state.py`.
  - In `ensure_session()`, check in-memory first, then load `SessionData` from store before fallback creation.
  - Store-first path must create UI `Session`, register runtime state, mark idle, broadcast created/updated events, and **not** call `store.save()`.
  - Add overwrite-prevention tests TG-2/TG-5/TG-11/TG-17/TG-19/TG-32.

  **Must NOT do**:
  - Do not call `bind_agent_to_session()` on store-first child path.
  - Do not rewrite fallback behavior for sessions absent from memory and store.

  **Recommended Agent Profile**:
  - **Category**: `deep` — safety gate with server side effects and data-loss risk.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES with T8, but Wave 1 must complete before Wave 2
  - **Parallel Group**: Wave 1
  - **Blocks**: T9, T10, T11, T12, T13, T14
  - **Blocked By**: None

  **References**:
  - `src/agentpool_server/opencode_server/state.py:ensure_session` — current overwrite path.
  - `src/agentpool_server/opencode_server/models.py:Session` — UI session model.
  - `tests/servers/opencode_server/test_ensure_session.py`, `test_subagent_sessions.py`, `test_spawn_session_start.py` — server test patterns.
  - RFC lines 932-998 and TG-2/TG-5/TG-11/TG-17/TG-19/TG-32.

  **Acceptance Criteria**:
  - [ ] `ensure_session()` loads already-persisted `SessionData` and preserves `agent_type`/`pool_id`.
  - [ ] Store-miss fallback still creates and persists a new session.
  - [ ] Concurrent calls for same ID produce one in-memory Session.

  **QA Scenarios**:
  ```text
  Scenario: store-first prevents overwrite
    Tool: Bash
    Preconditions: store-first path implemented
    Steps:
      1. Run `uv run pytest tests/servers/opencode_server/ -k "ensure_session and (store or overwrite or concurrent)" -v`.
    Expected Result: pytest exits 0; persisted `agent_type` and `pool_id` remain unchanged after `ensure_session()`.
    Failure Indicators: store data overwritten, duplicate sessions, missing broadcasts.
    Evidence: .sisyphus/evidence/task-7-ensure-session-store-first.txt

  Scenario: store-miss fallback still works
    Tool: Bash
    Preconditions: store-first path implemented
    Steps:
      1. Run `uv run pytest tests/servers/opencode_server/ -k "store_miss or fallback" -v`.
    Expected Result: unknown session ID creates a Session and persists fallback data.
    Failure Indicators: None return, missing save, or broken existing session creation.
    Evidence: .sisyphus/evidence/task-7-ensure-session-fallback.txt
  ```

  **Commit**: YES
  - Message: `fix(opencode): preserve persisted child session data`

- [x] 8. Deprecate or safely connect `AgentRunContext.session_id`

  **What to do**:
  - Implement RFC-recommended deprecation descriptor only if it passes `dataclasses.asdict()` and mypy.
  - If descriptor is fragile, use the safe fallback: pass `session_id=self.session_id` when constructing `AgentRunContext` and document deprecation separately.
  - Add tests for warning/asdict behavior or fallback connection.

  **Must NOT do**:
  - Do not remove `session_id` outright; that is a public API break.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — dataclass descriptor/type-checking subtlety.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES with T7
  - **Parallel Group**: Wave 1
  - **Blocks**: T16
  - **Blocked By**: None

  **References**:
  - `src/agentpool/agents/context.py:AgentRunContext.session_id` — dead field.
  - `src/agentpool/agents/base_agent.py:BaseAgent.run_stream` — possible fallback connection point.
  - RFC lines 748-827 and TG-29.

  **Acceptance Criteria**:
  - [ ] `dataclasses.asdict(AgentRunContext(...))` works.
  - [ ] `uv run --no-group docs mypy src/agentpool/agents/context.py src/agentpool/agents/base_agent.py` passes.

  **QA Scenarios**:
  ```text
  Scenario: deprecation path is serialization-safe
    Tool: Bash
    Preconditions: descriptor or fallback implemented
    Steps:
      1. Run `uv run pytest tests/agents/ -k "session_id and asdict" -v`.
    Expected Result: pytest exits 0; warning behavior matches implementation choice.
    Failure Indicators: descriptor object stored as value, asdict crash, or missing field.
    Evidence: .sisyphus/evidence/task-8-session-id-asdict.txt

  Scenario: type checking survives session_id change
    Tool: Bash
    Preconditions: implementation complete
    Steps:
      1. Run `uv run --no-group docs mypy src/agentpool/agents/context.py src/agentpool/agents/base_agent.py`.
    Expected Result: mypy exits 0.
    Failure Indicators: assignment/type-ignore errors beyond intentional documented ignore.
    Evidence: .sisyphus/evidence/task-8-session-id-mypy.txt
  ```

  **Commit**: YES
  - Message: `refactor(agents): deprecate run context session id`

- [x] 9. Adapt SubagentTools to create/persist child sessions once

  **What to do**:
  - Use `ctx.create_child_session()` in `SubagentTools.task()`.
  - Emit exactly one `SpawnSessionStart` per delegation from `task()`.
  - Remove `SpawnSessionStart` emission from `_stream_task()` and ensure `_stream_task()` receives already-created session IDs.
  - Replace `getattr(ctx, "current_depth", 0)` with `ctx.run_ctx.depth` pattern and enforce max depth.
  - Pass `session_id`, `parent_session_id`, and `depth` into child `run_stream()`.

  **Must NOT do**:
  - Do not duplicate spawn event emission.
  - Do not use `identifier.ascending("session")` for provider-owned child IDs after adaptation.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — streaming/event behavior is delicate.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES after Wave 1
  - **Parallel Group**: Wave 2
  - **Blocks**: T14, T15, T16
  - **Blocked By**: T1, T2, T5, T7

  **References**:
  - `src/agentpool_toolsets/builtin/subagent_tools.py:task,_stream_task` — current dual emission and depth pattern.
  - `src/agentpool/agents/events/events.py:SpawnSessionStart,SubAgentEvent` — event fields.
  - `tests/toolsets/test_subagent_async.py` and `tests/servers/opencode_server/test_spawn_session_start.py` — event behavior tests.
  - RFC lines 448-505 and TG-4/TG-8/TG-9/TG-14.

  **Acceptance Criteria**:
  - [ ] Exactly one `SpawnSessionStart` per delegated child session.
  - [ ] Child `SessionData` exists with correct `parent_id`.
  - [ ] `RunStartedEvent.session_id == SpawnSessionStart.child_session_id`.

  **QA Scenarios**:
  ```text
  Scenario: single spawn event for subagent delegation
    Tool: Bash
    Preconditions: SubagentTools adapted
    Steps:
      1. Run `uv run pytest tests/toolsets/test_subagent_async.py tests/servers/opencode_server/test_spawn_session_start.py -k "spawn or subagent" -v`.
    Expected Result: pytest exits 0 and no duplicate `child_session_id` spawn events exist.
    Failure Indicators: duplicate `SpawnSessionStart`, missing child ID, or depth always 1 in nested test.
    Evidence: .sisyphus/evidence/task-9-subagent-single-spawn.txt

  Scenario: max depth failure is graceful
    Tool: Bash
    Preconditions: depth guard wired into SubagentTools
    Steps:
      1. Run targeted test simulating `ctx.run_ctx.depth == MAX_DELEGATION_DEPTH`.
    Expected Result: `DelegationDepthError` is raised before child session creation.
    Failure Indicators: recursion proceeds or child session is persisted at overflow.
    Evidence: .sisyphus/evidence/task-9-subagent-depth-overflow.txt
  ```

  **Commit**: YES
  - Message: `fix(subagents): persist child sessions once`

- [x] 10. Adapt WorkersTools child sessions and depth propagation

  **What to do**:
  - Use `ctx.create_child_session()` in `_create_agent_tool()` and `_create_node_tool()`.
  - Replace all hardcoded `depth=1` with computed `child_depth` from `ctx.run_ctx.depth`.
  - Enforce `MAX_DELEGATION_DEPTH`.
  - Preserve existing worker event behavior and message history options.

  **Must NOT do**:
  - Do not change worker config/YAML schema.
  - Do not change pass-message-history behavior.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — two worker paths plus tests.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES after Wave 1
  - **Parallel Group**: Wave 2
  - **Blocks**: T14, T15, T16
  - **Blocked By**: T1, T2, T5, T7

  **References**:
  - `src/agentpool_toolsets/builtin/workers.py:_create_agent_tool,_create_node_tool` — current child ID/depth logic.
  - `tests/tools/test_workers.py` — worker behavior tests.
  - RFC lines 506-527 and TG-15.

  **Acceptance Criteria**:
  - [ ] Worker child sessions persist with correct parent.
  - [ ] Worker spawn depth equals parent depth + 1.
  - [ ] Existing worker tests still pass.

  **QA Scenarios**:
  ```text
  Scenario: worker child session persisted
    Tool: Bash
    Preconditions: WorkersTools adapted
    Steps:
      1. Run `uv run pytest tests/tools/test_workers.py -v`.
    Expected Result: pytest exits 0; worker events include non-null child and parent session IDs.
    Failure Indicators: missing SessionData, depth hardcoded to 1, or worker YAML regression.
    Evidence: .sisyphus/evidence/task-10-workers-session-persisted.txt

  Scenario: nested worker depth increments
    Tool: Bash
    Preconditions: depth propagation test added
    Steps:
      1. Run `uv run pytest tests/tools/test_workers.py -k "depth" -v`.
    Expected Result: spawn depth is parent depth + 1, e.g. 3 when parent depth is 2.
    Failure Indicators: observed depth remains 1.
    Evidence: .sisyphus/evidence/task-10-workers-depth.txt
  ```

  **Commit**: YES
  - Message: `fix(workers): propagate persisted child sessions`

- [x] 11. Adapt streamed Team parallel execution

  **What to do**:
  - Add explicit `depth: int = 0` handling to `Team.run_stream()`.
  - Pop `session_id` and `depth` from `kwargs` before forwarding to prevent duplicate keyword errors.
  - Create child sessions per member via `self.agent_pool.sessions.create_child_session()` when available; generate ID fallback when outside a pool.
  - Emit `SpawnSessionStart` per member before member events.
  - Preserve nested `SubAgentEvent.child_session_id` / `parent_session_id`; only raw events use the current member child session.

  **Must NOT do**:
  - Do not change `Team.run()` or `BaseTeam.execute()`.
  - Do not create an intermediate Team session; hierarchy remains flat.

  **Recommended Agent Profile**:
  - **Category**: `deep` — concurrent stream wrapping and hierarchy semantics.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES after Wave 1
  - **Parallel Group**: Wave 2
  - **Blocks**: T14, T15, T16
  - **Blocked By**: T1, T2, T3, T7

  **References**:
  - `src/agentpool/delegation/team.py:Team.run_stream` — current parallel wrapping logic.
  - `src/agentpool/delegation/teamrun.py` — nested team interaction reference.
  - RFC lines 579-665, 1099-1120, TG-3/TG-7/TG-18/TG-22.
  - Metis directive: pop `depth` and `session_id` from kwargs.

  **Acceptance Criteria**:
  - [ ] Team member streams emit `SpawnSessionStart` before corresponding `SubAgentEvent` content.
  - [ ] Out-of-pool Team generates session IDs without persistence and does not crash.
  - [ ] Nested SubAgentEvent IDs are preserved.

  **QA Scenarios**:
  ```text
  Scenario: parallel team emits member spawn events
    Tool: Bash
    Preconditions: Team.run_stream adapted
    Steps:
      1. Run `uv run pytest tests/teams/ -k "stream or session or spawn" -v`.
    Expected Result: pytest exits 0; one spawn per member and child session IDs are non-null.
    Failure Indicators: no spawn events, duplicate keyword TypeError, or flat depth for nested team.
    Evidence: .sisyphus/evidence/task-11-team-spawn-events.txt

  Scenario: Team.run remains out of scope
    Tool: Bash
    Preconditions: Team.run_stream adapted
    Steps:
      1. Run existing non-streaming Team tests under `uv run pytest tests/teams/test_team.py -k "not stream" -v`.
    Expected Result: existing non-streaming behavior remains unchanged.
    Failure Indicators: return type changes or new event requirements on `Team.run()`.
    Evidence: .sisyphus/evidence/task-11-team-run-unchanged.txt
  ```

  **Commit**: YES
  - Message: `feat(teams): add streamed child session lifecycle`

- [x] 12. Adapt streamed TeamRun sequential execution

  **What to do**:
  - Add explicit `depth: int = 0` while preserving `require_all: bool = True`.
  - Pop `session_id` and `depth` from `kwargs` before forwarding.
  - Create child session and emit `SpawnSessionStart` for each member.
  - Preserve nested `SubAgentEvent` IDs/depth and raw event wrapping semantics.
  - Keep sequential message passing from `StreamCompleteEvent` unchanged.

  **Must NOT do**:
  - Do not change `TeamRun.run()` or `BaseTeam.execute()`.
  - Do not alter `require_all` behavior.

  **Recommended Agent Profile**:
  - **Category**: `deep` — sequential stream semantics and message handoff.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES after Wave 1
  - **Parallel Group**: Wave 2
  - **Blocks**: T14, T15, T16
  - **Blocked By**: T1, T2, T3, T7

  **References**:
  - `src/agentpool/delegation/teamrun.py:TeamRun.run_stream` — current sequential stream logic.
  - `tests/teams/test_team_run.py` and `tests/teams/` — TeamRun tests.
  - RFC lines 666-747, TG-16/TG-18/TG-22.

  **Acceptance Criteria**:
  - [ ] `TeamRun.run_stream(..., depth=1, require_all=False)` does not raise `TypeError`.
  - [ ] Each streamed member receives its own child session under the caller session.
  - [ ] Sequential handoff still uses prior `StreamCompleteEvent` content.

  **QA Scenarios**:
  ```text
  Scenario: TeamRun accepts depth and require_all together
    Tool: Bash
    Preconditions: TeamRun.run_stream adapted
    Steps:
      1. Run `uv run pytest tests/teams/ -k "teamrun and depth" -v`.
    Expected Result: pytest exits 0; `require_all=False` with `depth=1` works and spawn depth is 2.
    Failure Indicators: duplicate keyword TypeError or changed require_all behavior.
    Evidence: .sisyphus/evidence/task-12-teamrun-depth-require-all.txt

  Scenario: TeamRun.run remains out of scope
    Tool: Bash
    Preconditions: TeamRun.run_stream adapted
    Steps:
      1. Run existing non-streaming TeamRun tests.
    Expected Result: non-streaming behavior unchanged.
    Failure Indicators: return type or execution semantics changed.
    Evidence: .sisyphus/evidence/task-12-teamrun-run-unchanged.txt
  ```

  **Commit**: YES
  - Message: `feat(teamrun): add streamed child session lifecycle`

- [x] 13. Adapt `ACPSessionManager` child-session path

  **What to do**:
  - Add optional `parent_session_id` parameter to `ACPSessionManager.create_session()`.
  - When provided, call `self._pool.sessions.create_child_session(parent_session_id=..., agent_name=agent.name, agent_type="acp")`.
  - When absent, preserve top-level ACP session behavior with project_id computed from cwd.
  - Update ACP callers only where child-session context exists; leave top-level calls unchanged.

  **Must NOT do**:
  - Do not force top-level ACP sessions through `create_child_session()`.
  - Do not add `create_top_level_session()`.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — server session lifecycle with several call sites.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES after Wave 1
  - **Parallel Group**: Wave 2
  - **Blocks**: T14, T16
  - **Blocked By**: T7

  **References**:
  - `src/agentpool_server/acp_server/session_manager.py:create_session` — current direct `SessionData` save.
  - `src/agentpool_server/acp_server/acp_agent.py` — create_session call sites.
  - `tests/servers/acp_server/` — ACP server fixtures and tests.
  - RFC lines 829-931 and TG-10/TG-27.

  **Acceptance Criteria**:
  - [ ] Child ACP session inherits parent project_id/cwd.
  - [ ] Top-level ACP session still has `parent_id is None` and computed project_id.
  - [ ] Existing ACP tests pass.

  **QA Scenarios**:
  ```text
  Scenario: ACP child session inherits parent fields
    Tool: Bash
    Preconditions: ACP child path implemented
    Steps:
      1. Run `uv run pytest tests/servers/acp_server/ -k "session and child" -v`.
    Expected Result: child SessionData parent_id/project_id/cwd match parent.
    Failure Indicators: parent_id missing, cwd/project_id lost, or direct save path used for child.
    Evidence: .sisyphus/evidence/task-13-acp-child-session.txt

  Scenario: ACP top-level behavior preserved
    Tool: Bash
    Preconditions: ACP child path implemented
    Steps:
      1. Run `uv run pytest tests/servers/acp_server/ -k "new_session or top_level or rpc" -v`.
    Expected Result: top-level sessions have no parent and ACP RPC tests pass.
    Failure Indicators: top-level call requires parent or returns child-only ID.
    Evidence: .sisyphus/evidence/task-13-acp-top-level.txt
  ```

  **Commit**: YES
  - Message: `feat(acp): support inherited child sessions`

- [x] 14. Add cross-provider event/depth/session tests

  **What to do**:
  - Implement focused tests for RFC TG-1 through TG-32 that are in scope.
  - Cover event ordering: `SpawnSessionStart` before first child `SubAgentEvent` for same `child_session_id`.
  - Cover `RunStartedEvent.session_id == SpawnSessionStart.child_session_id`.
  - Cover SubagentTools → Team → Member flat hierarchy.
  - Cover mixed agent type Team session data.

  **Must NOT do**:
  - Do not test out-of-scope non-streaming Team.run/TeamRun.run as if adapted.
  - Do not require manual TUI verification.

  **Recommended Agent Profile**:
  - **Category**: `deep` — integration-style behavior across providers.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO; depends on provider adaptations
  - **Parallel Group**: Wave 2 tail
  - **Blocks**: T16
  - **Blocked By**: T9, T10, T11, T12, T13

  **References**:
  - RFC Test Specifications TG-1 through TG-32.
  - `tests/sessions/test_session_manager.py`, `tests/servers/opencode_server/test_subagent_sessions.py`, `tests/tools/test_workers.py`, `tests/teams/`, `tests/servers/acp_server/`.

  **Acceptance Criteria**:
  - [ ] Tests verify persistence, event order, depth, and ID consistency across all adapted providers.
  - [ ] Negative tests document out-of-scope non-streaming team behavior as unchanged.

  **QA Scenarios**:
  ```text
  Scenario: cross-provider RFC tests pass
    Tool: Bash
    Preconditions: all provider adaptations complete
    Steps:
      1. Run `uv run pytest tests/sessions/ tests/toolsets/test_subagent_async.py tests/tools/test_workers.py tests/teams/ tests/servers/acp_server/ tests/servers/opencode_server/ -k "session or spawn or depth or child" -v`.
    Expected Result: pytest exits 0; adapted providers show persisted child sessions and correct depth.
    Failure Indicators: missing session IDs, out-of-order spawn events, or overwritten SessionData.
    Evidence: .sisyphus/evidence/task-14-cross-provider-tests.txt

  Scenario: event ordering is deterministic
    Tool: Bash
    Preconditions: ordering test added
    Steps:
      1. Run targeted test that records events and compares index of `SpawnSessionStart` vs first child `SubAgentEvent` for each child session.
    Expected Result: each spawn index is less than child content index.
    Failure Indicators: SubAgentEvent arrives before SpawnSessionStart or no matching spawn.
    Evidence: .sisyphus/evidence/task-14-event-ordering.txt
  ```

  **Commit**: YES
  - Message: `test(delegation): cover child session lifecycle`

- [x] 15. Remove legacy provider session/depth patterns

  **What to do**:
  - Remove remaining provider usage of `identifier.ascending("session")` for child session IDs.
  - Remove remaining `getattr(ctx, "current_depth", 0)` delegation depth patterns.
  - Remove duplicate or obsolete helper parameters introduced only for compatibility during adaptation.
  - Verify no in-scope provider hardcodes `depth=1`.

  **Must NOT do**:
  - Do not remove identifier usage outside child session generation if unrelated.
  - Do not alter out-of-scope `pool.py` CLI depth behavior.

  **Recommended Agent Profile**:
  - **Category**: `quick` — cleanup after behavior is tested.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocks**: T16
  - **Blocked By**: T6, T9, T10, T11, T12, T13

  **References**:
  - `src/agentpool_toolsets/builtin/subagent_tools.py`, `src/agentpool_toolsets/builtin/workers.py`, `src/agentpool/delegation/team.py`, `src/agentpool/delegation/teamrun.py`.
  - RFC success criteria lines 141-149.

  **Acceptance Criteria**:
  - [ ] Search shows no in-scope provider uses `getattr(ctx, "current_depth", 0)`.
  - [ ] Search shows no adapted provider uses `identifier.ascending("session")` for child sessions.

  **QA Scenarios**:
  ```text
  Scenario: legacy depth pattern removed
    Tool: Bash
    Preconditions: cleanup complete
    Steps:
      1. Search changed provider files for `current_depth`.
      2. Run provider tests from T14.
    Expected Result: no provider references to `current_depth`; tests pass.
    Failure Indicators: lingering `getattr` anti-pattern or failed depth tests.
    Evidence: .sisyphus/evidence/task-15-current-depth-cleanup.txt

  Scenario: legacy child ID generation removed from providers
    Tool: Bash
    Preconditions: cleanup complete
    Steps:
      1. Search in-scope provider files for `identifier.ascending("session")`.
      2. Save search output.
    Expected Result: no adapted provider child-session generation uses identifier.ascending.
    Failure Indicators: legacy child IDs remain in SubagentTools/WorkersTools/Team/TeamRun.
    Evidence: .sisyphus/evidence/task-15-identifier-cleanup.txt
  ```

  **Commit**: YES
  - Message: `refactor(delegation): remove legacy session patterns`

- [x] 16. Run broad validation and fix regressions within RFC scope

  **What to do**:
  - Run targeted and broad validation commands.
  - Fix regressions only within RFC scope.
  - Capture evidence for tests, mypy, and lint.

  **Must NOT do**:
  - Do not expand scope into non-streaming Team.run/TeamRun.run or EventManager.
  - Do not mask failures by skipping tests.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — multi-suite validation and regression triage.
  - **Skills**: [`systematic-debugging`]
    - `systematic-debugging`: Use if any test/build failure appears before proposing fixes.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocks**: Final Verification
  - **Blocked By**: T4, T8, T14, T15

  **References**:
  - `AGENTS.md` development commands — `uv run pytest`, `uv run --no-group docs mypy src/`, `duty lint`.
  - All changed files and tests from prior tasks.

  **Acceptance Criteria**:
  - [ ] `uv run pytest` passes.
  - [ ] `uv run --no-group docs mypy src/` passes.
  - [ ] `duty lint` passes.

  **QA Scenarios**:
  ```text
  Scenario: targeted RFC validation passes
    Tool: Bash
    Preconditions: tasks T1-T15 complete
    Steps:
      1. Run `uv run pytest tests/sessions/ tests/servers/opencode_server/ tests/tools/test_workers.py tests/toolsets/test_subagent_async.py tests/teams/ tests/servers/acp_server/ tests/messaging/ tests/test_events.py -v`.
    Expected Result: pytest exits 0.
    Failure Indicators: any failing test, skipped revived hierarchy tests, or timeout.
    Evidence: .sisyphus/evidence/task-16-targeted-pytest.txt

  Scenario: project quality gates pass
    Tool: Bash
    Preconditions: targeted validation passes
    Steps:
      1. Run `uv run --no-group docs mypy src/`.
      2. Run `duty lint`.
    Expected Result: both commands exit 0.
    Failure Indicators: mypy errors, ruff errors, formatting failures.
    Evidence: .sisyphus/evidence/task-16-quality-gates.txt
  ```

  **Commit**: YES
  - Message: `test(delegation): validate RFC 0028 session adaptation`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit okay before completing.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read this plan and the implementation diff. Verify every Must Have is present and every Must NOT Have is absent. Confirm evidence files exist for all tasks.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run `uv run --no-group docs mypy src/`, `duty lint`, and relevant pytest commands. Review changed files for type shortcuts, `getattr`/`hasattr`, unused imports, broad exception swallowing, or AI-slop abstractions.
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | VERDICT`

- [x] F3. **Real QA Execution** — `unspecified-high`
  Execute every task QA scenario exactly, save command outputs to `.sisyphus/evidence/final-qa/`, and verify cross-task integration.
  Output: `Scenarios [N/N pass] | Evidence [N/N present] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  Compare actual diff against this plan. Reject if it changes out-of-scope non-streaming Team/TeamRun, EventManager, pool.py CLI depth, schema migrations, or protocol surface.
  Output: `Tasks [N/N compliant] | Scope creep [NONE/issues] | VERDICT`

---

## Commit Strategy

- T1-T3: Foundation commits (`feat(agents)`, `feat(delegation)`).
- T4/T14/T16: Test commits (`test(sessions)`, `test(delegation)`).
- T7: Safety fix commit (`fix(opencode)`).
- T9-T13: Provider adaptation commits by provider.
- T15: Cleanup commit.

---

## Success Criteria

### Verification Commands
```bash
uv run pytest tests/sessions/ tests/servers/opencode_server/ tests/tools/test_workers.py tests/toolsets/test_subagent_async.py tests/teams/ tests/servers/acp_server/ tests/messaging/ tests/test_events.py -v
uv run pytest
uv run --no-group docs mypy src/
duty lint
```

### Final Checklist
- [x] Store-first `ensure_session()` prevents SessionData overwrite.
- [x] SubagentTools and WorkersTools persist child sessions via `create_child_session()`.
- [x] Team and TeamRun streamed execution emits `SpawnSessionStart` and preserves child/parent IDs.
- [x] ACP child session path inherits parent fields while top-level sessions remain unchanged.
- [x] Depth propagation uses `run_stream(depth=...)` / `ctx.run_ctx.depth` with max-depth guard.
- [x] Out-of-scope areas remain untouched.
- [x] All tests, mypy, and lint pass.
