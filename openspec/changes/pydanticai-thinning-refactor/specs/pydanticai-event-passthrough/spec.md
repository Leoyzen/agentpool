## ADDED Requirements

### Requirement: AgentPool streaming events pass through PydanticAI event types directly
AgentPool SHALL use PydanticAI's `AgentStreamEvent` types directly in streaming. The custom `PartStartEvent(PyAIPartStartEvent)` and `PartDeltaEvent(PyAIPartDeltaEvent)` subclasses SHALL be removed. `session_id` SHALL be accessed via `RunContext.deps` (which carries `AgentContext` containing `session_id`), not from event payload fields.

#### Scenario: Streaming event is a raw PydanticAI event type
- **WHEN** a native agent emits a `PartStartEvent` during streaming
- **THEN** the event is a `pydantic_ai.AgentStreamEvent` instance, not an AgentPool subclass
- **AND** the event does not have a `session_id` field
- **AND** consumers access `session_id` via `run_ctx.session_id` or `AgentContext.session_id`

#### Scenario: RunExecutor forwards PydanticAI events without wrapping
- **WHEN** `RunExecutor.execute()` receives a `PartStartEvent` or `PartDeltaEvent` from PydanticAI's `agent_run.next(node)`
- **THEN** it forwards the event as-is to the event queue
- **AND** no subclassing or field-addition wrapping occurs

### Requirement: ToolCallStartEvent and ToolCallCompleteEvent are thin wrappers
`ToolCallStartEvent` and `ToolCallCompleteEvent` SHALL be constructed by `RunExecutor` as thin dataclass instances from PydanticAI's `FunctionToolCallEvent` and `FunctionToolResultEvent`, not by subclassing PydanticAI event types. They SHALL carry only AgentPool-specific fields not present on PydanticAI's events.

#### Scenario: ToolCallStartEvent constructed from FunctionToolCallEvent
- **WHEN** `RunExecutor` receives a `FunctionToolCallEvent` from PydanticAI
- **THEN** it constructs a `ToolCallStartEvent` with `tool_name`, `tool_call_id`, and `raw_input` extracted from the PydanticAI event
- **AND** the `ToolCallStartEvent` does not subclass any PydanticAI event type
- **AND** the event is published to the EventBus

#### Scenario: ToolCallCompleteEvent constructed from FunctionToolResultEvent
- **WHEN** `RunExecutor` receives a `FunctionToolResultEvent` from PydanticAI
- **THEN** it constructs a `ToolCallCompleteEvent` with `tool_name`, `tool_call_id`, `tool_result`, and metadata extracted from the PydanticAI event
- **AND** the `ToolCallCompleteEvent` does not subclass any PydanticAI event type

## REMOVED Requirements

### Requirement: PartStartEvent and PartDeltaEvent subclass PydanticAI events with session_id
**Reason**: Subclassing PydanticAI events just to add `session_id` creates coupling — every PydanticAI event change requires AgentPool to update subclasses. `session_id` is already available via `AgentContext` in `RunContext.deps`. Protocol consumers can access it from context, not event payload.
**Migration**: Replace `event.session_id` access with `run_ctx.session_id` or `AgentContext.session_id` lookups.
