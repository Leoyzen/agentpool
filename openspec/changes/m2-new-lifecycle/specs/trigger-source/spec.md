## ADDED Requirements

### Requirement: TriggerSource is a Protocol with subscribe, poll, and close methods

TriggerSource SHALL be a `@runtime_checkable` Protocol defining how prompts arrive at the RunLoop. It SHALL have `subscribe(run_loop)`, `poll()`, and `close()` methods.

#### Scenario: Protocol conformance

- **WHEN** a class implements `subscribe`, `poll`, and `close` methods with the correct signatures
- **THEN** `isinstance(instance, TriggerSource)` SHALL return `True`

#### Scenario: Subscribe attaches to RunLoop

- **WHEN** `trigger_source.subscribe(run_loop)` is called
- **THEN** the TriggerSource SHALL attach to the RunLoop for prompt delivery
- **AND** this method SHALL be called exactly once during `RunLoop.start()`

### Requirement: ImmediateTrigger delivers a single prompt for standalone execution

`ImmediateTrigger` SHALL deliver a single prompt immediately and then return `None` from `poll()` on all subsequent calls. It is the default TriggerSource for standalone `agent.run()` execution.

#### Scenario: Single prompt delivery

- **WHEN** an `ImmediateTrigger` is constructed with a prompt string and `poll()` is called
- **THEN** a `Prompt` with the provided content SHALL be returned
- **AND** on the next `poll()` call, `None` SHALL be returned

#### Scenario: Subscribe is a no-op

- **WHEN** `subscribe(run_loop)` is called on an `ImmediateTrigger`
- **THEN** no action SHALL be taken (the prompt is already set in the constructor)

### Requirement: ProtocolTrigger bridges protocol handlers to RunLoop

`ProtocolTrigger` SHALL receive prompts from protocol handlers (ACP, OpenCode, AG-UI, OpenAI API) via a `deliver()` method and forward them to the RunLoop via `poll()`. It SHALL use an internal `asyncio.Queue` for asynchronous prompt delivery.

#### Scenario: Protocol handler delivers prompt

- **WHEN** a protocol handler calls `trigger.deliver(content, priority="normal")`
- **THEN** the content SHALL be enqueued as a `Prompt`
- **AND** the next `poll()` call SHALL return the `Prompt`

#### Scenario: Poll returns None when empty

- **WHEN** `poll()` is called and no prompts have been delivered
- **THEN** `None` SHALL be returned without blocking

### Requirement: ScheduledTrigger triggers RunLoop on a schedule

`ScheduledTrigger` SHALL trigger the RunLoop based on a schedule (cron expression or interval). It SHALL render a prompt from a Jinja2 template on each trigger. This implementation is defined but may be deferred beyond M2.

#### Scenario: Interval trigger fires

- **WHEN** a `ScheduledTrigger` is configured with an interval and the interval has elapsed
- **THEN** `poll()` SHALL return a `Prompt` with the rendered template content
- **AND** the next trigger time SHALL be computed

#### Scenario: No trigger due

- **WHEN** `poll()` is called and the next scheduled time has not elapsed
- **THEN** `None` SHALL be returned

### Requirement: ChannelTrigger triggers RunLoop on external channel messages

`ChannelTrigger` SHALL listen on an external channel (Telegram, Discord, Slack, webhook) for incoming messages and deliver them as prompts. It SHALL use an internal `asyncio.Queue` and a background listener task. This implementation is defined but may be deferred beyond M2.

#### Scenario: External message arrives

- **WHEN** an external message arrives on the channel and the listener is active
- **THEN** a `Prompt` with the message content and source metadata SHALL be enqueued
- **AND** `poll()` SHALL return the `Prompt`

#### Scenario: Close stops listener

- **WHEN** `close()` is called on a `ChannelTrigger`
- **THEN** the background listener task SHALL be cancelled
- **AND** no further prompts SHALL be enqueued

### Requirement: TriggerSource defaults to ImmediateTrigger

When no TriggerSource is provided to RunLoop, `ImmediateTrigger` SHALL be used as the default.

#### Scenario: Default trigger in standalone mode

- **WHEN** a RunLoop is constructed without a `trigger_source` parameter
- **THEN** an `ImmediateTrigger` SHALL be created internally
- **AND** the RunLoop SHALL use it for prompt delivery
