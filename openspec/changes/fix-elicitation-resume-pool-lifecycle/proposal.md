## Why

Elicitation crash recovery bypasses the SessionPool's turn management (`_resume_native_agent()` calls `agent.run_stream(_skip_pool=True)`), creating a standalone execution without RunHandle lifecycle, journal/snapshot persistence, or session coordination. In long interactive sessions, this means: (1) resumed runs cannot themselves crash-recover, (2) second elicitations during resume are not durable, (3) session state transitions are manual and inconsistent, (4) events may not reach protocol consumers reliably. The current design treats resume as a one-shot replay, but in production it must support full session continuation — the agent may encounter new elicitations, new tool calls, and further timeouts, each requiring the same durability guarantees as a normal turn.

## What Changes

- **`_resume_native_agent()` routes through the pool**: Instead of calling `agent.run_stream(_skip_pool=True)`, resume passes `cached_elicitation_responses`, `deferred_tool_results`, and `message_history` as optional parameters through `session_pool.run_stream()` → `_run_stream_run_turn()` → `_create_run_handle()`.
- **`_create_run_handle()` sets resume state on `AgentRunContext`**: When constructing `AgentRunContext` in `_create_run_handle()` (`session_pool.py`), set `cached_elicitation_responses` if provided. `get_agentlet()` already sets `elicitation_registry`, `checkpoint_manager`, and `elicitation_timeout` — it never overwrites `cached_elicitation_responses`.
- **`message_history` reaches `NativeTurn` via `RunHandle`**: `_create_run_handle()` stores `message_history` on the `RunHandle`; `RunHandle.start()` passes it to `NativeTurn.__init__()` instead of using the agent's current conversation.
- **`deferred_tool_results` via `**pydantic_ai_kwargs`**: Flows through `session_pool.run_stream()` → `_run_stream_run_turn()` → `RunHandle.start()` → `agent.create_turn()` → `NativeTurn.__init__()` → `agentlet.iter(deferred_tool_results=...)`. NOTE: `agent.create_turn()` must be modified to accept and forward `**pydantic_ai_kwargs` (currently does not).
- **All three parameters default to `None`**: Normal turns pass `None` for all three — runtime behavior is unchanged. Only `resume_session()` provides non-`None` values.
- **Event converter uses `refusal` for `RunErrorEvent`**: `TurnCompleteUpdate(stop_reason="refusal")` instead of `"end_turn"` so ACP clients distinguish timeout errors from normal completion.
- **Removal of `_skip_pool=True` workaround**: The standalone Path B execution in `_resume_native_agent()` is replaced by the pool-managed Path A.

## Capabilities

### New Capabilities

_None_

### Modified Capabilities

- `session-orchestration`: Resume path routes through SessionPool's turn management (RunHandle lifecycle, journal, snapshot, session coordination) instead of standalone execution. Resume state (`cached_elicitation_responses`, `deferred_tool_results`, `message_history`) is passed as optional parameters through the pool's method chain.
- `acp-elicitation-server`: Event converter maps `RunErrorEvent` to `stop_reason="refusal"` (was `"end_turn"`) so ACP clients distinguish timeout/error from normal completion and do not create new sessions.
- `unified-session-lifecycle`: Resumed turns participate in the full RunHandle lifecycle (TriggerSource, Journal, SnapshotStore, CommChannel), enabling crash recovery for the resume run itself. Resumed turns with durable journals SHALL start with a fresh journal.

## Impact

- **`src/agentpool/orchestrator/session_pool.py`**: `_resume_native_agent()` rewritten to call `session_pool.run_stream()` with resume parameters; `_create_run_handle()` accepts and sets `cached_elicitation_responses` + initializes `_message_history`; wires `_host_context` and `_agent_registry`; adds staleness check; `_run_stream_run_turn()` acquires `_request_lock`
- **`src/agentpool/orchestrator/run.py`**: `RunHandle` — `_message_history` initialized from checkpoint when provided
- **`src/agentpool/agents/native_agent/agent.py`**: `agent.create_turn()` gains `**pydantic_ai_kwargs` forwarding to `NativeTurn`
- **`src/agentpool_server/acp_server/event_converter.py`**: `RunErrorEvent` → `stop_reason="refusal"` (already implemented)
- **`src/agentpool/agents/base_agent.py`**: `**pydantic_ai_kwargs` forwarding (already done) retained
- **Tests**: E2e crash recovery test updated to verify full lifecycle; new tests for second elicitation during resume, concurrent run guard, `_host_context` wiring
