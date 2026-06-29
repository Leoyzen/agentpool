## ADDED Requirements

### Requirement: v2 session/prompt SHALL return immediately upon acceptance

v2 `session/prompt` SHALL respond with an empty `result: {}` as soon as the prompt is accepted, NOT when the turn completes. The turn's outcome SHALL be communicated via `state_update` notifications.

#### Scenario: Prompt accepted immediately

- **WHEN** v2 client sends `session/prompt` with a user message
- **THEN** agent SHALL return `{"jsonrpc":"2.0","id":"<req_id>","result":{}}` before processing begins

#### Scenario: Turn completion via state_update

- **WHEN** agent finishes processing the turn
- **THEN** agent SHALL send `session/update` with `state_update` `state="idle"` and `stopReason="end_turn"` (or other valid stop reason)

### Requirement: v2 agent SHALL send user_message notification upon prompt acceptance

After accepting a `session/prompt`, v2 agent SHALL send a `session/update` notification with `sessionUpdate: "user_message"` containing the accepted prompt content and the agent-assigned `messageId`.

#### Scenario: User message acknowledged

- **WHEN** v2 client sends `session/prompt` with prompt `[{type:"text", text:"Hello"}]`
- **THEN** agent SHALL send `session/update` with `user_message` containing the prompt content and a `messageId` assigned by the agent

#### Scenario: Multiple clients receive user_message

- **WHEN** two v2 clients are connected to the same session and client A sends a prompt
- **THEN** both client A and client B SHALL receive the `user_message` notification with the same `messageId`

### Requirement: v2 agent SHALL send state_update on state transitions

v2 agent SHALL send `state_update` notifications when the session state changes: `running` when a turn begins, `idle` when a turn ends (with `stopReason`), and `requires_action` when user input is needed.

#### Scenario: Turn lifecycle

- **WHEN** agent accepts a prompt and begins processing
- **THEN** agent SHALL send `state_update` with `state="running"`
- **WHEN** agent completes the turn
- **THEN** agent SHALL send `state_update` with `state="idle"` and `stopReason`

#### Scenario: Requires action during turn

- **WHEN** agent needs user permission for a tool call during a turn
- **THEN** agent SHALL send `state_update` with `state="requires_action"`
- **WHEN** user grants permission
- **THEN** agent SHALL send `state_update` with `state="running"`

### Requirement: v2 agent SHALL support out-of-turn updates

v2 agent SHALL be able to send `session/update` notifications outside of an active turn (e.g., background subagent completion, deferred tool results).

#### Scenario: Background task completion notification

- **WHEN** a background subagent completes after the main turn has ended (state is `idle`)
- **THEN** agent SHALL send `session/update` with the subagent's results without requiring a new prompt

#### Scenario: No state_change required for content updates

- **WHEN** agent sends an out-of-turn `agent_message` or `tool_call_update`
- **THEN** the current `state` SHALL remain `idle` (content updates do not imply state changes)
