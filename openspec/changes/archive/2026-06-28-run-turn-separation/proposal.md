## Why

AgentPool's orchestrator conflates session-level persistence with reactive execution in a 1:1:1 binding (prompt = turn = RunHandle), requiring ~415 lines of compensating complexity (dual queues, auto-resume, re-iteration loops, 4-branch steer/followup) across ~2500 lines total. This makes the codebase hard to reason about, blocks ACP v2 alignment (where prompt ≠ turn), and prevents future multi-server distribution.

## What Changes

- **Restructure `RunHandle`** from per-turn lifecycle handle to session-level persistent execution context with idle/running/done states, `async with` lifecycle, message queue, and unified steer/followup
- **Introduce `Turn` abstract class** — single reactive cycle (prompt → model → tools → response), agent-type-specific (`NativeTurn`, `ACPTurn`)
- **Extract `EventMapper`** from `RunExecutor` L220-283 as a shared utility for pydantic-ai event → `RichAgentStreamEvent` mapping
- **Add `BaseAgent.run()` / `BaseAgent.run_stream()`** — returns `RunHandle` (async context manager + async iterator), unifying v1 (single Turn) and v2 (persistent with idle)
- **Simplify `SessionController`** to pure session registry — remove `_create_run()`, `_cleanup_run()`, `cancel_run_for_session()` (absorbed by RunHandle), simplify `receive_request()` from ~70 to ~15 lines
- **BREAKING: Delete `TurnRunner` class entirely** (Phase 3) — all 11 methods/fields absorbed by RunHandle (~850 lines removed)
- **BREAKING: Delete `RunExecutor` class entirely** (Phase 3) — replaced by `NativeTurn.execute()` + `EventMapper` (~440 lines removed)
- **BREAKING: `RunHandle.complete_event`** fires per-RunHandle lifecycle (not per-turn). Callers must check `RunStatus` instead.
- **Deprecate `PromptInjectionManager.queue()` / `.pop_queued()`** — replaced by `RunHandle._message_queue`. Tool-result augmentation (`inject()`/`consume()`) retained.
- **Deprecate `TurnRunner` methods** with `DeprecationWarning` in Phase 1-2, deleted in Phase 3
- Feature flag `AGENTPOOL_USE_RUN_TURN=true` gates Phase 1 rollout (default: `false`)

## Capabilities

### New Capabilities
- `run-handle-session-lifecycle`: RunHandle restructured as session-level persistent execution context with idle/running/done states, `async with` lifecycle, unified steer/followup, and message queue
- `turn-abstraction`: Turn abstract class for agent-type-specific single reactive cycles (NativeTurn, ACPTurn)

### Modified Capabilities
- `steer-followup-api`: Unified steer/followup on RunHandle — eliminates 4-branch native/non-native routing, replaces TurnRunner delegation
- `pending-message-queue`: Replaced by RunHandle._message_queue — auto-resume and dual queue system eliminated
- `sessionpool-only-execution`: SessionController.receive_request() simplified to delegate to RunHandle; _create_run/_cleanup_run/cancel_run_for_session removed

## Impact

- **Files modified**: `orchestrator/run.py` (RunHandle restructured), `orchestrator/core.py` (SessionController simplified, TurnRunner deprecated then deleted), `agents/base_agent.py` (new run()/run_stream() methods)
- **Files created**: `orchestrator/turn.py` (Turn abstract), `agents/native_agent/turn.py` (NativeTurn), `agents/acp_agent/turn.py` (ACPTurn), `orchestrator/event_mapper.py` (EventMapper)
- **Files deleted** (Phase 3): `orchestrator/run_executor.py` (entire file)
- **Protocol servers**: ACP, OpenCode, AG-UI, OpenAI API servers — replace TurnRunner references with RunHandle
- **Tests**: TurnRunner tests, RunExecutor tests, PromptInjectionManager queuing tests updated then deleted in Phase 3
- **RFC**: `docs/rfcs/draft/RFC-0041-loop-run-separation.md` (Oracle PASS, revision 7)
- **Net code reduction**: ~1168 lines (~46%)
