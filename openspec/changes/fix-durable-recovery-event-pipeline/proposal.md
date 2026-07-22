## Why

After durable execution recovery (elicitation resume), user input messages don't display in the OpenCode TUI. The root cause is a design split between event persistence and event delivery: `UserMessageInsertedEvent` is published directly to `EventBus.publish()`, bypassing `ProtocolChannel.publish()` (the only path that journals events). During recovery, `journal.resume()` replays only journaled events — user messages are never replayed. Additionally, `_message_registered` is not reset after `StreamCompleteEvent`, causing the "finalize incomplete turn" warning on every subsequent turn. The recovery path in `_before_consumer_loop()` is dead code because `set_session_context_data()` is never called in production.

**Scope clarification**: P3 (activate recovery path) fixes **elicitation resume only** (same-process checkpoint), not cross-process crash restart. `_resume_contexts` is an in-memory dict that doesn't survive server restart. Cross-process crash recovery requires persisting `EventProcessorContext` to the session store — tracked as a follow-up.

Cross-framework analysis (8 frameworks: opencode v2, deer-flow, zed, pydantic-ai-harness, pi-agent, oh-my-openagent, claw-code, hermes) confirms AgentPool is the ONLY framework with dual event publish paths. All others have a unified event entry point.

Four interdependent problems:
- **P4**: `_message_registered` stays `True` after `StreamCompleteEvent` — every new turn triggers false "finalize incomplete turn" warning
- **P1**: Protocol-sourced user messages don't emit `PartUpdatedEvent` — TUI can't render message content. The TUI has no optimistic mechanism and calls `sync()` only once per session. New user messages after `sync()` rely entirely on SSE `message.part.updated` for parts, which P1 prevents. This is the root cause of user messages not displaying.
- **P3**: `set_session_context_data()` never called in production — recovery path in `_before_consumer_loop()` is dead code, `EventProcessorContext.serialize()/deserialize()` infrastructure unused
- **P2**: `UserMessageInsertedEvent` bypasses `ProtocolChannel` — user messages never journaled, lost on crash recovery replay

## What Changes

- **P4 fix**: Reset `_message_registered = False` in `StreamCompleteEvent` and `RunFailedEvent` handlers in `opencode_event_bridge.py`. One-line fix, zero risk.
- **P1 fix**: Unconditionally emit `PartUpdatedEvent` for protocol-sourced user messages in `event_processor.py`. The TUI has no optimistic mechanism and no `replayedParts` deduplication (only CLI has it). For new messages, SSE is the only parts source — no duplicate risk. For historical messages, P1.0 must verify part ID alignment.
- **P3 fix**: Activate the recovery path — call `set_session_context_data()` after `StreamCompleteEvent` (serialize `EventProcessorContext`), and in checkpoint resume flow (deserialize and restore). Wire up existing `EventProcessorContext.serialize()/deserialize()` infrastructure. **Note**: This fixes elicitation resume (same-process) only. Cross-process crash recovery requires persisting to session store (follow-up).
- **P2 fix**: Route `UserMessageInsertedEvent` through `ProtocolChannel.publish()` for steer/followup messages (not initial REST messages, which are sent before the run starts). Add deduplication guard to prevent double-publish during replay.

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `session-orchestration`: `UserMessageInsertedEvent` must flow through `ProtocolChannel` for journaling; recovery replay must include user messages.
- `unified-session-lifecycle`: `EventProcessorContext` must be serialized and restored across crash/restart boundaries; `set_session_context_data()` must be called in production code paths.
- `opencode-server`: Protocol-sourced user messages must emit `PartUpdatedEvent`; `_message_registered` must reset on turn completion.

## Impact

- **`src/agentpool_server/opencode_server/opencode_event_bridge.py`**: Reset `_message_registered` on `StreamCompleteEvent`/`RunFailedEvent` (P4); serialize `EventProcessorContext` after turn completion (P3).
- **`src/agentpool_server/opencode_server/event_processor.py`**: Unconditionally yield `PartUpdatedEvent` for protocol-sourced user messages (P1).
- **`src/agentpool/orchestrator/session_controller_runs.py`**: Route `UserMessageInsertedEvent` through `ProtocolChannel` for steer/followup messages (P2).
- **`src/agentpool/orchestrator/session_controller_close.py`** or **`session_controller_agent.py`**: Call `set_session_context_data()` in checkpoint resume flow (P3).
- **`src/agentpool/lifecycle/comm_channel.py`**: Add deduplication guard for replayed `UserMessageInsertedEvent` (P2).
- **`tests/`**: 22 new test cases across 3 layers (15 Unit, 3 Integration, 4 E2E).
- **Risk**: P4 is zero risk (1 line). P1 is low risk for new messages (SSE is only parts source), medium risk for historical replay (part ID mismatch — P1.0 must verify). P3 is medium risk (serialization edge cases, same-process only). P2 is medium risk (deduplication guard has crash-before-delivery edge case — see design.md).
