# RFC-0027 ACP Subagent Zed Compatibility Plan

## TL;DR

> **Quick Summary**: Deliver Zed-compatible ACP subagent support in phases: validate transport feasibility, add `_meta` plumbing for Zed, propagate the new `zed` mode through config/types, then bridge child ACP sessions and message indexing.
>
> **Deliverables**:
> - Zed-aware `ToolCallStart`/`ToolCallProgress` with `_meta.subagent_session_info` + `tool_name`
> - `display_mode=zed` wiring across ACP server/config surfaces
> - Child-session routing + cleanup for Zed subagents
> - Message index tracking and regression tests/snapshots
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: Phase 0 spike → Phase 1 `_meta` plumbing → Phase 2 child-session routing → Phase 3 message indexing

---

## Context

### Original Request
User asked: “当前有 rfc 0027 的 worktree；请为 rfc 0027 生成 plan”.

### Interview Summary
**Key Discussions**:
- RFC-0027 is the Zed ACP subagent compatibility effort.
- Recommended direction is RFC option 2: phased delivery with `display_mode=zed`.
- Preserve legacy/inline/tool_box behavior exactly.
- Do not change ACP schema or widen scope to other editor features.

**Research Findings**:
- Zed’s subagent UI depends on `_meta.subagent_session_info` and `_meta.tool_name` on `ToolCallStart`/`ToolCallProgress`.
- The repo already has child-session primitives in core session management.
- Phase 0 must prove whether unknown-session `session/update` is viable through the existing ACP transport.
- Metis flagged a few implementation risks: destructuring needs widening, `subagent_tools.py` has a child-session-id / double-emit issue, and Phase 2 needs a fallback if direct child routing fails.

### Metis Review
**Identified Gaps** (addressed):
- Child-session identifiers are currently not captured in the relevant event match arms.
- `subagent_tools.py` needs a regression fix for duplicate `SpawnSessionStart` emission.
- Phase 2 must clarify runtime ACP session handling vs persisted core session handling.
- The plan needs an explicit fallback if Phase 0 says direct child routing is impossible.

---

## Work Objectives

### Core Objective
Make AgentPool’s ACP server render Zed subagent UI correctly without breaking existing ACP clients or current display modes.

### Concrete Deliverables
- Zed `_meta` payload support for subagent tool calls
- `zed` display mode support across server/config/type surfaces
- Child ACP session routing and cleanup
- Message index tracking for subagent turns
- Tests + snapshots for backward compatibility and Zed behavior

### Definition of Done
- [ ] `uv run pytest` passes for the touched ACP/server test slices.
- [ ] `uv run mypy src/` (or the repo’s equivalent strict type check) passes for the touched modules.
- [ ] `uv run ruff check src/` passes.
- [ ] Snapshot tests prove `_meta.subagent_session_info` serializes as JSON object data, not a string.

### Must Have
- Zed mode must be explicitly opt-in (`display_mode=zed`).
- Legacy/inline/tool_box behavior must remain unchanged.
- `_meta.tool_name` and `_meta.subagent_session_info` must be present only where RFC allows.

### Must NOT Have (Guardrails)
- No ACP schema changes.
- No automatic client detection.
- No Zed Parallel Agents / Proxy Chains work.
- No deeper subagent nesting support beyond the RFC scope.
- No accidental `_meta.subagent_session_info` leakage into non-Zed subagent tool calls.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - all verification must be agent-executed.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: YES, tests-after
- **Framework**: pytest
- **If TDD**: not required here; add regression tests alongside each implementation task

### QA Policy
Every task must include agent-executed QA scenarios and evidence paths in `.sisyphus/evidence/`.

---

## Execution Strategy

### Parallel Execution Waves

**Wave 1 (foundation + independent fixes, run in parallel)**
1. Phase 0 transport feasibility spike
2. Phase 1 `_meta` plumbing in event conversion
3. Type/config propagation for `zed`
4. `subagent_tools.py` regression fix

