## ADDED Requirements

### Requirement: ACPClientProtocol SHALL be redefined for non-blocking semantics

The `ACPClientProtocol` interface SHALL be modified to support non-blocking prompt semantics. The `prompt()` method SHALL return `None` (not `PromptResponse`). The `stream_events()` method SHALL take no `response` parameter and SHALL return an `AsyncIterator[SessionUpdate]`. A `stop_reason` property SHALL be added to expose the `PromptResponse.stop_reason` after streaming completes. This is an internal interface change — `ACPClientProtocol` is only implemented by `ACPClientAdapter` and consumed by `ACPTurn`.

#### Scenario: prompt returns None
- **WHEN** `ACPClientAdapter.prompt()` is called
- **THEN** the adapter SHALL launch `api.prompt()` as a background asyncio task (fire-and-forget)
- **AND** SHALL return `None` immediately without waiting for the prompt to complete
- **AND** SHALL NOT block the calling coroutine

#### Scenario: stream_events takes no arguments
- **WHEN** `ACPClientAdapter.stream_events()` is called (with no arguments)
- **THEN** the adapter SHALL return an async iterator that yields ACP session update notifications
- **AND** notifications SHALL be pushed to the queue by `ACPClientHandler.session_update()` as they arrive
- **AND** the iterator SHALL yield notifications in order
- **AND** the iterator SHALL signal completion when the prompt background task completes

#### Scenario: stop_reason available after streaming
- **WHEN** the prompt background task completes
- **THEN** the adapter SHALL store the `PromptResponse` internally
- **AND** the `stop_reason` property SHALL return the `PromptResponse.stop_reason` value
- **AND** accessing `stop_reason` before streaming completes SHALL raise `RuntimeError("stop_reason not available until streaming completes")`

#### Scenario: Adapter get_messages retrieves history
- **WHEN** `ACPClientAdapter.get_messages()` is called after the prompt completes
- **THEN** the adapter SHALL call `api.get_messages()` and return the message history

### Requirement: ACPClientHandler SHALL bifurcate state updates and stream data

The `ACPClientHandler.session_update()` method SHALL process state updates (model, mode, config, commands) in-place and push only stream-data updates (text chunks, tool calls, thoughts) to the async queue. State updates (`CurrentModeUpdate`, `CurrentModelUpdate`, `ConfigOptionUpdate`, `AvailableCommandsUpdate`) SHALL NOT be pushed to the stream queue — they SHALL be processed by the handler directly, preserving the existing state tracking behavior. Stream-data updates (`AgentMessageChunk`, `ToolCallStart`, `ToolCallComplete`, etc.) SHALL be pushed to the async queue.

#### Scenario: State update processed in-place
- **WHEN** `ACPClientHandler.session_update()` receives a `CurrentModelUpdate` notification
- **THEN** the handler SHALL update its internal model state directly
- **AND** SHALL NOT push the update to the async queue

#### Scenario: Stream data pushed to queue
- **WHEN** `ACPClientHandler.session_update()` receives an `AgentMessageChunk` notification
- **THEN** the handler SHALL push the notification to the async queue
- **AND** SHALL NOT process it as a state update

### Requirement: ACPClientAdapter async queue SHALL be bounded

The async queue in `ACPClientAdapter` SHALL have a `max_buffer_size` of 1000 items to prevent unbounded memory growth. If the queue is full when a new notification arrives, the adapter SHALL apply backpressure by blocking the push until the consumer drains items.

#### Scenario: Queue backpressure
- **WHEN** the async queue has 1000 items and a new notification arrives
- **THEN** the push operation SHALL block until the consumer dequeues at least one item
- **AND** the ACP server SHALL be effectively throttled until the consumer catches up

### Requirement: ACPClientAdapter SHALL reject concurrent prompts

The `ACPClientAdapter` SHALL reject a new `prompt()` call while a previous prompt is still streaming. ACP sessions typically allow one active prompt at a time. This matches the current behavior where `ACPAgentAPI.prompt()` blocks until completion.

#### Scenario: Concurrent prompt rejected
- **WHEN** `adapter.prompt()` is called while a previous prompt's background task is still running
- **THEN** the adapter SHALL raise `RuntimeError("Prompt already in progress")`
- **AND** SHALL NOT launch a new background task

### Requirement: ACPTurn SHALL use ACPClientAdapter instead of cast hack

The `ACPAgent.create_turn()` method SHALL construct an `ACPClientAdapter` wrapping `self._api` and pass it to `ACPTurn`. The `cast("ACPClientProtocol", self._api)` hack SHALL be removed. `ACPTurn.execute()` SHALL call `adapter.prompt()`, then iterate `adapter.stream_events()`, then access `adapter.stop_reason`, and finally call `adapter.get_messages()`.

#### Scenario: ACPTurn executes successfully
- **WHEN** `ACPTurn.execute()` is called
- **THEN** the turn SHALL call `adapter.prompt()` (returns None, non-blocking)
- **AND** SHALL iterate `adapter.stream_events()` yielding each notification as a `RichAgentStreamEvent`
- **AND** SHALL access `adapter.stop_reason` after the stream completes
- **AND** SHALL call `adapter.get_messages()` after the stream completes
- **AND** SHALL return the final `ChatMessage[str]` result

#### Scenario: ACPTurn no longer uses cast
- **WHEN** `ACPAgent.create_turn()` is called
- **THEN** it SHALL construct `ACPClientAdapter(self._api)` 
- **AND** SHALL NOT use `cast("ACPClientProtocol", self._api)`
