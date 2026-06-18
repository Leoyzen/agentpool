## ADDED Requirements

### Requirement: Session tracks current turn owner task

`SessionState` SHALL expose a `_turn_owner_task` field that tracks the `asyncio.Task` currently executing a turn under `turn_lock`.

- The field SHALL default to `None`
- `TurnRunner._run_turn_unlocked()` SHALL set `_turn_owner_task` to `asyncio.current_task()` at entry
- `TurnRunner._run_turn_unlocked()` SHALL reset `_turn_owner_task` to `None` in its `finally` block

#### Scenario: Turn owner set during execution

- **WHEN** `_run_turn_unlocked()` starts executing a turn
- **THEN** `session._turn_owner_task` SHALL equal `asyncio.current_task()`

#### Scenario: Turn owner cleared after execution

- **WHEN** `_run_turn_unlocked()` completes (normally or exceptionally)
- **THEN** `session._turn_owner_task` SHALL be `None`

#### Scenario: Turn owner cleared on cancellation

- **WHEN** `_run_turn_unlocked()` is cancelled
- **THEN** `session._turn_owner_task` SHALL be `None` (cleared in `finally`)

### Requirement: _in_turn_context guards against child task deadlock

A `_in_turn_context: ContextVar[bool]` (default `False`) SHALL be used as a secondary safety guard alongside `_turn_owner_task`.

- `_run_turn_unlocked()` SHALL set `_in_turn_context` to `True` at entry, reset to `False` in `finally`
- This ContextVar propagates to `asyncio.create_task()` child tasks via `contextvars.Context`
- The routing logic SHALL check `_in_turn_context` FIRST, before checking `_turn_owner_task`
- If `_in_turn_context` is `True` → execute directly (catches child tasks that inherited the context)
- `_in_turn_context` SHALL NOT be used for any purpose other than this child-task deadlock guard
- `_in_turn_context` SHALL be a module-level variable in `base_agent.py` (same as the removed `_bypass_session_pool`), not a method on BaseAgent

#### Scenario: Child task inherits in_turn_context

- **WHEN** a child task created via `asyncio.create_task()` inside `_run_turn_unlocked()` calls `agent.run_stream()`
- **THEN** `_in_turn_context.get()` returns `True` (inherited from parent)
- **AND** `run_stream()` executes directly, avoiding deadlock on `turn_lock`

#### Scenario: External task does not see in_turn_context

- **WHEN** a task NOT spawned from within `_run_turn_unlocked()` calls `agent.run_stream()`
- **THEN** `_in_turn_context.get()` returns `False`
- **AND** routing proceeds to the `_turn_owner_task` identity check

### Requirement: BaseAgent routes based on turn ownership

`BaseAgent.run_stream()` and `BaseAgent.run()` SHALL use turn ownership to decide whether to delegate to SessionPool or execute directly, replacing `_should_bypass_session_pool()`.

The routing logic SHALL be:

1. If `self.agent_pool` is None or `self.agent_pool.session_pool` is None → execute directly (standalone mode)
2. **Guard**: If `_in_turn_context.get()` is True → execute directly (child task safety guard)
3. Look up the session by `effective_session_id` from SessionPool
4. If session does not exist → delegate to SessionPool (new session)
5. If session exists and `session._turn_owner_task is asyncio.current_task()` → execute directly (already in turn, avoid deadlock)
6. Otherwise → delegate to SessionPool (normal path)

#### Scenario: No AgentPool executes directly

- **WHEN** agent has no `agent_pool` set
- **THEN** `run_stream()` SHALL execute directly without SessionPool delegation

#### Scenario: New session delegates to SessionPool

- **WHEN** agent has an `agent_pool` with `session_pool`, and the session does not exist yet
- **THEN** `run_stream()` SHALL delegate to `SessionPool.run_stream()` which creates the session

#### Scenario: Nested call from within turn_lock executes directly

- **WHEN** the current `asyncio.Task` is the `_turn_owner_task` of the session
- **THEN** `run_stream()` SHALL execute directly to avoid deadlock on `turn_lock`

#### Scenario: Cross-task call from outside turn_lock delegates to SessionPool

- **WHEN** the current `asyncio.Task` is NOT the `_turn_owner_task` of the session
- **AND** `_in_turn_context` is `False` (not a child task of a turn)
- **THEN** `run_stream()` SHALL delegate to `SessionPool.run_stream()` (which will acquire `turn_lock` or queue)

### Requirement: _bypass_session_pool ContextVar is removed

The `_bypass_session_pool` ContextVar and `_should_bypass_session_pool()` function SHALL be removed from `base_agent.py`.

All imports and references to these symbols SHALL be updated or removed.

#### Scenario: No remaining references

- **WHEN** searching the codebase for `_bypass_session_pool` or `_should_bypass_session_pool`
- **THEN** no references SHALL remain (except in git history)

### Requirement: _run_stream_direct is merged into run_stream

The `_run_stream_direct()` method SHALL be removed. Its logic SHALL be inlined into the direct-execution branch of `run_stream()`.

The three use cases of `_run_stream_direct()` are preserved:
1. Standalone agents (no AgentPool) — same path as before
2. AG-UI protocol — same path as before (no `agent_pool`)
3. Nested calls within turn_lock — now detected via turn ownership instead of ContextVar

#### Scenario: Standalone agent executes directly

- **WHEN** an agent without `agent_pool` calls `run_stream()`
- **THEN** it SHALL execute `_run_stream_once()` directly, including session logging, `AgentRunContext` creation, and `_current_run_ctx_var` setup

#### Scenario: In-turn nested call executes directly with full context

- **WHEN** a nested call within `turn_lock` calls `run_stream()`
- **THEN** it SHALL execute `_run_stream_once()` directly with the same context setup as the standalone path