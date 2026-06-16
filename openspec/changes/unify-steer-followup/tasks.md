## 1. Repurpose RunHandle._native_run_ref → active_agent_run and wire in RunExecutor

- [ ] 1.1 Rename `RunHandle._native_run_ref: Any | None` → `active_agent_run: AgentRun | None` in `orchestrator/run.py` (field already exists at line 53)
- [ ] 1.2 In `RunExecutor.execute()`, set `run_handle.active_agent_run = agent_run` after entering `async with agentlet.iter(...) as agent_run:`
- [ ] 1.3 In `RunExecutor.execute()` finally block, clear `run_handle.active_agent_run = None` before `async with` exit
- [ ] 1.4 Verify `RunHandle.active_agent_run` is cleared on both normal completion and exception paths

## 2. Fix _run_agentlet_core() bare async for → next() loop

- [ ] 2.1 Replace `async for node in agent_run:` in `_run_agentlet_core()` (`native_agent/agent.py:920`) with explicit `while True: node = await agent_run.next(node)` loop, mirroring `RunExecutor.execute()` pattern
- [ ] 2.2 Ensure the `next()` loop handles `node.stream()` for `ModelRequestNode`/`CallToolsNode` correctly (stream content as before)
- [ ] 2.3 Ensure `break` on `End` or `StopIteration` — match existing error handling
- [ ] 2.4 Verify `after_node_run` hooks fire after the fix (test with `when_idle` enqueue)

## 3. Change agent type detection to use agent.AGENT_TYPE

- [ ] 3.1 In `_run_turn_unlocked()` (orchestrator/core.py), replace `session.metadata.get("agent_type", "unknown")` with `agent.AGENT_TYPE` for gating decisions
- [ ] 3.2 In `_create_run()` (orchestrator/core.py), replace `session.metadata.get("agent_type", "unknown")` with `agent.AGENT_TYPE`
- [ ] 3.3 In `SessionController.receive_request()`, keep `session.metadata.get("agent_type")` only as fallback (agent may not be resolved yet), but prefer resolved agent check when available

## 4. Add steer()/followup() to TurnRunner

- [ ] 4.1 Implement `TurnRunner.steer(session_id, message)` — lookup `RunHandle` via `self.sessions._runs.get(session.current_run_id)`, read `run_handle.active_agent_run`: if set (native) → `enqueue(message, priority='asap')`; if None → fallback to `receive_request(session_id, message, priority="steer")`
- [ ] 4.2 Implement `TurnRunner.followup(session_id, message)` — lookup `RunHandle` via `self.sessions._runs.get(session.current_run_id)`, read `run_handle.active_agent_run`: if set (native) → `enqueue(message, priority='when_idle')`; if None → fallback to `receive_request(session_id, message, priority="followup")`
- [ ] 4.3a For non-native agents with active run, `steer()` → `run_handle.run_ctx.injection_manager.inject()`, `followup()` → `run_handle.run_ctx.injection_manager.queue()`
- [ ] 4.3b For non-native agents with no active run (idle), `steer()` → store in `_post_turn_injections[session_id]` + `_trigger_auto_resume()`, `followup()` → store in `_post_turn_prompts[session_id]` + `_trigger_auto_resume()`

## 5. Add steer/followup priority aliases to SessionController.receive_request()

- [ ] 5.1 Accept `priority="steer"` and `priority="followup"` in `receive_request()`, mapping to `"asap"` and `"when_idle"` internally
- [ ] 5.2 For active native runs, route to `TurnRunner.steer()`/`TurnRunner.followup()` instead of `inject_prompt()`/`queue_prompt()`
- [ ] 5.3 Verify backward compatibility — `"asap"`/`"when_idle"` still work without deprecation warning

## 6. Remove manual follow-up loop from _run_turn_unlocked() for native agents

