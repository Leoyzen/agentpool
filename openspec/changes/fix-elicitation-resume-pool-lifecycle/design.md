## Context

The elicitation crash recovery path (`_resume_native_agent()`) currently bypasses the SessionPool's turn management by calling `agent.run_stream(_skip_pool=True)`. This was originally a workaround because the pool's turn creation path creates a fresh `AgentRunContext` that loses `cached_elicitation_responses` and `deferred_tool_results` — the two pieces of state needed for crash recovery.

In production, this causes:
1. Resumed runs have no RunHandle lifecycle (no journal, no snapshot, no crash recovery for the resume itself)
2. Session state transitions are manual and inconsistent
3. ACP event consumers may not receive resumed agent output
4. `_host_context` and `_agent_registry` not wired (second elicitations crash, subagent delegation fails)

The recent debugging session (Bugs 1-14) fixed checkpoint storage, event delivery, and timeout semantics, but the architectural debt of bypassing the pool remains.

## Goals / Non-Goals

**Goals:**
- Route resume through the pool's normal turn management (RunHandle lifecycle, journal, snapshot, session coordination)
- Preserve `cached_elicitation_responses` and `deferred_tool_results` across the pool boundary
- Wire `_host_context` and `_agent_registry` in `_create_run_handle()` so second elicitations are durable
- Guard against concurrent RunHandle creation (race condition between `run_stream()` and `receive_request()`)
- Enable full session continuation after resume (second elicitations, further timeouts, crash recovery for the resume run itself)
- ACP clients distinguish timeout errors from normal completion (`stop_reason="refusal"`)

**Non-Goals:**
- Redesigning the elicitation mechanism itself (Path 1/2/3 in `handle_elicitation()`)
- Changing the checkpoint format or storage layer
- Adding new ACP protocol extensions
- Fixing the `input_provider` setup for new ACP sessions (Bug 12 — separate issue, mitigated by `refusal` stop reason)

## Decisions

### Decision 1: Direct parameter passing (no `ResumeContext`)

**Decision**: Pass `cached_elicitation_responses`, `deferred_tool_results`, and `message_history` as optional parameters through the pool's method chain: `session_pool.run_stream()` → `_run_stream_run_turn()` → `SessionPool._create_run_handle()` (`session_pool.py:937`). All three default to `None` and are only set by `resume_session()`.

**Rationale**: The data flow is explicit and traceable. Normal turns pass `None` for all three — runtime behavior is unchanged. This avoids adding hidden side-channel state to `SessionState` and avoids the need for defensive clearing of stale resume context.

**Alternative considered (rejected)**: `ResumeContext` dataclass on `SessionState` as a side-channel. Rejected because: (1) it makes `SessionState` a grab-bag of ephemeral fields, (2) the data flow is hidden and hard to trace, (3) it requires defensive clearing to prevent stale state, (4) the "hot path pollution" concern is overblown — 3 optional `None` parameters don't change runtime behavior.

### Decision 2: `_create_run_handle()` sets resume state on `AgentRunContext`

**Decision**: In `SessionPool._create_run_handle()` (`session_pool.py:937`), after creating the `AgentRunContext`, set `cached_elicitation_responses` if provided:

```python
run_ctx = AgentRunContext(
    session_id=session_id,
    event_bus=event_bus,
)
if cached_elicitation_responses is not None:
    run_ctx.cached_elicitation_responses = cached_elicitation_responses
```

**Rationale**: `SessionPool._create_run_handle()` is the single point where `AgentRunContext` is constructed for pool-managed turns (called by `_run_stream_run_turn()` at line 1198). Setting resume state here ensures it's available before `get_agentlet()` runs. `get_agentlet()` already sets `elicitation_registry`, `checkpoint_manager`, and `elicitation_timeout` on `run_ctx` — it never overwrites `cached_elicitation_responses`, so the value set by `_create_run_handle()` is preserved.

Note: `SessionController._start_run_handle()` (`session_controller.py:886`) is a different method that creates a background task — it is NOT used by `_run_stream_run_turn()`. The pool path uses `SessionPool._create_run_handle()` directly.

### Decision 3: `message_history` initializes existing `_message_history` field

**Decision**: `_create_run_handle()` initializes `RunHandle._message_history` (existing `list[ModelMessage]` field at `run.py:185`, defaults to `[]`) from the checkpoint's `message_history` when provided. `RunHandle._execute_turn()` already passes `self._message_history` to `agent.create_turn()` at line 607. No new field needed.

The checkpoint's `message_history` is already `list[ModelMessage]` — no `MessageHistory` wrapper is needed for the pool path. The `MessageHistory` wrapper in the current `_resume_native_agent()` exists only for the standalone `agent.run_stream()` path and is removed.

### Decision 4: `deferred_tool_results` via `**pydantic_ai_kwargs` (requires `create_turn` fix)

**Decision**: `deferred_tool_results` flows through to `NativeTurn.__init__()` via `**pydantic_ai_kwargs` → `agentlet.iter()`. The forwarding mechanism exists on `NativeTurn.__init__()` (line 77), but `agent.create_turn()` (line 1178) does NOT currently accept or forward `**kwargs` to `NativeTurn`. This must be added: `agent.create_turn()` gains `**pydantic_ai_kwargs` parameter, forwarded to `NativeTurn.__init__()`.

**Rationale**: The `**pydantic_ai_kwargs` forwarding is already implemented and tested. No new mechanism needed — just populate the kwarg from the `_run_stream_run_turn()` parameter.

### Decision 5: `_resume_native_agent()` uses pool path