**Wave 2 (dependent core routing, run in parallel after Wave 1)**
5. Phase 2 child ACP session lifecycle + routing
6. Phase 3 message index tracking

**Wave 3 (stabilization)**
7. Final regression sweep + docs/config updates

**Final Verification Wave**
- F1 plan compliance / scope audit
- F2 lint/type/test quality gate
- F3 end-to-end QA replay
- F4 scope fidelity / diff audit

### Dependency Matrix
- 1: none
- 2: none (but informed by 1 and RFC)
- 3: none
- 4: none
- 5: depends on 1 and 2
- 6: depends on 5
- 7: depends on 2-6
- F1-F4: depend on 1-7

### Agent Dispatch Summary
- Phase 0 spike: `unspecified-high`
- `_meta` plumbing: `deep`
- config/type propagation: `quick`
- subagent tools fix: `quick`
- child session routing: `deep`
- message indexing: `deep`
- final regression sweep: `unspecified-high`
- final audits: `oracle` / `deep` / `unspecified-high`

---

## TODOs

- [ ] 1. Phase 0 spike: validate child `session/update` transport

  **What to do**:
  - Build a proof-of-concept that creates a child ACP session during prompt processing.
  - Verify whether Zed accepts `session/update` notifications for a child `session_id` that was not pre-announced.
  - Verify whether a server-initiated `session/new` is possible if Zed requires it.
  - Produce a short decision memo that chooses the Phase 2 routing path or the fallback design.

  **Must NOT do**:
  - Do not change production routing logic yet.
  - Do not widen the RFC scope into unrelated editor integrations.

  **Recommended Agent Profile**:
  > - **Category**: `unspecified-high`
    - Reason: feasibility spike with protocol uncertainty.
  > - **Skills**: `[]`
  > - **Skills Evaluated but Omitted**: `librarian` (RFC already provides enough direction; this is repo-local validation)

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 5
  - **Blocked By**: None

  **References**:
  - `docs/rfcs/draft/RFC-0027-acp-subagent-zed-compatibility.md:978-996` - Phase 0 goal, scope, and go/no-go criteria.
  - `src/agentpool_server/acp_server/session.py` - where child-session lifecycle will eventually hook in.

  **Why these references matter**:
  - The RFC defines the exact transport question that Phase 0 must answer.
  - Session lifecycle code is the likely integration point if the spike succeeds.

  **Acceptance Criteria**:
  - [ ] PoC runs and records whether the Zed-side transport path is viable.
  - [ ] Decision memo states Go / No-Go / Alternate path.
  - [ ] Evidence file saved: `.sisyphus/evidence/task-1-phase-0-spike.md`

  **QA Scenarios**:
  ```
  Scenario: child session update is accepted
    Tool: Bash
    Preconditions: Zed-connected ACP server running locally
    Steps:
      1. Create a child session with a distinct session_id.
      2. Emit one session/update to that child session over the existing transport.
      3. Confirm the client receives and renders it.
    Expected Result: child update is accepted without a protocol error.
    Evidence: .sisyphus/evidence/task-1-child-update-accepted.md

  Scenario: unknown child session is rejected cleanly
    Tool: Bash
    Preconditions: server running; use a fake child session_id
    Steps:
      1. Emit session/update for a non-existent child session_id.
      2. Capture the server/client response.
    Expected Result: failure is explicit and documented; no crash.
    Evidence: .sisyphus/evidence/task-1-unknown-child-update-rejected.md
  ```

