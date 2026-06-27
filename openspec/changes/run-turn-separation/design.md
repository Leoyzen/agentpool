## Context

AgentPool's orchestrator layer (~2500 lines across `RunHandle`, `RunExecutor`, `TurnRunner`, `SessionController`, `PromptInjectionManager`) conflates two distinct concepts: session-level persistence and reactive execution. The 1:1:1 binding (prompt = turn = RunHandle) forces compensating complexity: dual queues, auto-resume, re-iteration loops, and 4-branch steer/followup.

The `introduce-anyio-structured-concurrency` OpenSpec change (completed, 69/69 tasks) established the CancelScope hierarchy but did not address the Run/Turn conceptual separation. ACP v2's prompt lifecycle RFD (fire-and-forget `session/prompt`, `state_change` notifications, `session/inject` with `mode: "queue"|"steer"`) requires this separation as a prerequisite.

RFC-0041 (`docs/rfcs/draft/RFC-0041-loop-run-separation.md`, Oracle PASS revision 7) documents the full design with code sketches, line-level deletion tables, and 3-phase migration plan.

## Goals / Non-Goals

**Goals:**
- Separate Run (session-level persistent execution) from Turn (single reactive cycle)
- Restructure `RunHandle` class (not rename) to absorb Run semantics: idle/running/done, `async with`, message queue, unified steer/followup
- Simplify `SessionController` to pure session registry (remove run lifecycle methods)
- Delete `TurnRunner` class entirely (absorbed by RunHandle)
- Delete `RunExecutor` class entirely (replaced by `NativeTurn` + `EventMapper`)
- Achieve ~46% net code reduction (~1168 lines) in the orchestrator layer
- Maintain v1 compatibility via `BaseAgent.run_stream()` and feature flag

**Non-Goals:**
- Multi-server / distributed Run (deferred to follow-up RFC, extensibility hooks documented)
- ACP v2 protocol implementation (this RFC is v1-compatible, v2 alignment is a consequence)
- Graph-based team execution changes (orthogonal to Run/Turn separation)
- Subagent spawn-session architecture changes (handled by existing graph architecture)
- Message history serialization across idle periods (storage-layer concern)

## Decisions

### D1: RunHandle restructured (not renamed)

**Decision**: Keep `RunHandle` as the class name. Restructure internals to absorb Run semantics.

**Alternatives considered**:
- Rename to `Run` with `RunHandle` as deprecated alias — rejected: unnecessary import churn across `close_session()`, `cancel_run()`, `SessionPool._runs`, protocol servers
- New `Run` class with `RunHandle` as wrapper — rejected: indirection layer adds complexity

**Rationale**: Class name is an API surface. Restructuring internals (adding `idle_event`, `message_queue`, `start()`, `async with`) is additive — existing attribute access (`run_id`, `complete_event`, `status`) remains compatible.

### D2: Turn as separate abstract class

**Decision**: Introduce `Turn` ABC with `execute()` async generator, `message_history` property, `final_message` property. `NativeTurn` wraps pydantic-ai `iter()`/`next(node)` cycle (~80 lines). `ACPTurn` wraps ACP `session/prompt` cycle (~30 lines).

**Alternatives considered**:
- Keep single `RunExecutor.execute()` and add idle around it — rejected: doesn't solve agent-type branching in steer/followup
- Modify pydantic-ai's `AgentRun` to add idle state — rejected: upstream dependency, too invasive

**Rationale**: Turn is the natural seam between protocol-agnostic run management and agent-type-specific execution. Each Turn is a self-contained async generator with no back-references to RunHandle state — enables future serialization for multi-server.

### D3: SessionController simplified to session registry

**Decision**: Remove `_create_run()`, `_cleanup_run()`, `cancel_run_for_session()` from SessionController. Simplify `receive_request()` to ~15 lines (session check + delegate to RunHandle). Keep all 27 registry/factory/hierarchy/storage/cleanup methods.

**Alternatives considered**:
- Merge SessionController into RunHandle — rejected: SessionController owns cross-session state (registry, MCP counts, pending questions, TTL cleanup) that cannot live on a per-RunHandle object
- Rename to `SessionRegistry` — deferred: optional cosmetic change, no functional impact

