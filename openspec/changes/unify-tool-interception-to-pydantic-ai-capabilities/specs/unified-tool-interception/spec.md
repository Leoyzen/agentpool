## ADDED Requirements

### Requirement: NativeAgentHookManager provides unified tool interception via pydantic-ai capabilities

`NativeAgentHookManager.as_capability()` SHALL return a `CombinedCapability` that provides uniform tool interception across all tool sources (direct tools, MCP tools, ACP MCP tools) through pydantic-ai's `AbstractCapability` chain.

#### Scenario: Capability always registers regardless of old hook mechanism

- **WHEN** `get_agentlet()` assembles capabilities for a native agent
- **THEN** `NativeAgentHookManager.as_capability()` is always appended to `tool_capabilities`, regardless of whether `self.hooks` (old `AgentHooks` mechanism) is set

#### Scenario: Capability applies to all tool sources

- **WHEN** a model calls any tool — whether registered via `wrap_tool()` (direct tools), `MCP` capability (MCP tools), or `Toolset` capability (ACP MCP tools)
- **THEN** the capability's `before_tool_execute`, `wrap_tool_execute`, and `after_tool_execute` hooks fire for that tool call

### Requirement: Tool confirmation mode is implemented via get_wrapper_toolset with ApprovalRequiredToolset

`tool_confirmation_mode` (always/never/per_tool) SHALL be implemented by wrapping the assembled toolset with pydantic-ai's `ApprovalRequiredToolset` in the capability's `get_wrapper_toolset()` method. This uses pydantic-ai's native deferred tool mechanism (`ApprovalRequired` → `HandleDeferredToolCalls` → `approval_bridge` → `InputProvider`).

#### Scenario: mode="always" forces confirmation for all tools

- **WHEN** `tool_confirmation_mode` is `"always"`
- **THEN** `get_wrapper_toolset()` wraps the toolset with `ApprovalRequiredToolset` using `approval_required_func=lambda *_: True`
- **AND** pydantic-ai defers all tool calls, routing each through `HandleDeferredToolCalls` → `approval_bridge` → `InputProvider.get_tool_confirmation()`

#### Scenario: mode="never" skips confirmation for all tools

- **WHEN** `tool_confirmation_mode` is `"never"`
- **THEN** `get_wrapper_toolset()` returns `None` (no wrapper), causing pydantic-ai to execute all tools without deferral
- **AND** the redundant `mode == "never"` auto-approval check in `approval_bridge.py` is removed

#### Scenario: mode="per_tool" respects individual tool configuration

- **WHEN** `tool_confirmation_mode` is `"per_tool"`
- **THEN** `get_wrapper_toolset()` wraps the toolset with `ApprovalRequiredToolset` using a function that checks each tool's `requires_confirmation` flag (looked up from `ToolManager` by tool name)
- **AND** only tools with `requires_confirmation=True` trigger deferral

### Requirement: prepare_tools provides schema modification for all tools

The capability's `prepare_tools()` SHALL allow modification of `ToolDefinition` objects (name, description, parameters_json_schema) before the model sees them. This is reserved for schema-level changes (e.g., injecting metadata, modifying descriptions), NOT for confirmation control.

#### Scenario: Schema modification for dynamic MCP tools

- **WHEN** a tool originates from a dynamic MCP bridge
- **THEN** `prepare_tools()` MAY modify the tool's `description` or `parameters_json_schema` to add bridge-specific metadata
- **AND** modifications use `dataclasses.replace()` to create new `ToolDefinition` instances without mutating shared state

#### Scenario: Schema modification preserves non-dynamic tools unchanged

- **WHEN** a tool does not originate from a dynamic MCP bridge
- **THEN** `prepare_tools()` returns the `ToolDefinition` unchanged

### Requirement: before_tool_execute handles pre-tool hooks

The capability's `before_tool_execute()` SHALL execute pre-tool hooks (from `AgentHooks`) and handle their results.

#### Scenario: Pre-tool hook modifies tool arguments

- **WHEN** a configured `pre_tool_use` hook returns `modified_input`
- **THEN** `before_tool_execute()` merges `modified_input` into the validated tool arguments before execution

#### Scenario: Pre-tool hook deny blocks tool execution

- **WHEN** a configured `pre_tool_use` hook returns `decision="deny"`
- **THEN** `before_tool_execute()` raises `ModelRetry` (or equivalent mechanism validated by spike task 5.0) with the hook's reason, asking the model to try a different approach instead of aborting the entire run

### Requirement: wrap_tool_execute provides error handling with failure annotation

The capability's `wrap_tool_execute()` SHALL catch tool execution exceptions and return annotated error results instead of propagating exceptions.

#### Scenario: Tool execution failure returns annotated error

- **WHEN** a tool raises an exception during execution
- **THEN** `wrap_tool_execute()` catches the exception and returns a `ToolReturn` with content describing the failure, including the tool name and error message
- **AND** the model receives the annotated error as the tool result, enabling it to recover or try alternatives

#### Scenario: Successful tool execution passes through unchanged

- **WHEN** a tool executes successfully
- **THEN** `wrap_tool_execute()` returns the original result without modification

### Requirement: after_tool_execute handles hook callbacks, injection consumption, and result modification

The capability's `after_tool_execute()` SHALL execute post-tool hooks (from `AgentHooks`), consume pending prompt injections from `PromptInjectionManager`, and apply `modified_output` and `additional_context` from hook results.

#### Scenario: Post-tool hook modified_output replaces tool result

- **WHEN** a configured `post_tool_use` hook returns `modified_output`
- **THEN** `after_tool_execute()` replaces the tool result with `modified_output` before returning it to pydantic-ai

#### Scenario: Post-tool hook additional_context is appended to result

- **WHEN** a configured `post_tool_use` hook returns `additional_context`
- **THEN** `after_tool_execute()` appends the context to the tool result via `_inject_additional_context()` before returning it to pydantic-ai

#### Scenario: Pending injection is consumed after tool execution

- **WHEN** `PromptInjectionManager` has a pending injection queued
- **THEN** `after_tool_execute()` consumes it and appends it to the tool result via `_inject_additional_context()`

### Requirement: wrap_tool is simplified to only handle AgentContext injection

`wrap_tool()` SHALL no longer call `handle_confirmation()` or `_execute_with_hooks()`. It SHALL only handle AgentContext injection and deferred execution support for legacy direct tools.

#### Scenario: Confirmation is no longer handled by wrap_tool

- **WHEN** a direct tool is executed
- **THEN** `wrap_tool()` does NOT call `handle_confirmation()` — confirmation is handled by the capability chain's `get_wrapper_toolset()` + `ApprovalRequiredToolset` + `HandleDeferredToolCalls`

#### Scenario: Hooks are no longer executed by wrap_tool

- **WHEN** a direct tool is executed
- **THEN** `wrap_tool()` does NOT call `run_pre_tool_hooks()` or `run_post_tool_hooks()` — hooks are handled by the capability chain's `before_tool_execute()` and `after_tool_execute()`

#### Scenario: AgentContext injection is preserved

- **WHEN** a direct tool requires `AgentContext` injection
- **THEN** `wrap_tool()` still injects `AgentContext` into the tool's kwargs as before

#### Scenario: Deferred execution path is preserved

- **WHEN** a tool with `deferred=True` raises `CallDeferred` or `ApprovalRequired` during body execution
- **THEN** `wrap_tool()` still catches these exceptions and routes them through `_handle_deferred_exception()`
