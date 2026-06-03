## ADDED Requirements

### Requirement: Native pydantic-ai events propagate directly through AgentPool streams
AgentPool SHALL propagate pydantic-ai's native `AgentStreamEvent` types directly through the event stream where they overlap with AgentPool's event taxonomy, creating custom events only for AgentPool-specific concepts.

#### Scenario: Text part delta events
- **WHEN** a pydantic-ai `PartDeltaEvent` is emitted during streaming
- **THEN** it is propagated directly as a pydantic-ai event type, not wrapped in `RichAgentStreamEvent`

#### Scenario: Tool call events
- **WHEN** a pydantic-ai `FunctionToolCallEvent` or `ToolCallEvent` is emitted
- **THEN** it is propagated directly as a pydantic-ai event type

#### Scenario: Tool result events
- **WHEN** a pydantic-ai `FunctionToolResultEvent` is emitted
- **THEN** it is propagated directly as a pydantic-ai event type

#### Scenario: AgentPool-specific events remain custom
- **WHEN** an AgentPool-specific event occurs (subagent delegation, tool call progress, stream completion)
- **THEN** a custom AgentPool event type (`SubAgentEvent`, `ToolCallProgressEvent`, `StreamCompleteEvent`) is created and emitted

#### Scenario: Event stream type union
- **WHEN** a consumer subscribes to an agent's event stream
- **THEN** the stream yields a union of pydantic-ai native events and AgentPool-specific events

## MODIFIED Requirements

### Requirement: Agent streaming emits structured events
**Existing spec**: `native-agent` capability requires rich streaming events with cost tracking and formatting.

#### Scenario: RichAgentStreamEvent thinning
- **WHEN** `RichAgentStreamEvent` is used for a pydantic-ai native event
- **THEN** the native event is propagated directly and the wrapper is bypassed
