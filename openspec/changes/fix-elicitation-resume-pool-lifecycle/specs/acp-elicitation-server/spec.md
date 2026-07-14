## MODIFIED Requirements

### Requirement: RunErrorEvent stop reason

The ACP event converter SHALL map `RunErrorEvent` to `TurnCompleteUpdate(stop_reason="refusal")`, not `"end_turn"`. This signals to ACP clients that the turn ended abnormally (e.g., elicitation timeout, model error), distinguishing it from successful completion.

#### Scenario: Elicitation timeout sends refusal stop reason
- **WHEN** an elicitation timeout causes `RunAbortedError` → `RunErrorEvent`
- **THEN** the event converter yields `TurnCompleteUpdate(stop_reason="refusal")`
- **AND** the ACP client receives `stop_reason="refusal"`, not `"end_turn"`
- **AND** the client does not treat the turn as successfully completed

#### Scenario: Normal completion still sends end_turn
- **WHEN** the agent completes normally → `StreamCompleteEvent`
- **THEN** the event converter yields `TurnCompleteUpdate(stop_reason="end_turn")`
- **AND** the ACP client receives `stop_reason="end_turn"` as before
