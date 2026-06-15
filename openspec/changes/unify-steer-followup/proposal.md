## Why

AgentPool currently has two parallel message injection systems for native agents: `PromptInjectionManager` (inject/queue/consume/pop_queued) and `TurnRunner._post_turn_*` (inject_prompt/queue_prompt/trigger_auto_resume). Both exist because pydantic-ai's `PendingMessageDrainCapability` already handles the same concerns with `asap`/`when_idle` priority. This duplication adds complexity, makes the codebase harder to reason about, and creates subtle bugs (e.g., dual-write patterns where messages are queued in two places simultaneously).

Meanwhile, external callers (protocol handlers, `BackgroundTaskProvider`) lack a clean `steer()` / `followup()` API — they must understand the internal distinction between `inject_prompt` and `queue_prompt` and manually decide which to call. A unified API that maps directly to pydantic-ai's proven enqueue mechanism would reduce complexity and improve developer experience.

## What Changes

- **NEW**: `TurnRunner.steer()` — maps to `agent_run.enqueue(message, priority='asap')` for mid-turn injection into the next model request
- **NEW**: `TurnRunner.followup()` — maps to `agent_run.enqueue(message, priority='when_idle')` for post-work continuation
- **MODIFY**: `TurnRunner._run_turn_unlocked()` — remove the manual follow-up prompt loop (`while has_queued(): pop_queued() + _run_stream_once()`) for native agents; `PendingMessageDrainCapability.after_node_run()` handles this via graph-node redirection
- **MODIFY**: `BaseAgent.inject_prompt()` / `queue_prompt()` — remove dual-write pattern; for native agents, delegate to `SessionController.receive_request()` only
- **MOVE**: `PromptInjectionManager` (inject/queue/consume/pop_queued/flush_pending_to_queue) — keep only for non-native agents (ACP), where it remains the sole mechanism for tool result augmentation and follow-up
- **MODIFY**: `BaseAgent._run_stream_direct()` — the explicit `while` loop for queued prompts is removed for native agents; kept for non-native agents
- **KEEP**: `PromptInjectionManager.inject()`/`consume()` for tool result augmentation on native agents — this is **NOT** replaced by enqueue (different semantics: augments tool results, not conversation messages)
- **NEW**: Expose `steer`/`followup` at `SessionController` level so protocol handlers can call a single `receive_request(session_id, content, priority="steer"|"followup")` instead of understanding internal queue internals
- **BREAKING**: `TurnRunner.inject_prompt()` and `TurnRunner.queue_prompt()` are deprecated in favor of `steer()` and `followup()` for native agents

## Capabilities

### New Capabilities
- `steer-followup-api`: Unified `steer()` / `followup()` interface on `TurnRunner` and `SessionController`, backed by pydantic-ai's `PendingMessageDrainCapability` for native agents

### Modified Capabilities
- `pending-message-queue`: Update Phase 2 scenarios to use `steer`/`followup` naming; add scenarios for PromptInjectionManager being non-native-only
- `sessionpool-only-execution`: Add requirement that `PromptInjectionManager` follow-up queue is removed from native agent path

## Impact

- **`agentpool/agents/prompt_injection.py`**: `PromptInjectionManager` becomes non-native-agent-only for follow-up queue; `inject()`/`consume()` kept for tool augmentation on all agents
- **`agentpool/agents/base_agent.py`**: `inject_prompt()`/`queue_prompt()` simplified; `_run_stream_direct()` follow-up loop removed for native agents
- **`agentpool/agents/native_agent/agent.py`**: `_run_agentlet_core()` `async for` fix needed for standalone path (already fixed in `RunExecutor`)
- **`agentpool/agents/native_agent/hook_manager.py`**: `as_capability()` keeps `injection_manager.consume()` for tool augmentation
- **`agentpool/orchestrator/core.py`**: `TurnRunner` gets `steer()`/`followup()`; `_run_turn_unlocked()` follow-up loop removed for native agents; `_post_turn_*` kept only for non-native
- **`agentpool/orchestrator/run_executor.py`**: No change needed — already uses `next()` correctly
- **`agentpool/delegation/pool.py`**: `inject_prompt()`/`queue_prompt()` → `steer()`/`followup()` delegation
- **`xeno-agent/.../background_task_provider.py`**: Calls `steer()` instead of `inject_prompt()`
- **External callers** (ACP handlers, AG-UI, OpenCode): Updated to use `steer()`/`followup()` via `receive_request(priority=...)`