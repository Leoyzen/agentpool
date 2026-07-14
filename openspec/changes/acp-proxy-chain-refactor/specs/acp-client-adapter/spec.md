## ADDED Requirements

### Requirement: ACPClientAdapter SHALL bridge ACPAgentAPI to ACPClientProtocol

The `ACPClientAdapter` class SHALL wrap `ACPAgentAPI` to implement the `ACPClientProtocol` interface. This adapter SHALL make `ACPTurn` functional by providing the three methods that `ACPClientProtocol` requires: `prompt()`, `stream_events()`, and `get_messages()`. This implements the "Future Work" described in `unify-hook-system` Section 11 (tasks 11.1-11.2).

#### Scenario: Adapter prompt is non-blocking
- **WHEN** `ACPClientAdapter.prompt()` is called
- **THEN** the adapter SHALL launch `api.prompt()` as a background asyncio task (fire-and-forget)
- **AND** SHALL return immediately without waiting for the prompt to complete
- **AND** SHALL NOT block the calling coroutine

#### Scenario: Adapter stream_events returns async iterator
- **WHEN** `ACPClientAdapter.stream_events()` is called
- **THEN** the adapter SHALL return an async iterator (asyncio.Queue) that yields ACP session update notifications
- **AND** notifications SHALL be pushed to the queue by `ACPClientHandler.session_update()` as they arrive
- **AND** the iterator SHALL yield notifications in order
- **AND** the iterator SHALL signal completion when the prompt background task completes

#### Scenario: Adapter get_messages retrieves history
- **WHEN** `ACPClientAdapter.get_messages()` is called after the prompt completes
- **THEN** the adapter SHALL call `api.get_messages()` and return the message history

### Requirement: ACPClientHandler SHALL push updates directly to async queue

The `ACPClientHandler.session_update()` method SHALL push session update notifications directly to an `asyncio.Queue` instead of appending to a deque. The `ACPClientAdapter` SHALL own this queue and expose it via `stream_events()`. The 50ms polling loop (`poll_acp_events()`) SHALL be eliminated.

#### Scenario: Session update pushes to queue
- **WHEN** `ACPClientHandler.session_update()` receives a notification from the ACP server
- **THEN** the handler SHALL push the notification to the async queue
- **AND** SHALL NOT use any polling or timeout mechanism

#### Scenario: No polling for events
- **WHEN** the ACP agent is streaming events
- **THEN** the system SHALL NOT use `poll_acp_events()` or any polling loop
- **AND** SHALL NOT use `TimeoutableEvent` with timeout values
- **AND** SHALL use pure async push via queue.get()

### Requirement: ACPTurn SHALL use ACPClientAdapter instead of cast hack

The `ACPAgent.create_turn()` method SHALL construct an `ACPClientAdapter` wrapping `self._api` and pass it to `ACPTurn`. The `cast("ACPClientProtocol", self._api)` hack SHALL be removed. `ACPTurn.execute()` SHALL call `adapter.prompt()`, then iterate `adapter.stream_events()`, then call `adapter.get_messages()`.

#### Scenario: ACPTurn executes successfully
- **WHEN** `ACPTurn.execute()` is called
- **THEN** the turn SHALL call `adapter.prompt()` (non-blocking)
- **AND** SHALL iterate `adapter.stream_events()` yielding each notification as a `RichAgentStreamEvent`
- **AND** SHALL call `adapter.get_messages()` after the stream completes
- **AND** SHALL return the final `ChatMessage[str]` result

#### Scenario: ACPTurn no longer uses cast
- **WHEN** `ACPAgent.create_turn()` is called
- **THEN** it SHALL construct `ACPClientAdapter(self._api)` 
- **AND** SHALL NOT use `cast("ACPClientProtocol", self._api)`