**Rationale**: Clear boundary — SessionController = "which sessions exist and who owns them", RunHandle = "how does a session execute". The `receive_request()` method becomes a thin routing layer.

### D4: TurnRunner deleted entirely (Phase 3)

**Decision**: All 11 TurnRunner methods/fields absorbed by RunHandle. Phase 1-2: deprecated with `DeprecationWarning` thin delegates. Phase 3: class deleted.

**Rationale**: TurnRunner's entire purpose was managing the turn lifecycle — which is exactly what RunHandle now does. Keeping a thin wrapper indefinitely adds indirection without value. The deprecation period gives callers one release cycle to migrate.

### D5: No idle_timeout parameter

**Decision**: RunHandle waits indefinitely until woken by `close()`, `steer()`, or `followup()`. Timeout is caller's policy via `anyio.move_on_after(N)`.

**Alternatives considered**:
- `idle_timeout` parameter on RunHandle — rejected: mixes mechanism with policy, race conditions between timeout and steer

**Rationale**: Clean separation of concerns. SessionPool can implement session-level idle policy (TTL cleanup) without RunHandle needing to know about timeouts.

### D6: PromptInjectionManager partially retained

**Decision**: `inject()`/`consume()` retained for tool-result augmentation in `ACPTurn`. `queue()`/`pop_queued()` deprecated and deleted in Phase 3.

**Rationale**: Tool-result augmentation is a per-Turn concern that pydantic-ai handles natively for native agents but ACP agents still need. Follow-up queuing is fully replaced by `RunHandle._message_queue`.

### D7: 3-phase migration with feature flag

**Decision**: Phase 1 (native, feature flag), Phase 2 (ACP, feature flag), Phase 3 (cleanup + deletion). `AGENTPOOL_USE_RUN_TURN=true` gates Phase 1.

**Rationale**: Native and ACP paths are independent enough to migrate separately. Feature flag allows production testing without committing. Phase 3 deletion only after both phases stable for 1 release cycle.

## Risks / Trade-offs

- **`complete_event` semantic change** (fires per-RunHandle, not per-turn) → Callers check `RunStatus` instead. Small blast radius: only `close_session`, `cancel_run`, `_cleanup_expired_sessions` affected.
- **`turn_lock` held during idle** → Prevents concurrent turns (desired behavior). `close_session()` force-wakes via `RunHandle.close()` + 30s timeout fallback to `cancel()`.
- **Non-native steer behavioral change** → Steer messages queued for next Turn instead of mid-run injection. Tool-result augmentation preserved via `PromptInjectionManager.inject()`/`consume()`.
- **Phase 3 irreversibility** → `TurnRunner` and `RunExecutor` classes deleted. Git tags mark pre-Phase-3 state for revert.
- **Memory overhead of persistent RunHandle** → Holds agent + message_history during idle. Negligible vs destroy/recreate (agent recreation is expensive).

## Migration Plan

### Phase 1: Native Agent Run/Turn (v1 compatible)
- Implement `RunHandle` (restructured), `Turn`, `NativeTurn`, `EventMapper`, `BaseAgent.run()`/`run_stream()`
- Simplify `SessionController.receive_request()` to delegate to RunHandle
- Deprecate `TurnRunner` with `DeprecationWarning`
- Feature flag `AGENTPOOL_USE_RUN_TURN=true` (default: `false`)
- Rollback: disable flag → revert to `RunExecutor.execute()` path

### Phase 2: Non-Native Agent (ACP) Migration
- Implement `ACPTurn`, migrate ACP path to RunHandle
- Remove ACP-specific compensating complexity (dual queues, auto-resume)
- Deprecate `PromptInjectionManager.queue()`/`.pop_queued()`
- Feature flag `AGENTPOOL_USE_RUN_TURN_FOR_ACP=true` (default: `false`)

### Phase 3: Cleanup and Deprecation Removal
- Delete `TurnRunner` class entirely
- Delete `RunExecutor` class entirely
- Delete `PromptInjectionManager` queuing methods
- Remove feature flags
- Update all protocol server references
- Delete deprecated tests
- Dependencies: Phase 1 and Phase 2 stable for 1 release cycle

## Open Questions

All 7 open questions from RFC-0041 are resolved (see RFC "Open Questions" section).
