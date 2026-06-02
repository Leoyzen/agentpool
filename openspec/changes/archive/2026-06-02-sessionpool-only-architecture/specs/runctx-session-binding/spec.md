### Requirement: Interrupt uses run_ctx.current_task
`BaseAgent.interrupt()` SHALL cancel `run_ctx.current_task` instead of `_current_stream_task` or `_iteration_task`. This works uniformly across all agent types because `current_task` is stored in `AgentRunContext` by both legacy and SessionPool paths.

#### Scenario: Interrupt during SessionPool turn
- **WHEN** `interrupt()` is called during an active SessionPool-managed turn
- **THEN** it cancels `run_ctx.current_task`
- **AND** the agent stream terminates with `run_ctx.cancelled = True`

#### Scenario: Interrupt works for all agent types
- **WHEN** `interrupt()` is called on any agent type (Native, ClaudeCode, ACP)
- **THEN** it correctly cancels the active turn without relying on agent-type-specific task references