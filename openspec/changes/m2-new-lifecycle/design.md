## Context

AgentPool's execution layer is fragmented across 5 different entry points (standalone `agent.run()`, `SessionController.receive_request()`, `BackgroundTaskProvider`, `WatchCommand`, future channel gateway), each with its own lifecycle management, input handling, and output delivery. RFC-0041 (Run/Turn separation) and RFC-0042 (six pluggable dimensions) define a unified lifecycle architecture that replaces this fragmentation with a single RunLoop driven by six composable dimensions.

M1 (foundation restructure) is now complete: `HostContext` carries infrastructure handles, `AgentFactory` compiles manifests into agent registries, and `MessageNode.agent_pool` returns a HostContext-compatible shim. The `agent_pool` backdoor still exists across ~211 references across 21 files (not ~25 across 4 files as originally estimated in M1 design) — M1b (parallel with M2) migrates those call sites to direct HostContext usage.

The current `RunHandle` (in `orchestrator/session_controller.py`) manages session runs with idle/running/done states but is session-scoped only. Standalone execution (`BaseAgent._run_stream_once()`) has no state machine. `TurnRunner` preserves a manual queue system for non-native agents. These three paths must converge into a single RunLoop.

RFC-0042 defines six pluggable dimensions: RunLoop (core loop), TriggerSource (input), Journal (event persistence), SnapshotStore (state persistence), CommChannel (output + feedback), EventTransport (wire protocol). Each dimension has default in-memory implementations that preserve existing behavior and durable implementations that enable crash recovery.

## Goals / Non-Goals

**Goals:**
- Implement RunLoop as the restructured RunHandle — idle/running/done state machine with `start()`, `steer()`, `followup()`, `close()`
- Implement six pluggable dimensions with default implementations that preserve existing behavior
- `agent.run()` and `agent.run_stream()` internally route through RunLoop
- Unified steer (inject into active Turn) and followup (queue for next Turn) across standalone and session modes via CommChannel feedback loop
- Crash recovery (opt-in): `DurableJournal` + `DurableSnapshotStore` enable snapshot/resume at Turn boundaries
- New `lifecycle:` YAML config section for opting into durable execution
- `StateUpdate` event: protocol-agnostic state notification (Running/Idle/Done) published through CommChannel
- Remove `MessageNode.agent_pool` backdoor: add deprecation warnings, replace ~211 call sites across 21 files with HostContext

**Non-Goals:**
- Config split (HostConfig/AgentManifest) — deferred to M4
- Capability migration (ResourceProvider → pydantic-ai Capability) — M3
- Multi-tenant support (ConfigRegistry, HostRegistry, RunScope) — M5
- Polyglot protocol servers (MessageQueueTransport, EventEnvelope) — M6
- ProtocolBridge for ACP v2↔v1 version translation — future
- Channel wake-up mode (GatewayChannel, ChannelTrigger) — future (interfaces defined, implementations deferred)
- Turn execution internals — defined by RFC-0041, not modified here

## Decisions

### Decision 1: RunLoop IS the restructured RunHandle, not a new class

**Choice**: RunLoop is implemented by restructuring the existing `RunHandle` class, not by creating a parallel class. The idle/running/done state machine from RFC-0041 is extended with dimension injection points.

**Rationale**: RFC-0042 explicitly states "RunLoop IS RFC-0041's restructured RunHandle — not a new class." Creating a parallel class would require migrating all `RunHandle` consumers simultaneously. Restructuring allows incremental migration: `RunHandle` gains dimension injection while preserving its existing API surface.

**Alternative considered**: New `RunLoop` class with `RunHandle` as adapter — rejected because it doubles the maintenance surface and creates a permanent adapter layer. The restructured RunHandle IS the RunLoop.

### Decision 2: Default dimensions preserve existing behavior

**Choice**: When no dimensions are injected, RunLoop uses `ImmediateTrigger` + `MemoryJournal` + `MemorySnapshotStore` + `DirectChannel` + `InProcessTransport`. This组合 produces behavior identical to the current standalone `agent.run()` path.

**Rationale**: Existing code must work without modification. Default dimensions are zero-infrastructure (in-memory, in-process). Users opt into durability via `lifecycle:` YAML config. This ensures backward compatibility while enabling progressive enhancement.

**Alternative considered**: Force all execution through `ProtocolChannel` + `SQLJournal` — rejected because it adds SQL dependency to standalone mode, breaking the "just run an agent" use case.

### Decision 3: Crash recovery is opt-in via `lifecycle:` YAML section

**Choice**: Crash recovery requires explicit configuration:

```yaml
agents:
  crash_safe_agent:
    type: native
    model: "openai:gpt-4o"
    lifecycle:
      journal: durable        # DurableJournal
      snapshot: durable       # DurableSnapshotStore
      recover_strategy: mark_interrupted  # default: safest
```

Without `lifecycle:`, all dimensions default to in-memory.

**Rationale**: Crash recovery adds I/O overhead (journal writes, snapshot saves) that standalone execution doesn't need. Making it opt-in preserves the lightweight default. The `lifecycle:` section is a clear, discoverable configuration point. `recover_strategy: mark_interrupted` is the default because re-invoking the LLM after crash is non-deterministic and potentially dangerous.