- [ ] 2. Phase 1 `_meta` plumbing for Zed subagent tool calls

  **What to do**:
  - Add `SubagentSessionInfo` and helper builders for `field_meta` / `_meta`.
  - Update `event_converter.py` so `display_mode=zed` emits `ToolCallStart` / `ToolCallProgress` with `_meta`.
  - Keep legacy/inline/tool_box behavior unchanged.
  - Fix the `reset()` duplication / double-call issue and preserve `_subagent_tool_map` until session close.

  **Must NOT do**:
  - Do not add `_meta.subagent_session_info` to non-Zed subagent tool calls.
  - Do not alter the meaning of the existing non-Zed modes.

  **Recommended Agent Profile**:
  > - **Category**: `deep`
    - Reason: event conversion is the core feature path and needs careful state handling.
  > - **Skills**: `[]`
  > - **Skills Evaluated but Omitted**: `quick` (too risky for protocol/state changes)

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 5, Task 6
  - **Blocked By**: None

  **References**:
  - `src/agentpool_server/acp_server/event_converter.py` - main conversion logic.
  - `src/acp/schema/session_updates.py` - `ToolCallStart` / `ToolCallProgress` payload types.
  - `docs/rfcs/draft/RFC-0027-acp-subagent-zed-compatibility.md:998-1052` - Phase 1 scope and tests.

  **Why these references matter**:
  - The converter is where `_meta` is actually attached.
  - The ACP schema types define the serialization shape that Zed consumes.
  - The RFC already enumerates the exact regressions and tests expected here.

  **Acceptance Criteria**:
  - [ ] Zed-mode `SpawnSessionStart` produces `ToolCallStart` with `_meta`.
  - [ ] Zed-mode `StreamCompleteEvent` produces completion `ToolCallProgress` with matching `_meta`.
  - [ ] Non-Zed modes do not leak `subagent_session_info`.
  - [ ] Snapshot proves `_meta.subagent_session_info` is a JSON object.

  **QA Scenarios**:
  ```
  Scenario: zed mode emits meta-bearing tool calls
    Tool: Bash
    Preconditions: relevant unit test target exists
    Steps:
      1. Run the targeted pytest slice for event_converter meta behavior.
      2. Inspect emitted ToolCallStart and ToolCallProgress payloads.
      3. Verify _meta contains subagent_session_info and tool_name.
    Expected Result: zed path emits meta-bearing tool calls.
    Evidence: .sisyphus/evidence/task-2-zed-meta-path.md

  Scenario: non-zed modes stay clean
    Tool: Bash
    Preconditions: same test suite
    Steps:
      1. Run the regression tests for legacy/inline/tool_box.
      2. Assert field_meta is None where the RFC forbids leakage.
    Expected Result: no `_meta.subagent_session_info` leakage.
    Evidence: .sisyphus/evidence/task-2-non-zed-clean.md
  ```

- [ ] 3. Propagate `zed` through config, CLI, and type surfaces

  **What to do**:
  - Add `zed` to the display-mode literals in the ACP server/config surface.
  - Update coercion logic and CLI choices so `zed` is accepted explicitly.
  - Keep existing defaults unchanged; only the opt-in mode is new.
  - Run type checks after updating the string unions.

  **Must NOT do**:
  - Do not make `zed` the default.
  - Do not change the existing meaning of `legacy`, `inline`, or `tool_box`.

  **Recommended Agent Profile**:
  > - **Category**: `quick`
    - Reason: broad but mechanical type/config propagation.
  > - **Skills**: `[]`
  > - **Skills Evaluated but Omitted**: `deep` (overkill for a literal propagation task)

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 2, 5, 7
  - **Blocked By**: None

  **References**:
  - `docs/rfcs/draft/RFC-0027-acp-subagent-zed-compatibility.md:1027-1045` - exact type propagation checklist.
  - `src/agentpool_server/acp_server/server.py` - coercion path for `display_mode`.
  - `src/agentpool_server/acp_server/pool_server.py` - server-side configuration surface.
  - `src/agentpool_server/acp_server/session.py` - session-level display mode typing.
  - `src/agentpool_server/acp_server/session_manager.py` - manager-level display mode typing.
  - `src/agentpool_server/acp_server/acp_agent.py` - ACP agent wrapper typing.
  - `src/agentpool_server/acp_server/serve_acp.py` - CLI choice surface.

  **Why these references matter**:
  - The RFC names every surface that must accept `zed`.
  - The coercion and CLI code paths are where accidental defaults or invalid mode handling would leak in.

  **Acceptance Criteria**:
  - [ ] Every display-mode literal listed in the RFC accepts `zed`.
  - [ ] Coercion rejects invalid modes and still preserves current defaults.
  - [ ] Type checks pass after the enum/literal updates.

  **QA Scenarios**:
  ```
  Scenario: zed is accepted end-to-end
    Tool: Bash
    Preconditions: config and CLI surfaces updated
    Steps:
      1. Start the ACP server with display_mode=zed.
      2. Verify the server boots without coercion errors.
      3. Confirm the runtime mode remains zed.
    Expected Result: zed is accepted but not auto-selected.
    Evidence: .sisyphus/evidence/task-3-zed-accepted.md

  Scenario: invalid mode is rejected
    Tool: Bash
    Preconditions: same server entrypoint
    Steps:
      1. Start the server with an invalid display mode.
      2. Capture the error message or validation failure.
    Expected Result: invalid mode is rejected cleanly.
    Evidence: .sisyphus/evidence/task-3-invalid-mode-rejected.md
  ```

