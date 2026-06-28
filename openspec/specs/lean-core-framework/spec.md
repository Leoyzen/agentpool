## ADDED Requirements

### Requirement: Framework supports only native and acp agent types
The system SHALL accept only `native` and `acp` as valid agent type discriminators in all configuration and runtime APIs. All other agent types SHALL be rejected at config validation time.

#### Scenario: YAML config with native agent passes validation
- **WHEN** a YAML config defines `type: native` for an agent
- **THEN** the config is accepted and the agent is instantiated as a `NativeAgent`

#### Scenario: YAML config with acp agent passes validation
- **WHEN** a YAML config defines `type: acp` for an agent
- **THEN** the config is accepted and the agent is instantiated as an `ACPAgent`

#### Scenario: YAML config with removed agent type fails validation
- **WHEN** a YAML config defines `type: claude`, `type: agui`, or `type: codex`
- **THEN** config validation raises a `ValidationError` with a clear message indicating the type is no longer supported

#### Scenario: AnyAgentConfig union only includes native and acp
- **WHEN** code references `AnyAgentConfig` type
- **THEN** the union only contains `NativeAgentConfig` and `ACPAgentConfig`

### BaseAgent.run_stream() Path B eliminates yield-in-task-group

`BaseAgent.run_stream()` Path B (standalone mode) SHALL NOT contain `yield` inside `async with anyio.create_task_group()`. Path B SHALL delegate to `RunHandle.start()` for event streaming.

**Scenarios:**

1. **WHEN** `run_stream()` is called in standalone mode (no SessionPool), **THEN** it SHALL create a minimal `RunHandle` with synthetic `SessionState` and iterate `run_handle.start()` for events, yielding them outside any cancel scope context.

2. **WHEN** consecutive `run_stream()` calls are made on the same agent, **THEN** no `RuntimeError: Attempted to exit cancel scope in a different task` SHALL occur.

3. **WHEN** the `run_stream()` generator is GC'd without explicit `aclose()`, **THEN** no cancel scope error SHALL occur — `RunHandle.start()` has no task group, so there is no cancel scope to leak. The `finally: await gen.aclose()` pattern in Path B SHALL ensure `GeneratorExit` propagates into `start()`, releasing `turn_lock` and running cleanup.

4. **WHEN** Path B creates a local EventBus (`_created_local_bus` flag), **THEN** the EventBus session SHALL be closed/unsubscribed in Path B's `finally` block after `gen.aclose()` — `RunHandle.start()`'s finally block does NOT handle EventBus cleanup.

### ACPAgent._stream_events() eliminates yield-in-task-group

`ACPAgent._stream_events()` SHALL NOT contain `yield` inside `async with anyio.create_task_group()`. (Note: the task group is in `_stream_events()`, not `_run_stream_once()` — `_run_stream_once()` is the base class method that calls `_stream_events()`.)

**Scenarios:**

5. **WHEN** `ACPAgent._stream_events()` is called, **THEN** it SHALL NOT create its own `anyio.create_task_group()` — event forwarding (`_forward_acp_events`, `_forward_secondary_events`) SHALL use `asyncio.create_task()` with manual `finally` cleanup that cancels and awaits both tasks.

6. **WHEN** consecutive ACP agent `run_stream()` calls are made, **THEN** no `RuntimeError` from cancel scope cross-task exit SHALL occur.

7. **WHEN** `_forward_secondary_events` is restructured, **THEN** `ToolResultMetadataEvent` handling and secondary event forwarding SHALL be preserved — the forwarding tasks run alongside the consumer loop without a task group.

## REMOVED Requirements

### Requirement: Framework supports claude agent type
**Reason**: Claude Code agent is an external CLI wrapper with high maintenance overhead and overlaps with native agent capabilities. The framework core should focus on pydantic-ai native agents and ACP protocol agents.
**Migration**: Users previously using `type: claude` should migrate to `type: native` with appropriate model configuration, or run Claude Code externally via ACP protocol.

### Requirement: Framework supports agui agent type
**Reason**: AG-UI agent is a remote HTTP-based agent with low adoption. The ACP protocol provides a more robust and standard way to integrate external agents.
**Migration**: Users previously using `type: agui` should migrate to `type: acp` for external agent integration.

### Requirement: Framework supports codex agent type
**Reason**: Codex agent is an OpenAI Codex CLI wrapper that duplicates native agent functionality. Native agents already support OpenAI models directly.
**Migration**: Users previously using `type: codex` should migrate to `type: native` with `model: openai:gpt-4o-codex` or equivalent.