**Alternative considered**: Always-on durability with lazy persistence — rejected because journal writes on every event add latency even when no crash occurs.

### Decision 4: CommChannel owns Journal, not RunLoop

**Choice**: The Journal reference is held by CommChannel, not RunLoop. CommChannel calls `journal.append()` for delta events and `journal.upsert()` for entity-state events. RunLoop only interacts with SnapshotStore (loop-layer) and calls `journal.resume()` during crash recovery.

**Rationale**: CommChannel knows event semantics (delta vs entity update → append vs upsert). RunLoop doesn't. Persisting events before delivery (in CommChannel.publish()) ensures crash safety: if the process dies after journal append but before delivery, the event is recoverable. This aligns with Akka/Pekko's design where journal and snapshot-store are independently owned by different layers.

**Alternative considered**: RunLoop owns both Journal and SnapshotStore — rejected because RunLoop would need event-type knowledge to decide append vs upsert, violating separation of concerns.

### Decision 5: agent_pool backdoor removal via deprecation warnings first

**Choice**: M2 adds `DeprecationWarning` to `MessageNode.agent_pool` property. All ~211 call sites across 21 files are migrated to use `HostContext` directly (received via constructor injection from M1). The property is NOT removed in M2 — removal is deferred to M3.

**Rationale**: Removing the property immediately would break all agents. Deprecation warnings provide visibility while migration proceeds. M1b (parallel with M2) handles the actual call-site migration. The shim returns HostContext, so new code patterns work even through the deprecated property.

**Alternative considered**: Remove property in M2 with a hard cutover — rejected because ~211 call sites across 21 files is too many to migrate atomically alongside the RunLoop implementation. The migration may need to be phased (core agents first, then protocol servers, then tools).

### Decision 6: StateUpdate event is protocol-agnostic, published through CommChannel

**Choice**: `StateUpdate` (Running/Idle/Done) is a new event type published through `CommChannel.publish()`, not directly through EventBus. This allows non-EventBus consumers (webhook callbacks, MQ consumers) to receive state notifications.

**Rationale**: EventBus is an in-process mechanism tied to SessionPool. Standalone execution has no EventBus. Publishing StateUpdate through CommChannel ensures all execution modes emit state transitions uniformly.

**Alternative considered**: StateUpdate through EventBus only — rejected because standalone mode would need an EventBus instance just for state notifications.

## Risks / Trade-offs

- **[Risk] RunLoop must be stable before M4 multi-config** — M4 (config split) assumes RunLoop is the single execution path. If RunLoop has bugs or missing features, M4 is blocked. Mitigated by comprehensive test coverage of all state transitions and dimension combinations. HIGH risk.

- **[Risk] Steer/followup unification may break edge cases** — The current dual system (PydanticAI's `PendingMessageDrainCapability` for native agents, manual queue for non-native) has subtle behaviors around timing and ordering. Unifying through CommChannel feedback may expose latent bugs. Mitigated by keeping the dual system as fallback during M2, with a feature flag for the unified path. MEDIUM risk.

- **[Risk] Crash recovery needs tool execution log for idempotency** — Without logging tool executions, crash recovery would re-execute bash commands, API calls, and file writes. The tool execution log (`Journal.log_tool_execution()`) must be wired into the tool execution path. If any tool bypasses the log, recovery is unsafe. Mitigated by making the log a Journal-level concern that wraps all tool calls. MEDIUM risk.

- **[Risk] RunLoop restructuring may regress session mode** — SessionController currently uses RunHandle for protocol sessions. Restructuring RunHandle into RunLoop changes the internal API. Protocol server handlers (ACP, OpenCode, AG-UI) must be updated to use the new API. Mitigated by keeping the RunHandle public API stable and only changing internals. MEDIUM risk.

- **[Trade-off] Six new abstractions vs. simplicity** — Six dimensions add cognitive load. Mitigated by: (1) default dimensions are invisible (no config needed), (2) each dimension is a small Protocol with 3-5 methods, (3) users only interact with dimensions when opting into durability or non-standard modes.

- **[Trade-off] CommChannel owns Journal vs. RunLoop owns Journal** — CommChannel ownership means RunLoop can't directly journal events. This is intentional (separation of concerns) but means RunLoop depends on CommChannel for event persistence. If CommChannel is misconfigured, events are lost. Mitigated by CommChannel constructor requiring a Journal parameter.

- **[Risk] agent_pool migration scope is larger than originally estimated** — M1 design estimated ~25 call sites across 4 files, but the actual codebase has ~211 references across 21 files. This significantly increases the migration effort and risk of regressions. Mitigated by phasing the migration (core agents first, then protocol servers, then tools) and by the HostContext shim ensuring backward compatibility during migration. HIGH risk.

- **[Trade-off] Deprecation warnings for agent_pool may be noisy** — ~211 call sites emitting warnings could flood logs. Mitigated by using `DeprecationWarning` (filterable) and documenting the migration path in the warning message.
