## ADDED Requirements

### Requirement: Turn abstract class defines single reactive cycle interface

The system SHALL introduce a `Turn` abstract base class that defines the interface for a single reactive cycle (prompt → model → tools → response). `Turn` SHALL be agent-type-specific — each agent type implements its own `Turn` subclass.

- `Turn.execute()` SHALL be an abstract async generator yielding `RichAgentStreamEvent`
- `Turn.execute()` SHALL yield only mid-stream events (PartDeltaEvent, ToolCallStartEvent, ToolCallCompleteEvent, etc.) — lifecycle events (RunStartedEvent, StreamCompleteEvent) are published by `RunHandle`, not by `execute()`
- `Turn.message_history` SHALL be an abstract property returning the updated `list[ModelMessage]` after execution
- `Turn.final_message` SHALL be an abstract property returning the final `ChatMessage` from this Turn
- `Turn.final_message` SHALL raise `RuntimeError` if accessed before `execute()` completes
- `Turn` SHALL have no back-references to `RunHandle` state — it receives `run_ctx` and `message_history` at construction

#### Scenario: NativeTurn executes single pydantic-ai iteration
- **WHEN** `NativeTurn.execute()` is called
- **THEN** it enters `async with agentlet.iter(message_history=...) as agent_run:`
- **AND** sets `run_ctx._run_handle.active_agent_run = agent_run`
- **AND** loops: `node = agent_run.next_node` → check End → stream events → `node = await agent_run.next(node)`
- **AND** uses `agent_run.next(node)` (not bare `async for`) to fire `after_node_run` capability hooks
- **AND** maps pydantic-ai events to `RichAgentStreamEvent` via `EventMapper`
- **AND** handles `RunAbortedError` (graceful cancel), `UndrainedPendingMessagesError` (warning), `CancelledError` (re-raise)

#### Scenario: ACPTurn executes single ACP session/prompt cycle
- **WHEN** `ACPTurn.execute()` is called
- **THEN** it sends a `session/prompt` to the ACP agent
- **AND** streams events from the ACP response
- **AND** maps ACP events to `RichAgentStreamEvent`
- **AND** uses `PromptInjectionManager.inject()`/`consume()` for tool-result augmentation within `execute()`

#### Scenario: Turn final_message accessed before execute
- **WHEN** `turn.final_message` is accessed before `execute()` has completed
- **THEN** a `RuntimeError` is raised with message "final_message accessed before execute() completed"

### Requirement: BaseAgent.create_turn() factory method

`BaseAgent` SHALL implement `create_turn(prompts, run_ctx, message_history)` that returns a `Turn` instance. Each agent type SHALL override this to return its specific Turn subclass.

- `NativeAgent.create_turn()` SHALL return a `NativeTurn`
- `ACPAgent.create_turn()` SHALL return an `ACPTurn`
- `create_turn()` SHALL NOT execute the Turn — it only constructs it

#### Scenario: NativeAgent creates NativeTurn
- **WHEN** `agent.create_turn(prompts, run_ctx, message_history)` is called on a NativeAgent
- **THEN** a `NativeTurn` instance is returned
- **AND** the Turn is not yet executed

### Requirement: BaseAgent.run() returns RunHandle

`BaseAgent.run(prompt, *, run_ctx, message_history, event_bus, session)` SHALL return a `RunHandle` instance. The RunHandle SHALL be usable as both an async context manager and an async iterator.

- `agent.run()` SHALL construct a `RunHandle` with the agent, run_ctx, event_bus, and session
- `agent.run_stream(prompt, ...)` SHALL be a v1-compatible async generator that wraps a single Turn. It SHALL detect `StreamCompleteEvent` and call `run.close()` to prevent deadlock

#### Scenario: v1 single Turn via run_stream
- **WHEN** `async for event in agent.run_stream("prompt", ...):` is called
- **THEN** a RunHandle is created via `agent.run()`
- **AND** `run.start("prompt")` yields events
- **AND** when `StreamCompleteEvent` is yielded, `run.close()` is called
- **AND** the async generator exits after the first Turn

#### Scenario: v2 persistent Run via async with
- **WHEN** `async with agent.run("prompt", ...) as run:` is used
- **THEN** a RunHandle is returned
- **AND** `run.start("prompt")` can be iterated across multiple Turns
- **AND** between Turns, `start()` blocks on `idle_event.wait()`
- **AND** a separate task calling `run.steer("new message")` wakes the RunHandle
- **AND** exiting `async with` calls `run.close()`

### Requirement: EventMapper extracts event mapping from RunExecutor

The system SHALL extract pydantic-ai event → `RichAgentStreamEvent` mapping logic from `RunExecutor` (L220-283) into a shared `EventMapper` class. `NativeTurn` SHALL use `EventMapper` to map events.

- `EventMapper` SHALL track pending tool calls by `tool_call_id`
- `EventMapper` SHALL map `FunctionToolCallEvent` → `ToolCallStartEvent` with `tool_call_id`, `title`, `raw_input`, `agent_name`, `message_id`
- `EventMapper` SHALL map `FunctionToolResultEvent` → `ToolCallCompleteEvent` with `tool_result`, `tool_input`, `agent_name`, `message_id`
- `EventMapper` SHALL pass through unmatched pydantic-ai events unchanged (documented behavior)
- `EventMapper` SHALL be constructed with `agent_name` and `message_id` parameters

#### Scenario: EventMapper maps tool call events
- **WHEN** pydantic-ai yields `FunctionToolCallEvent` with `tool_call_id="abc"`
- **THEN** `EventMapper` emits `ToolCallStartEvent(tool_call_id="abc", title=..., raw_input=..., agent_name=..., message_id=...)`
- **AND** stores pending call info keyed by `tool_call_id`

#### Scenario: EventMapper maps tool result events
- **WHEN** pydantic-ai yields `FunctionToolResultEvent` with matching `tool_call_id`
- **THEN** `EventMapper` emits `ToolCallCompleteEvent(tool_result=..., tool_input=..., agent_name=..., message_id=...)`
- **AND** clears the pending call entry
