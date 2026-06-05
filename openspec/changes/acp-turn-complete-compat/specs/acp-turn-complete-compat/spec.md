## ADDED Requirements

### Requirement: Client capability negotiation for turn_complete
The ACP server SHALL allow clients to declare `turn_complete` support via `ClientCapabilities`.

#### Scenario: Client declares turn_complete support
- **WHEN** a client sends `initialize` with `clientCapabilities.turnComplete = true`
- **THEN** the server stores this capability for the session

#### Scenario: Client does not declare turn_complete support
- **WHEN** a client sends `initialize` without `turn_complete` in `clientCapabilities`
- **THEN** the server treats the client as not supporting `turn_complete`

### Requirement: Capability-gated turn_complete advertisement
The ACP server SHALL only advertise `turn_complete` in `InitializeResponse` when the client declares support.

#### Scenario: Supported client receives advertisement
- **WHEN** the client declares `turn_complete` support
- **THEN** `InitializeResponse.agentCapabilities.turnComplete` is present and truthy

#### Scenario: Legacy client receives no advertisement
- **WHEN** the client does not declare `turn_complete` support
- **THEN** `InitializeResponse.agentCapabilities.turnComplete` is absent or falsy

### Requirement: Capability-gated TurnCompleteUpdate emission
The ACP server SHALL only emit `TurnCompleteUpdate` session updates when the client supports `turn_complete`.

#### Scenario: Stream completes for supported client
- **WHEN** an agent stream completes for a client that supports `turn_complete`
- **THEN** the server emits `session/update` with `sessionUpdate: "turn_complete"`

#### Scenario: Stream completes for legacy client
- **WHEN** an agent stream completes for a client that does not support `turn_complete`
- **THEN** the server does NOT emit `session/update` with `sessionUpdate: "turn_complete"`

### Requirement: Legacy client PromptResponse timing
For clients that do not support `turn_complete`, the ACP server SHALL return `PromptResponse` only after the agent stream has fully completed.

#### Scenario: Legacy client prompt handling via SessionPool
- **WHEN** a legacy client sends `session/prompt` and the server uses the SessionPool path
- **THEN** the server blocks the `PromptResponse` until all stream events are emitted
- **AND** the client receives stream events before the `PromptResponse`

#### Scenario: Supported client prompt handling via SessionPool
- **WHEN** a client that supports `turn_complete` sends `session/prompt`
- **THEN** the server may return `PromptResponse` immediately (fire-and-forget)
- **AND** the client receives `TurnCompleteUpdate` as the end-of-turn signal
