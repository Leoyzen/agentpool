## Why

AgentPool's execution layer is fragmented: 5 different entry points (agent.run(), SessionController.receive_request(), BackgroundTaskProvider, WatchCommand, future channel gateway), each with its own lifecycle management, input handling, and output delivery. RFC-0041 (Run/Turn separation) and RFC-0042 (six pluggable dimensions) define a unified lifecycle architecture, but implementation requires the M1 foundation (HostContext + AgentFactory) to be in place. M1 is now complete — this milestone implements the RunLoop and its six pluggable dimensions with default implementations that preserve existing behavior while enabling crash recovery and unified steer/followup.

## What Changes

- **Implement RunLoop**: Core execution loop with idle/running/done state machine (RFC-0041). Replaces RunHandle. `agent.run()` and `agent.run_stream()` internally route through RunLoop.
- **Implement six pluggable dimensions** with default implementations:
  - `TriggerSource`: `ImmediateTrigger` (standalone), `ProtocolTrigger` (session)
  - `Journal`: `MemoryJournal` (default), `SQLJournal` (session persistence)
  - `SnapshotStore`: `MemorySnapshotStore` (default), `SQLSnapshotStore`
  - `CommChannel`: `DirectChannel` (standalone), `ProtocolChannel` (session)
  - `EventTransport`: `InProcessTransport` (default)
- **Unified steer/followup**: Steer (inject into active Turn) and followup (queue for next Turn) work identically across standalone and session modes via CommChannel feedback loop.
- **Crash recovery (opt-in)**: `DurableJournal` + `DurableSnapshotStore` enable snapshot/resume at Turn boundaries. Configured via `lifecycle:` YAML section.
- **Remove `MessageNode.agent_pool` backdoor** (Phase 1b): Replace all 25 call sites with HostContext. Add deprecation warnings to `agent_pool` property.
- **New YAML config section**: `lifecycle:` for opting into durable execution (defaults to in-memory).
- **StateUpdate event**: Protocol-agnostic state notification (Running/Idle/Done) published through CommChannel.

## Capabilities

### New Capabilities

- `run-loop`: Core execution loop driving idle→running→idle|done cycle. Owns SnapshotStore. Delegates Turn execution to agent's `create_turn()`.
- `trigger-source`: Pluggable abstraction for how prompts arrive at the RunLoop. Bridges external stimuli to internal message queue.
- `journal`: Event-layer persistence with append+upsert semantics. Owned by CommChannel. Supports crash recovery via `resume()`.
- `snapshot-store`: Loop-layer state persistence at Turn boundaries. Owned by RunLoop. Provides idempotency keys via `turn_id`.
- `comm-channel`: Abstracts event delivery and feedback reception. Owns Journal. Publishes events before delivery (crash safety).
- `event-transport`: Abstracts wire protocol between RunLoop and external consumers. Default: InProcessTransport. Future: gRPC, MQ.

### Modified Capabilities

- `agent-pool`: AgentPool.get_agent() now returns agents whose run()/run_stream() route through RunLoop internally. Public API unchanged.
