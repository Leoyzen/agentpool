# Spec: Session Orchestration — Type Safety & Legacy Cleanup

## Requirements

### REQ-1: CommChannel Protocol Completeness

The `CommChannel` protocol MUST include:
- `deliver_feedback(feedback: Feedback) -> None` — for steer/followup message delivery
- `publishes_to_event_bus: bool` property — replaces `isinstance(channel, ProtocolChannel)` check
- `set_replaying(flag: bool) -> None` — replaces direct `_replaying` private field access

`DirectChannel` MUST implement `deliver_feedback` as a no-op, `publishes_to_event_bus` as `False`, and `set_replaying` as a no-op.
`ProtocolChannel` MUST implement `deliver_feedback` by enqueuing to the feedback queue, `publishes_to_event_bus` as `True`, and `set_replaying` by setting the internal flag.

**Rationale**: Currently `deliver_feedback` is duck-typed via `try/except AttributeError` with 4 `# type: ignore[attr-defined]`. The `publishes_to_event_bus` check uses fragile `isinstance`. The `_replaying` flag is accessed as a private field at `run.py:444,449`.

### REQ-2: RunHandle Dimension References

`RunHandle` MUST hold direct references to `_journal`, `_trigger_source`, and `_snapshot_store` rather than accessing them via `self._comm_channel._journal` (private member access). The `__post_init__` journal injection pattern at `run.py:265-270` MUST be replaced with constructor injection.

**Rationale**: 6 `# type: ignore[attr-defined]` in `run.py` result from accessing private members of `CommChannel`.

### REQ-3: RunStatus Enum Removal

The legacy `RunStatus` enum (defined at `orchestrator/run.py:130`, NOT `lifecycle/types.py`) MUST be removed. All code MUST use `RunState` (IDLE, RUNNING, DONE, defined at `lifecycle/types.py:15`). Migration mapping:
- `RunStatus.idle` → `RunState.IDLE`
- `RunStatus.running` → `RunState.RUNNING`
- `RunStatus.completed` → `RunState.DONE` + `outcome=COMPLETED`
- `RunStatus.failed` → `RunState.DONE` + `outcome=FAILED`
- `RunStatus.checkpointed` → `RunState.DONE` + `outcome=CHECKPOINTED`
- `RunStatus.done` → `RunState.DONE` + `outcome=None`
- `RunStatus.pending` → `RunState.IDLE`

A new `RunOutcome` enum with values `COMPLETED`, `FAILED`, `CHECKPOINTED` MUST be added to `lifecycle/types.py` (next to `RunState`). `RunHandle` MUST have an `outcome: RunOutcome | None = None` field to preserve the terminal state distinction. The `None` type annotation means Python `None` (not an enum member) — `done` maps to `outcome=None`.

Both `_status: RunStatus` (line 202) and `status: RunStatus` (line 193) fields on `RunHandle` MUST be removed. All checks in `steer()` and `followup()` that reference `_status` at `run.py:841,846` MUST migrate to `_run_state`. External consumers across `src/` (search: `grep -rn 'RunStatus' src/` — covers `session_pool.py`, `orchestrator/__init__.py`, `orchestrator/core.py`, `session_controller.py`, `src/agentpool_server/opencode_server/routes/message_routes.py`, `src/agentpool_server/opencode_server/session_pool_integration.py`) MUST be updated.

### REQ-4: RunHandle.start() Decomposition

`RunHandle.start()` (397 SLOC) MUST be decomposed into 5 sub-methods, each under 100 SLOC:
- `_handle_recovery()` — crash recovery + dimension subscription
- `_idle_loop()` — idle wait, feedback drain, prompt collection
- `_execute_turn()` — turn execution, event streaming (contains `yield` statements)
- `_handle_turn_result()` — cancel handling, error handling
- `_drain_events()` — post-turn snapshot, child events, feedback drain

State shared between methods MUST be passed as parameters or stored on `self`. The async generator semantics MUST be preserved.

**Rationale**: A 397-SLOC method with `# noqa: PLR0915` is unmaintainable and violates the 250 LOC ceiling.

### REQ-5: Dead Code Removal

Unreachable code in `session_controller.py:484-491` (after `return` statement) MUST be removed.

> Note: Single-config hardcoding removal (`self.pool.manifest.agents`) was moved to the `m4-multi-config` change (task group 18, task 18.9) because it touches `session_controller` identity logic that M4's RunScope routing modifies.

## Verification

- `grep -rn '# type: ignore\[attr-defined\]' src/agentpool/orchestrator/run.py` returns 0
- `grep -rn 'RunStatus' src/` returns 0
- `grep -rn '_channel_publishes_to_event_bus' src/` returns 0
- `grep -rn '_replaying' src/agentpool/orchestrator/run.py` returns 0 (direct private access replaced by protocol method)
- `RunHandle.start()` and all sub-methods are each under 100 SLOC
