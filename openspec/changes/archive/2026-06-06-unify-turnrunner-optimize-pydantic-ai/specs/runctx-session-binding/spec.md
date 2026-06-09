## MODIFIED Requirements

### Requirement: Interrupt uses run_ctx.current_task
`BaseAgent.interrupt()` SHALL cancel `run_ctx.current_task` instead of `_current_stream_task` or `_iteration_task`. This works uniformly across all agent types because `current_task` is stored in `AgentRunContext` by both legacy and SessionPool paths.

#### Scenario: Interrupt during SessionPool turn
- **WHEN** `interrupt()` is called during an active SessionPool-managed turn
- **THEN** it cancels `run_ctx.current_task`
- **AND** the agent stream terminates with `run_ctx.cancelled = True`

#### Scenario: Interrupt works for all agent types
- **WHEN** `interrupt()` is called on any agent type (Native, ClaudeCode, ACP)
- **THEN** it correctly cancels the active turn without relying on agent-type-specific task references

### Requirement: get_active_run_context uses at most two fallbacks
`BaseAgent.get_active_run_context()` SHALL use at most two levels of fallback. When `SessionPool` is active, it SHALL query `SessionPool` exclusively. When in standalone mode, it SHALL use `_current_run_ctx_var` exclusively. `_active_run_ctx` and `_background_run_ctx` instance attributes SHALL NOT be used as fallbacks.

#### Scenario: SessionPool lookup
- **WHEN** `get_active_run_context()` is called on an agent managed by `AgentPool`
- **AND** the agent has an active run in `SessionPool`
- **THEN** it returns the `RunHandle.run_ctx` from `SessionPool`
- **AND** it does NOT fall back to `_current_run_ctx_var` or instance attributes

#### Scenario: Standalone ContextVar lookup
- **WHEN** `get_active_run_context()` is called on a standalone agent (no `AgentPool`)
- **THEN** it returns the value from `_current_run_ctx_var`
- **AND** it does NOT fall back to instance attributes

#### Scenario: No active run returns None
- **WHEN** `get_active_run_context()` is called and no run is active in SessionPool or ContextVar
- **THEN** it returns `None`

## ADDED Requirements

### Requirement: RunHandle exposes cancellation interface
`RunHandle` SHALL expose a `cancel()` method that sets `run_ctx.cancelled = True` and cancels `run_ctx.current_task`. `SessionController` SHALL delegate cancellation to the active `RunHandle` rather than calling `BaseAgent.interrupt()` directly. `BaseAgent.interrupt()` SHALL find the active run via `SessionController` and call `RunHandle.cancel()` when a `SessionPool` is active. When no `SessionPool` is active (standalone mode), `BaseAgent.interrupt()` SHALL fall back to canceling `run_ctx.current_task` directly.

#### Scenario: Cancel active native run via SessionController
- **WHEN** `SessionController.cancel_run(session_id)` is called on a session with an active native run
- **THEN** the system retrieves the active `RunHandle` for that session
- **AND** calls `run_handle.cancel()`
- **AND** the run terminates with `run_ctx.cancelled = True`

#### Scenario: Cancel with no active run
- **WHEN** `SessionController.cancel_run(session_id)` is called on an idle session
- **THEN** the system returns immediately without error

#### Scenario: Interrupt delegates to SessionController when pool is active
- **WHEN** `BaseAgent.interrupt()` is called on an agent that is part of an active `AgentPool`
- **THEN** it calls `SessionController.cancel_run()` for the associated session
- **AND** `SessionController` delegates to the active `RunHandle.cancel()`

#### Scenario: Interrupt in standalone mode falls back to direct cancellation
- **WHEN** `BaseAgent.interrupt()` is called on an agent not managed by any `AgentPool`
- **THEN** it falls back to canceling `run_ctx.current_task` directly
- **AND** the run terminates with `run_ctx.cancelled = True`
