## ADDED Requirements

### Requirement: AgentPool uses PydanticAI ProcessHistory capability directly
AgentPool SHALL use `pydantic_ai.capabilities.ProcessHistory` for history processing on native agents. The custom `ProcessHistoryAdapter` class SHALL be removed. Custom history processors (compaction, etc.) SHALL be registered as callbacks on PydanticAI's `ProcessHistory` capability.

#### Scenario: Native agent uses PydanticAI ProcessHistory
- **WHEN** a native agent is created via `get_agentlet()` with history processors configured
- **THEN** the agent's capabilities list includes a `pydantic_ai.capabilities.ProcessHistory` instance
- **AND** custom history processors (compaction, etc.) are registered as callbacks on the `ProcessHistory` capability
- **AND** no `ProcessHistoryAdapter` instance is present in the capability pipeline

#### Scenario: History processor callback fires at correct time
- **WHEN** a native agent is about to make a model request with existing message history
- **THEN** the `ProcessHistory` callback fires with the current message history
- **AND** the callback can modify the history (e.g., compact old messages)
- **AND** the modified history is used for the model request

#### Scenario: Multiple history processors execute in order
- **WHEN** multiple history processors are configured (e.g., compaction + token limit trimming)
- **THEN** they execute in the order they were registered as `ProcessHistory` callbacks
- **AND** each processor receives the output of the previous processor

## REMOVED Requirements

### Requirement: Custom ProcessHistoryAdapter with caching and signature validation
**Reason**: PydanticAI's `ProcessHistory` capability provides the same functionality with a simpler API. The caching layer in `ProcessHistoryAdapter` was needed because AgentPool rebuilt the agent per-run, but PydanticAI's `ProcessHistory` already handles lifecycle efficiently. Signature validation was for detecting config changes — now handled by the capability rebuild mechanism in `get_agentlet()`.
**Migration**: Register history processors directly as callbacks on `pydantic_ai.capabilities.ProcessHistory` instead of wrapping them in `ProcessHistoryAdapter`.
