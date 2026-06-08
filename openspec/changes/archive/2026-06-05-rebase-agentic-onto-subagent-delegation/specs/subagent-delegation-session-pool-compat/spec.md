## ADDED Requirements

### Requirement: Subagent delegation works in SessionPool mode
When the ACP server operates with `use_session_pool=true` (SessionPool-backed prompt handling via `ACPProtocolHandler`), subagent delegation policies configured via `PromptDelegation` SHALL still be honored.

#### Scenario: Auto policy with SessionPool
- **WHEN** a prompt request includes `delegation.policy="auto"` and `use_session_pool=true`
- **THEN** the SessionPool processes the prompt normally through the main agent without blocking subagent tools

#### Scenario: Disable policy with SessionPool
- **WHEN** a prompt request includes `delegation.policy="disable"` and `use_session_pool=true`
- **THEN** subagent tools are disabled for that turn before the prompt is submitted to the SessionPool

#### Scenario: Prefer policy with SessionPool
- **WHEN** a prompt request includes `delegation.policy="prefer"` and `use_session_pool=true` and the specified subagent exists in the pool
- **THEN** the prompt is routed directly to the subagent via the SessionPool instead of the main agent

#### Scenario: Require policy with SessionPool
- **WHEN** a prompt request includes `delegation.policy="require"` and `use_session_pool=true` and the specified subagent does not exist
- **THEN** the system returns a `RequestError` with code `-32602` before invoking the SessionPool