- [ ] 4. Fix `subagent_tools.py` child-session emission correctness

  **What to do**:
  - Remove the duplicate `SpawnSessionStart` emission on the sync path.
  - Ensure the `child_session_id` used in the spawned event matches the one tracked by the converter.
  - Keep the generated child-session IDs stable and deterministic enough for the routing map.
  - Add regression tests for both sync and streamed paths.

  **Must NOT do**:
  - Do not change unrelated subagent behavior.
  - Do not alter the public task-tool contract beyond the bugfix.

  **Recommended Agent Profile**:
  > - **Category**: `quick`
    - Reason: focused bugfix with a narrow blast radius.
  > - **Skills**: `[]`
  > - **Skills Evaluated but Omitted**: `deep` (not needed for a single bugfix)

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 2, 5
  - **Blocked By**: None

  **References**:
  - `src/agentpool_server/acp_server/subagent_tools.py` - duplicate emit source.
  - `docs/rfcs/draft/RFC-0027-acp-subagent-zed-compatibility.md:1167-1170` - the RFC decision record for the bugfix.

  **Why these references matter**:
  - The bug lives in the task emission path and must be fixed where it originates.
  - The RFC explicitly records that this regression is in scope for Phase 1.

  **Acceptance Criteria**:
  - [ ] Sync path emits exactly one `SpawnSessionStart`.
  - [ ] Child-session IDs match between task emission and routing lookup.
  - [ ] Regression tests cover the duplicate-emit and ID-mismatch cases.

  **QA Scenarios**:
  ```
  Scenario: sync task emits one spawn event
    Tool: Bash
    Preconditions: subagent tool regression test available
    Steps:
      1. Run the sync-path test case.
      2. Count `SpawnSessionStart` emissions.
    Expected Result: exactly one spawn event is emitted.
    Evidence: .sisyphus/evidence/task-4-single-spawn-emit.md

  Scenario: child session id remains consistent
    Tool: Bash
    Preconditions: same test case
    Steps:
      1. Capture the child_session_id from emission.
      2. Verify the converter lookup uses the same id.
    Expected Result: ids match and routing can proceed.
    Evidence: .sisyphus/evidence/task-4-child-session-id-match.md
  ```

