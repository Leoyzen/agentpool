## MODIFIED Requirements

### Requirement: AgentPool.get_agent() returns agents whose run()/run_stream() route through RunLoop

`AgentPool.get_agent()` SHALL return agents whose `run()` and `run_stream()` methods internally create and use a RunLoop with default dimensions (`ImmediateTrigger`, `MemoryJournal`, `MemorySnapshotStore`, `DirectChannel`, `InProcessTransport`). The public API of `get_agent()` SHALL remain unchanged — callers receive the same agent interface.

#### Scenario: Standalone run routes through RunLoop

- **WHEN** `agent = pool.get_agent("my_agent")` is called and then `await agent.run("prompt")` is executed
- **THEN** a RunLoop SHALL be created internally with default dimensions
- **AND** the RunLoop SHALL execute the prompt via `ImmediateTrigger`
- **AND** the result SHALL be identical to the pre-M2 behavior

#### Scenario: Streaming run routes through RunLoop

- **WHEN** `agent = pool.get_agent("my_agent")` is called and then `async for event in agent.run_stream("prompt"):` is executed
- **THEN** a RunLoop SHALL be created internally with default dimensions
- **AND** events SHALL flow through `DirectChannel` to the caller
- **AND** the event stream SHALL be identical to the pre-M2 behavior

#### Scenario: Public API unchanged

- **WHEN** existing code calls `pool.get_agent("name")` and uses the returned agent
- **THEN** no code changes SHALL be required
- **AND** the agent's public interface (`run`, `run_stream`, `process`, `add_connection`) SHALL remain the same

### Requirement: AgentPool constructs RunLoop with lifecycle config from YAML

When an agent's YAML config includes a `lifecycle:` section, `AgentPool.get_agent()` SHALL construct the RunLoop with the configured dimensions instead of defaults.

#### Scenario: Durable lifecycle config

- **WHEN** an agent config contains:
  ```yaml
  lifecycle:
    journal: durable
    snapshot: durable
    recover_strategy: mark_interrupted
  ```
- **THEN** `get_agent()` SHALL construct the agent's RunLoop with `DurableJournal` and `DurableSnapshotStore`
- **AND** the `recover_strategy` SHALL be passed to the RunLoop

#### Scenario: No lifecycle config uses defaults

- **WHEN** an agent config does not contain a `lifecycle:` section
- **THEN** `get_agent()` SHALL construct the RunLoop with default in-memory dimensions
- **AND** no crash recovery SHALL be available

### Requirement: MessageNode.agent_pool property emits deprecation warning

`MessageNode.agent_pool` property SHALL emit a `DeprecationWarning` when accessed. The warning message SHALL direct users to use `HostContext` directly. The property SHALL continue to return a HostContext-compatible object (the compatibility shim from M1).

#### Scenario: Deprecation warning on access

- **WHEN** agent code accesses `self.agent_pool.storage`
- **THEN** a `DeprecationWarning` SHALL be emitted
- **AND** the storage manager SHALL still be returned (compatibility shim works)

#### Scenario: Warning message guides migration

- **WHEN** the `DeprecationWarning` is emitted
- **THEN** the warning message SHALL include guidance to use `HostContext` directly
- **AND** the message SHALL reference the migration path documented in AGENTS.md

### Requirement: SessionController uses RunLoop instead of RunHandle

`SessionController` SHALL use RunLoop (the restructured RunHandle) for session execution. The `receive_request()` method SHALL create a RunLoop with `ProtocolTrigger` and `ProtocolChannel` dimensions. The public API of `SessionController` SHALL remain unchanged.

#### Scenario: Protocol session uses RunLoop

- **WHEN** `SessionController.receive_request(session_id, content)` is called
- **THEN** a RunLoop SHALL be created (or reused) with `ProtocolTrigger` and `ProtocolChannel`
- **AND** the RunLoop SHALL execute the prompt via the ProtocolTrigger
- **AND** events SHALL flow through ProtocolChannel to the EventBus

#### Scenario: Session steer/followup works through CommChannel

- **WHEN** `SessionController.steer(session_id, content)` is called during an active Turn
- **THEN** the content SHALL be delivered as feedback to the `ProtocolChannel`
- **AND** the RunLoop SHALL inject it into the active Turn via `steer()`
