## ADDED Requirements

### Requirement: AgentPool delegates hook lifecycle to PydanticAI Hooks capability
AgentPool SHALL use `pydantic_ai.capabilities.Hooks` as the primary hook lifecycle mechanism for native agents. `NativeAgentHookManager.as_capability()` SHALL return a `pydantic_ai.capabilities.Hooks` instance with typed callbacks (`before_run`/`after_run`/`before_tool_execute`/`after_tool_execute`). The custom `Hook` base class, `CallableHook`, regex matchers, timeout handling, and parallel result combining logic SHALL be removed.

#### Scenario: Native agent uses PydanticAI Hooks capability
- **WHEN** a native agent is created via `get_agentlet()`
- **THEN** the agent's capabilities list includes a `pydantic_ai.capabilities.Hooks` instance
- **AND** the `Hooks` instance has callbacks registered for `before_tool_execute` and `after_tool_execute`
- **AND** no custom `Hook` base class or `CallableHook` instances are present in the capability pipeline

#### Scenario: Multiple hooks combined with priority semantics
- **WHEN** multiple hooks are registered (e.g., two `before_tool_execute` hooks, one returning "deny" and one returning "allow")
- **THEN** the `Hooks` callback wrapper combines results using priority: deny > ask > allow
- **AND** the combined result is returned to PydanticAI as the single callback result

#### Scenario: Hook fires at correct lifecycle point
- **WHEN** a tool is about to execute during a native agent run
- **THEN** the `before_tool_execute` callback on the `Hooks` capability fires
- **AND** the callback receives `RunContext` and tool call details
- **AND** the callback can return a decision (allow/deny/ask) that PydanticAI respects

### Requirement: CommandHook and PromptHook survive as thin adapters over Hooks callbacks
AgentPool SHALL preserve `CommandHook` (subprocess evaluation) and `PromptHook` (LLM evaluation) as thin adapter classes that register their logic as `pydantic_ai.capabilities.Hooks` callbacks. They SHALL NOT inherit from a custom `Hook` base class. They SHALL implement their evaluation logic inside `Hooks` callback functions.

#### Scenario: CommandHook registers as Hooks callback
- **WHEN** a `CommandHook` is configured for an agent
- **THEN** it registers its subprocess evaluation logic as a `before_tool_execute` callback on the `Hooks` capability
- **AND** the subprocess is spawned with the tool call details as input
- **AND** the subprocess stdout is parsed as the hook result (allow/deny/ask)

#### Scenario: PromptHook registers as Hooks callback
- **WHEN** a `PromptHook` is configured for an agent
- **THEN** it registers its LLM evaluation logic as a `before_tool_execute` callback on the `Hooks` capability
- **AND** a mini PydanticAI `Agent` evaluates the tool call against the prompt template
- **AND** the LLM response is parsed as the hook result (allow/deny/ask)

### Requirement: Hook YAML config uses callback references instead of matcher/event/timeout
AgentPool hook YAML configuration SHALL use callback references (`before_run`/`after_run`/`before_tool_execute`/`after_tool_execute`) instead of `matcher`/`event`/`timeout` fields. A deprecation shim SHALL auto-translate old-format configs (`matcher`/`event`/`timeout`) to the new format during a transition period.

#### Scenario: New-format hook config
- **WHEN** a YAML config defines a hook with `before_tool_execute: "mymodule:check_tool"`
- **THEN** the hook is registered as a `before_tool_execute` callback on the `Hooks` capability
- **AND** no `matcher`/`event`/`timeout` fields are present

#### Scenario: Old-format hook config with deprecation shim
- **WHEN** a YAML config defines a hook with `event: "before_tool_execute"` and `matcher: "bash.*"`
- **THEN** the deprecation shim translates it to `before_tool_execute` callback with a wrapper that applies the regex matcher internally
- **AND** a `DeprecationWarning` is emitted with migration guidance

## REMOVED Requirements

### Requirement: Custom Hook base class with regex matchers and timeout handling
**Reason**: PydanticAI's `Hooks` capability provides the same lifecycle hooks with a cleaner API. Regex matchers, timeout handling, and parallel result combining are implementation details that belong inside callback wrappers, not in a base class hierarchy.
**Migration**: Replace `Hook` subclasses with `pydantic_ai.capabilities.Hooks` callbacks. Move regex matching and timeout logic inside the callback wrapper function.
