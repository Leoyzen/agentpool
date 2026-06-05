## 0. Statelessness Prerequisite

- [x] 0.1 Remove `self.session_id` mutation from `BaseAgent` (`__init__`, `set_session_context()`, `run_stream()`)
- [x] 0.2 Remove `agent.session_id = session_id` from `TurnRunner._run_turn_unlocked()`
- [x] 0.3 Make `NativeAgent._stream_events()` use `session_id` parameter instead of `self.session_id`
- [x] 0.4 Update `ClaudeCodeAgent`, `ACPAgent`, `AGUINode` to use param-based session_id
- [x] 0.5 Verify all agents work without `self.session_id` being set

## 1. Foundation: Capability Collection + EventBus Adapter (Phase 2a)

- [x] 1.1 Add `as_capability()` abstract method to `ResourceProvider` base class
- [x] 1.2 Implement `EventBusHooksAdapter` — pydantic-ai `Hooks` capability that publishes to AgentPool `EventBus`
- [x] 1.3 Implement `as_capability()` for builtin tool providers returning `AbstractToolset`
- [x] 1.4 Implement `as_capability()` for custom tool providers
- [x] 1.5 Implement `AgentHooks.as_capability()` returning pydantic-ai `Hooks` instance
- [x] 1.6 Implement `MCPManager.as_capability()` returning pydantic-ai `MCP` instance
- [x] 1.7 Implement history processor wrapper as `ProcessHistory` capability
- [x] 1.8 Convert `SystemPrompts` to pydantic-ai `instructions` parameter format
- [x] 1.9 Write tests for each provider's `as_capability()` method
- [x] 1.10 Write tests for `EventBusHooksAdapter` publishing lifecycle events
- [x] 1.11 Run full test suite for capability foundation

## 2. get_agentlet() Refactor (Phase 2b)

- [x] 2.1 Produce line-by-line audit of current `get_agentlet()` vs capability-based construction
- [x] 2.2 Refactor `NativeAgent.get_agentlet()` to collect capabilities via `as_capability()` from all providers
- [x] 2.3 Remove manual tool flattening (`tools=[...]`) in favor of `capabilities=[...]`
- [x] 2.4 Remove manual hook resolution in favor of capability collection + EventBus adapter
- [x] 2.5 Remove manual MCP tool discovery in favor of `MCP` capability
- [x] 2.6 Remove manual history processor resolution in favor of `ProcessHistory` capability
- [x] 2.7 Pass `instructions=[...]` directly instead of `wrap_instruction()`
- [x] 2.8 Write tests verifying `get_agentlet()` constructs agent with unified capabilities
- [x] 2.9 Run full test suite for native agent construction

## 3. Direct Capability Passthrough (Phase 2c)

- [x] 3.1 Add `capabilities` field to `NativeAgentConfig` accepting `AbstractCapability` instances or `CapabilityConfig`
- [x] 3.2 Implement `CapabilityConfig` model for YAML-configured capabilities (type + args)
- [x] 3.3 Update `get_agentlet()` to merge user-provided capabilities with internal capabilities
- [x] 3.4 Support YAML `capabilities:` list with import path resolution (e.g., `pydantic_ai.capabilities.Instrumentation`)
- [x] 3.5 Write tests for direct capability passthrough from Python API
- [x] 3.6 Write tests for capability configuration from YAML
- [x] 3.7 Write tests verifying user-provided capabilities take precedence over internal ones
- [x] 3.8 Run full test suite for capability passthrough

## 4. Tool Confirmation Bridge (Phase 2d)

- [x] 4.1 Map `Tool.requires_confirmation` to pydantic-ai `ApprovalRequiredToolset`
- [x] 4.2 Bridge `ApprovalRequiredToolset` denial signals to AgentPool `InputProvider` flow
- [x] 4.3 Ensure `handle_confirmation()` UI integration works with capability-based tools
- [x] 4.4 Write tests for tool confirmation with capability-based toolsets
- [x] 4.5 Run full test suite for tool confirmation

## 5. Backward Compatibility Shim (Phase 2e)

- [x] 5.1 Implement `ToolManager` shim that delegates to `ResourceProvider.as_capability()`
- [x] 5.2 Implement `AgentHooks` shim that delegates to `Hooks` capability
- [x] 5.3 Implement `MCPManager` shim that delegates to `MCP` capability
- [x] 5.4 Implement `_resolve_history_processors()` shim that delegates to `ProcessHistory`
- [x] 5.5 Implement `SystemPrompts`/`wrap_instruction()` shim that delegates to `instructions` parameter
- [x] 5.6 Add `DeprecationWarning` to all shim methods (target removal: v0.5.0)
- [x] 5.7 Define shim API contract and deprecation timeline in docs
- [x] 5.8 Write backward-compat tests ensuring existing API usage still works
- [x] 5.9 Write tests verifying deprecation warnings are emitted correctly

## 6. Test Migration Inventory (Phase 2f)

- [x] 6.1 Migrate `tests/tools/test_manager.py` from `ToolManager` to capability construction
- [x] 6.2 Migrate `tests/hooks/test_hooks.py` from `AgentHooks` to `Hooks` capability
- [x] 6.3 Migrate `tests/hooks/test_native_agent_hook_manager.py` hook wrapping tests
- [x] 6.4 Migrate `tests/mcp_server/test_manager.py` from `MCPManager` to `MCP` capability
- [x] 6.5 Update `tests/agents/test_native_agent.py` `get_agentlet()` tests
- [x] 6.6 Add capability passthrough tests to `tests/config/test_yaml_loading.py`
- [x] 6.7 Run complete test suite and verify all tests pass

## 7. Stabilization (Phase 2g — 2+ weeks)

- [x] 7.1 Monitor production/staging for capability-related issues
- [x] 7.2 Fix edge cases in capability collection
- [x] 7.3 Benchmark capability overhead vs old manager approach
- [x] 7.4 Verify EventBus adapter handles all lifecycle events correctly
- [x] 7.5 Pin pydantic-ai version in `pyproject.toml`: `>=1.102.0,<2.0.0`
- [x] 7.6 Add CI job testing against pydantic-ai main branch

## 8. Cleanup (Phase 2h — after v0.5.0)

- [x] 8.1 Remove shim layers (2 release cycles after merge)
- [x] 8.2 Remove deprecated `ToolManager`, `AgentHooks`, `MCPManager` direct usage paths
- [x] 8.3 Update documentation to remove deprecation notices
- [x] 8.4 Archive shim layer code to `agentpool/compat/` if needed

## 9. Event Stream Thinning (Phase 2i — separate, after stabilization)

- [x] 9.1 Audit `RichAgentStreamEvent` hierarchy and identify pure pydantic-ai pass-through events
- [x] 9.2 Update event stream to propagate pydantic-ai native events directly where they overlap
- [x] 9.3 Ensure AgentPool-specific events (`SubAgentEvent`, `ToolCallProgressEvent`, `StreamCompleteEvent`) are still created
- [x] 9.4 Update protocol servers (ACP/AG-UI/OpenCode) to handle both pydantic-ai native events and AgentPool-specific events
- [x] 9.5 Write tests verifying correct event types are emitted during streaming
- [x] 9.6 Run full test suite for event streaming functionality