**Decision**: Rewrite `_resume_native_agent()` to:
1. Build `cached_elicitation_responses` from `elicitation_payloads` (existing logic)
2. Extract `message_history` as `list[ModelMessage]` from checkpoint (NOT wrapped in `MessageHistory`)
3. Call `session_pool.run_stream()` with `cached_elicitation_responses`, `deferred_tool_results`, and `message_history` as kwargs
4. No cleanup needed — parameters are used and discarded, no persistent state

**Rationale**: This makes resume a normal pool-managed turn with full lifecycle support. No `ResumeContext` to clear, no `SessionState` to mutate. The pool creates a `RunHandle` with all lifecycle dimensions via `SessionPool._create_run_handle()`.

### Decision 6: Event converter `RunErrorEvent` → `refusal`

**Decision**: Change `event_converter.py:832` from `stop_reason="end_turn"` to `stop_reason="refusal"` for `RunErrorEvent`. Already implemented in the current session.

**Rationale**: `end_turn` signals successful completion to ACP clients. `refusal` signals the agent could not continue. This prevents clients from creating new sessions after timeout (Bug 12 root cause).

### Decision 7: Resumed turns with durable journals start fresh

**Decision**: When `lifecycle: journal: durable` is configured, the resumed turn's `RunHandle` SHALL start with a fresh `MemoryJournal` (the default in `_create_run_handle()`). The original checkpoint data is in storage, not in the journal — no journal replay is needed for resume.

**Rationale**: The original turn's journal entries are from a different `RunHandle`. Replaying them into the resumed turn's journal would create duplicates and confusion. The checkpoint in storage is the authoritative state for crash recovery. `_create_run_handle()` already creates `MemoryJournal()` by default (line 968), so this is the natural behavior — just needs to be specified as a requirement.

### Decision 8: Wire `_host_context` and `_agent_registry` in `_create_run_handle()`

**Decision**: `SessionPool._create_run_handle()` currently lacks `_host_context` and `_agent_registry` wiring that `SessionController._start_run_handle()` provides. Add:
- `run_handle._host_context = pool.get_context()` (enables `CheckpointManager` creation in `get_agentlet()`)
- `run_handle._agent_registry` built from pool's manifest agent names (enables `SubagentCapability`)

**Rationale**: Without `_host_context`, `get_agentlet()` at agent.py:882-888 creates `CheckpointManager` only when `self.host_context is not None` — otherwise `checkpoint_manager` is `None`, and second elicitations (Path 3) crash with `AttributeError`. Metis review identified this as a critical gap.

### Decision 9: `_run_stream_run_turn()` acquires `_request_lock`

**Decision**: `_run_stream_run_turn()` currently does NOT hold `session._request_lock`, creating a race window with concurrent `receive_request()`. Add `_request_lock` acquisition before checking `current_run_id` and calling `_create_run_handle()`.

**Rationale**: Without this, `resume_session()` → `run_stream()` → `_run_stream_run_turn()` and a concurrent `receive_request()` → `_start_run_handle()` could both see `current_run_id is None` and create overlapping RunHandles. Metis review identified this as a critical race condition introduced by routing resume through the pool.

### Decision 10: `_create_run_handle()` staleness check

**Decision**: Add a check: if `session.current_run_id` is already set and the existing run is not `DONE`, raise `SessionBusyError` instead of silently overwriting.

**Rationale**: Currently `_create_run_handle()` blindly overwrites `session.current_run_id`, orphaning any existing RunHandle. The staleness check prevents this.

## Risks / Trade-offs

### Risk: `run_stream()` / `_create_run_handle()` signature grows
Adding 3 optional parameters to `_run_stream_run_turn()` and `_create_run_handle()`. Mitigation: All default to `None`, normal turns unaffected. The parameters are explicit and self-documenting. `run_stream()` already accepts `**kwargs` which forward to `_run_stream_run_turn()`.

### Risk: Journal interaction with durable lifecycle
If `lifecycle: journal: durable` is configured, the resumed turn's journal entries might conflict with the original turn's entries. Mitigation: Decision 7 — resumed turns start with a fresh `MemoryJournal`. The original checkpoint is in storage, not in the journal.

### Risk: ACP consumer subscription
The ACP event consumer must be active when the resumed turn starts. With `stop_reason="refusal"` (Decision 6), the client sees the turn as ended. If the consumer tears down after `refusal`, resumed events won't be delivered. Mitigation: The ACP handler's `_handle_elicitation_deferred()` calls `start_event_consumer()` before `resume_session()` (Bug 10 fix, already implemented). This needs verification with the pool-managed turn path.

### Risk: Session state transition conflicts
`resume_session()` currently manages session status manually (`checkpointed` → `resuming` → `active`). With pool-managed turns, the `RunHandle` lifecycle also manages status. Mitigation: `resume_session()` sets status to `resuming` before starting the turn; `RunHandle` transitions to `running`; on completion, `resume_session()` sets to `active` (as it does now).

### Risk: Cancellation during resumed turn
If `RunHandle.cancel()` is called during a resumed turn, the `resume_session()` `finally` block and the `RunHandle` cancellation path might conflict. Mitigation: `resume_session()` should use the same `RunHandle` for cancellation — `cancel_run_for_session()` cancels the active `RunHandle`, which propagates `CancelledError` to the turn, which is caught by `resume_session()`'s `except` block. This needs explicit testing.

### Trade-off: Smaller change surface than `ResumeContext` approach
This design touches `_run_stream_run_turn()`, `_create_run_handle()`, `RunHandle`, `NativeTurn`, `_resume_native_agent()`, and `event_converter`. No changes to `SessionState`, no new dataclass. ~40-60 lines across 5-6 files.