- [ ] 5. Phase 2 child ACP session lifecycle and event routing

  **What to do**:
  - Extend the ACP converter/session bridge so `zed` mode can create a child ACP session.
  - Route `SubAgentEvent.inner_event` into the child session update path.
  - Ensure `StreamCompleteEvent` closes the child session and completes the parent tool call.
  - Use the Phase 0 result to choose the direct-routing path or the fallback content-embedding path.

  **Must NOT do**:
  - Do not change the event data model.
  - Do not touch non-Zed routing behavior.

  **Recommended Agent Profile**:
  > - **Category**: `deep`
    - Reason: this is the riskiest part of the RFC and requires transport/lifecycle reasoning.
  > - **Skills**: `[]`
  > - **Skills Evaluated but Omitted**: `quick` (too shallow for child-session lifecycle)

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 6, Task 7
  - **Blocked By**: Tasks 1 and 2

  **References**:
  - `docs/rfcs/draft/RFC-0027-acp-subagent-zed-compatibility.md:1054-1073` - Phase 2 scope, dependencies, and rollback strategy.
  - `src/agentpool_server/acp_server/session_manager.py` - child-session creation and update routing hook.
  - `src/agentpool_server/acp_server/session.py` - cleanup/close path.
  - `src/agentpool_server/acp_server/event_converter.py` - where `SpawnSessionStart` / `SubAgentEvent` routing branches live.

  **Why these references matter**:
  - Phase 2 is mostly a bridge between existing session primitives and the ACP converter.
  - The rollback strategy depends on keeping the old routing path intact.

  **Acceptance Criteria**:
  - [ ] `zed` mode creates a child ACP session when a subagent spawns.
  - [ ] Subagent inner events are routed to the child session.
  - [ ] Parent completion closes the child session.
  - [ ] Fallback path is documented if Phase 0 was No-Go.

  **QA Scenarios**:
  ```
  Scenario: child session receives routed inner events
    Tool: Bash
    Preconditions: phase 2 implementation in place
    Steps:
      1. Spawn one zed subagent.
      2. Emit a non-complete inner event.
      3. Confirm the child session receives the update.
    Expected Result: routed inner event appears in the child session.
    Evidence: .sisyphus/evidence/task-5-child-event-routing.md

  Scenario: completion closes child session
    Tool: Bash
    Preconditions: same run
    Steps:
      1. Emit StreamCompleteEvent.
      2. Verify child session transitions to closed.
      3. Verify parent tool call completes.
    Expected Result: child lifecycle is closed cleanly.
    Evidence: .sisyphus/evidence/task-5-child-session-close.md
  ```

- [ ] 6. Phase 3 message index tracking

  **What to do**:
  - Track subagent message counts so `message_start_index` / `message_end_index` are meaningful.
  - Update `_meta` on the parent tool call as the child session receives routed events.
  - Use 0-based end indexes to match the RFC and Zed behavior.
  - Keep the index story compatible with the “new child session starts at 0” assumption.

  **Must NOT do**:
  - Do not change the event payload contract.
  - Do not introduce extra nesting semantics beyond the RFC.

  **Recommended Agent Profile**:
  > - **Category**: `deep`
    - Reason: index math and routing state need careful verification.
  > - **Skills**: `[]`
  > - **Skills Evaluated but Omitted**: `quick` (too easy to get subtly wrong)

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 7
  - **Blocked By**: Task 5

  **References**:
  - `docs/rfcs/draft/RFC-0027-acp-subagent-zed-compatibility.md:1074-1093` - Phase 3 requirements and assumptions.
  - `src/agentpool_server/acp_server/event_converter.py` - where index metadata is attached.

  **Why these references matter**:
  - The RFC defines the exact 0-based index semantics.
  - The converter is the place where the metadata must be updated in sync with routing.

  **Acceptance Criteria**:
  - [ ] Start/end indexes are populated for child sessions.
  - [ ] End index updates as inner events are routed.
  - [ ] Tests prove the 0-based math and the empty-session case.

  **QA Scenarios**:
  ```
  Scenario: message indexes advance with child events
    Tool: Bash
    Preconditions: phase 3 implementation in place
    Steps:
      1. Spawn a child session.
      2. Route three inner events.
      3. Assert the final message_end_index equals 2.
    Expected Result: 0-based indexing is correct.
    Evidence: .sisyphus/evidence/task-6-message-index-advances.md

  Scenario: empty child session leaves end index unset
    Tool: Bash
    Preconditions: same code path
    Steps:
      1. Spawn a child session.
      2. Complete it without inner events.
      3. Verify message_end_index remains None.
    Expected Result: empty-session case is handled cleanly.
    Evidence: .sisyphus/evidence/task-6-empty-session-index.md
  ```