- [ ] 6.1 Gate `flush_pending_to_queue()` in `_run_turn_unlocked()` behind `agent.AGENT_TYPE != "native"` (unconsumed injections dropped for native)
- [ ] 6.2 Gate the `while has_queued(): pop_queued() + _run_stream_once()` loop behind `agent.AGENT_TYPE != "native"`
- [ ] 6.3 Keep `_process_queued_work()` post-turn drain working for `_post_turn_injections`/`_post_turn_prompts` for ALL agents — native agents' `steer()`/`followup()` on idle delegate to `receive_request()` instead of these dicts, so they won't be populated
- [ ] 6.4 Keep `_run_turn_unlocked()` calling `agent._run_stream_once()` for all agents — `_run_agentlet_core()` `next()` fix (Task 2) handles the `after_node_run` hook firing. Do NOT switch to `RunExecutor.execute()`

## 7. Verify existing gating in BaseAgent._run_stream_direct() for native agents

- [ ] 7.1 Verify `_run_stream_direct()` at `base_agent.py:1080` already gates native agents correctly — `AGENT_TYPE == "native"` skips the `while has_queued()` loop
- [ ] 7.2 Confirm non-native agents still enter the `while has_queued()` loop as before

## 8. Deprecate inject_prompt()/queue_prompt() for native agents

- [ ] 8.1 Add `DeprecationWarning` to `BaseAgent.inject_prompt()` when called for native agent, delegate to `steer()` internally
- [ ] 8.2 Add `DeprecationWarning` to `BaseAgent.queue_prompt()` when called for native agent, delegate to `followup()` internally
- [ ] 8.3 Remove dual-write pattern in `inject_prompt()`/`queue_prompt()` for native agents (stop writing to both `injection_manager` AND `session_pool`)
- [ ] 8.4 For non-native agents, keep existing behavior without deprecation

## 9. Update external callers

- [ ] 9.1 Update `BaseAgent.inject_prompt()` in `pool.py` to delegate to `TurnRunner.steer()` instead of dual-write
- [ ] 9.2 Update `BaseAgent.queue_prompt()` in `pool.py` to delegate to `TurnRunner.followup()` instead of dual-write
- [ ] 9.3 Update xeno-agent's `BackgroundTaskProvider` to call `session_pool.steer()` instead of `session_pool.inject_prompt()`
- [ ] 9.4 Verify protocol handlers (ACP, AG-UI, OpenCode) that call `inject_prompt()`/`queue_prompt()` route through `receive_request()` with steer/followup priority

## 10. Tests

- [ ] 10.1 Unit test: `RunHandle.active_agent_run` set by `RunExecutor` on run start, cleared on completion
- [ ] 10.2 Unit test: `TurnRunner.steer()` calls `agent_run.enqueue(priority='asap')` for native agents
- [ ] 10.3 Unit test: `TurnRunner.followup()` calls `agent_run.enqueue(priority='when_idle')` for native agents
- [ ] 10.4 Unit test: `TurnRunner.steer()` delegates to `receive_request()` when `active_agent_run` is None
- [ ] 10.5 Unit test: `TurnRunner.steer()` calls `injection_manager.inject()` for non-native agents
- [ ] 10.6 Unit test: `TurnRunner.followup()` calls `injection_manager.queue()` for non-native agents
- [ ] 10.7 Integration test: steer message injected before next LLM call via `PendingMessageDrainCapability.before_model_request()`
- [ ] 10.8 Integration test: followup message processed after agent would otherwise end (via `after_node_run` redirect)
- [ ] 10.9 Integration test: manual follow-up loop NOT executed for native agents (no redundant processing)
- [ ] 10.10 Integration test: `_run_agentlet_core()` `next()` loop fires `after_node_run` hooks (verify `when_idle` drain works on standalone path)
- [ ] 10.11 Integration test: agent type detected via `agent.AGENT_TYPE` (not metadata) — native agents correctly skip manual loop
- [ ] 10.12 Integration test: tool result augmentation via `injection_manager.consume()` still works on native agents