- [ ] 7. Final regression sweep, docs, and cleanup

  **What to do**:
  - Update any config/docs/help text that mentions display modes or subagent behavior.
  - Run the targeted ACP/server test suite, snapshot tests, type checks, and lint checks.
  - Verify backward compatibility across legacy/inline/tool_box.
  - Capture any final notes needed for the implementation handoff.

  **Must NOT do**:
  - Do not expand scope into unrelated docs rewrites.
  - Do not soften any RFC guardrails.

  **Recommended Agent Profile**:
  > - **Category**: `unspecified-high`
    - Reason: broad stabilization and cleanup across multiple surfaces.
  > - **Skills**: `[]`
  > - **Skills Evaluated but Omitted**: `quick` (too narrow for cross-cutting validation)

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential after Tasks 1-6
  - **Blocks**: Final verification wave
  - **Blocked By**: Tasks 2-6

  **References**:
  - `docs/rfcs/draft/RFC-0027-acp-subagent-zed-compatibility.md:1094-1119` - milestone summary and open questions.
  - `README.md` / ACP docs - if mode examples or server help text need alignment.

  **Why these references matter**:
  - The final pass should ensure the implementation matches the RFC and that docs stay aligned with the chosen behavior.

  **Acceptance Criteria**:
  - [ ] All targeted tests pass.
  - [ ] Type/lint checks pass.
  - [ ] Backward-compatibility checks pass for legacy/inline/tool_box.

  **QA Scenarios**:
  ```
  Scenario: full regression suite passes
    Tool: Bash
    Preconditions: all implementation tasks merged locally
    Steps:
      1. Run the targeted pytest slices.
      2. Run type and lint checks.
      3. Capture the final output summary.
    Expected Result: no regressions remain.
    Evidence: .sisyphus/evidence/task-7-full-regression.md

  Scenario: backward compatibility remains intact
    Tool: Bash
    Preconditions: same environment
    Steps:
      1. Exercise legacy, inline, and tool_box modes.
      2. Assert each still behaves as before.
    Expected Result: no mode regression or `_meta` leakage.
    Evidence: .sisyphus/evidence/task-7-backward-compat.md
  ```

---

## Final Verification Wave (MANDATORY)

- [ ] F1. Plan compliance audit — `oracle`
  - Read the plan end-to-end and verify each must-have/must-not-have is satisfied by the implemented diff.
  - Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. Code quality review — `unspecified-high`
  - Run `uv run pytest`, the repo type check, and `ruff check` on the touched modules.
  - Check for `as any`-style shortcuts, empty catches, debug prints, unused imports, and AI-slop over-abstraction.
  - Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. Real QA replay — `unspecified-high`
  - Re-run every QA scenario listed above and capture evidence under `.sisyphus/evidence/final-qa/`.
  - Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. Scope fidelity check — `deep`
  - Compare the final diff against the RFC scope to ensure 1:1 coverage and no contamination from unrelated work.
  - Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- Prefer two commits if the execution naturally splits: Phase 0-1, then Phase 2-3.
- If the work is small enough after implementation, a single commit is acceptable.

---

## Success Criteria

### Verification Commands
```bash
uv run pytest
uv run ruff check src/
uv run mypy src/
```

### Final Checklist
- [ ] `zed` mode is opt-in only.
- [ ] `_meta.subagent_session_info` is present where required and absent where forbidden.
- [ ] Child ACP sessions route and close correctly.
- [ ] Message indexes are correct.
- [ ] Legacy/inline/tool_box behavior is unchanged.
- [ ] Tests, lint, and type checks pass.
