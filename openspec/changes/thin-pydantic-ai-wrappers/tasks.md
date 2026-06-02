## 1. Foundation: Capability Collection + EventBus Adapter (Phase 2a)

- [ ] 1.1 Add `as_capability()` abstract method to `ResourceProvider` base class
- [ ] 1.2 Implement `EventBusHooksAdapter` — pydantic-ai `Hooks` capability that publishes to AgentPool `EventBus`
- [ ] 1.3 Implement `as_capability()` for builtin tool providers returning `AbstractToolset`
- [ ] 1.4 Implement `as_capability()` for custom tool providers
- [ ] 1.5 Implement `AgentHooks.as_capability()` returning pydantic-ai `Hooks` instance
- [ ] 1.6 Implement `MCPManager.as_capability()` returning pydantic-ai `MCP` instance
- [ ] 1.7 Implement history processor wrapper as `ProcessHistory` capability
- [ ] 1.8 Convert `SystemPrompts` to pydantic-ai `instructions` parameter format
- [ ] 1.9 Write tests for each provider's `as_capability()` method
- [ ] 1.10 Write tests for `EventBusHooksAdapter` publishing lifecycle events
- [ ] 1.11 Run full test suite for capability foundation

## 2. get_agentlet() Refactor (Phase 2b)

- [ ] 2.1 Produce line-by-line audit of current `get_agentlet()` vs capability-based construction
- [ ] 2.2 Refactor `NativeAgent.get_agentlet()` to collect capabilities via `as_capability()` from all providers
- [ ] 2.3 Remove manual tool flattening (`tools=[...]`) in favor of `capabilities=[...]`
- [ ] 2.4 Remove manual hook resolution in favor of capability collection + EventBus adapter
- [ ] 2.5 Remove manual MCP tool discovery in favor of `MCP` capability
- [ ] 2.6 Remove manual history processor resolution in favor of `ProcessHistory` capability
- [ ] 2.7 Pass `instructions=[...]` directly instead of `wrap_instruction()`
- [ ] 2.8 Write tests verifying `get_agentlet()` constructs agent with unified capabilities
- [ ] 2.9 Run full test suite for native agent construction

## 3. Direct Capability Passthrough (Phase 2c)

- [ ] 3.1 Add `capabilities` field to `NativeAgentConfig` accepting `AbstractCapability` instances or `CapabilityConfig`
- [ ] 3.2 Implement `CapabilityConfig` model for YAML-configured capabilities (type + args)
- [ ] 3.3 Update `get_agentlet()` to merge user-provided capabilities with internal capabilities
- [ ] 3.4 Support YAML `capabilities:` list with import path resolution (e.g., `pydantic_ai.capabilities.Instrumentation`)
- [ ] 3.5 Write tests for direct capability passthrough from Python API
- [ ] 3.6 Write tests for capability configuration from YAML
- [ ] 3.7 Write tests verifying user-provided capabilities take precedence over internal ones
- [ ] 3.8 Run full test suite for capability passthrough

## 4. Tool Confirmation Bridge (Phase 2d)

- [ ] 4.1 Map `Tool.requires_confirmation` to pydantic-ai `ApprovalRequiredToolset`
- [ ] 4.2 Bridge `ApprovalRequiredToolset` denial signals to AgentPool `InputProvider` flow
- [ ] 4.3 Ensure `handle_confirmation()` UI integration works with capability-based tools
- [ ] 4.4 Write tests for tool confirmation with capability-based toolsets
- [ ] 4.5 Run full test suite for tool confirmation

## 5. Backward Compatibility Shim (Phase 2e)

- [ ] 5.1 Implement `ToolManager` shim that delegates to `ResourceProvider.as_capability()`
- [ ] 5.2 Implement `AgentHooks` shim that delegates to `Hooks` capability
- [ ] 5.3 Implement `MCPManager` shim that delegates to `MCP` capability
- [ ] 5.4 Implement `_resolve_history_processors()` shim that delegates to `ProcessHistory`
- [ ] 5.5 Implement `SystemPrompts`/`wrap_instruction()` shim that delegates to `instructions` parameter
- [ ] 5.6 Add `DeprecationWarning` to all shim methods (target removal: v0.5.0)
- [ ] 5.7 Define shim API contract and deprecation timeline in docs
- [ ] 5.8 Write backward-compat tests ensuring existing API usage still works
- [ ] 5.9 Write tests verifying deprecation warnings are emitted correctly

## 6. Test Migration Inventory (Phase 2f)

- [ ] 6.1 Migrate `tests/tools/test_manager.py` from `ToolManager` to capability construction
- [ ] 6.2 Migrate `tests/hooks/test_hooks.py` from `AgentHooks` to `Hooks` capability
- [ ] 6.3 Migrate `tests/hooks/test_native_agent_hook_manager.py` hook wrapping tests
- [ ] 6.4 Migrate `tests/mcp_server/test_manager.py` from `MCPManager` to `MCP` capability
- [ ] 6.5 Update `tests/agents/test_native_agent.py` `get_agentlet()` tests
- [ ] 6.6 Add capability passthrough tests to `tests/config/test_yaml_loading.py`
- [ ] 6.7 Run complete test suite and verify all tests pass

## 7. Stabilization (Phase 2g — 2+ weeks)

- [ ] 7.1 Monitor production/staging for capability-related issues
- [ ] 7.2 Fix edge cases in capability collection
- [ ] 7.3 Benchmark capability overhead vs old manager approach
- [ ] 7.4 Verify EventBus adapter handles all lifecycle events correctly
- [ ] 7.5 Pin pydantic-ai version in `pyproject.toml`: `>=1.102.0,<2.0.0`
- [ ] 7.6 Add CI job testing against pydantic-ai main branch

## 8. Cleanup (Phase 2h — after v0.5.0)

- [ ] 8.1 Remove shim layers (2 release cycles after merge)
- [ ] 8.2 Remove deprecated `ToolManager`, `AgentHooks`, `MCPManager` direct usage paths
- [ ] 8.3 Update documentation to remove deprecation notices
- [ ] 8.4 Archive shim layer code to `agentpool/compat/` if needed

## 9. Event Stream Thinning (Phase 2i — separate, after stabilization)

- [ ] 9.1 Audit `RichAgentStreamEvent` hierarchy and identify pure pydantic-ai pass-through events
- [ ] 9.2 Update event stream to propagate pydantic-ai native events directly where they overlap
- [ ] 9.3 Ensure AgentPool-specific events (`SubAgentEvent`, `ToolCallProgressEvent`, `StreamCompleteEvent`) are still created
- [ ] 9.4 Update protocol servers (ACP/AG-UI/OpenCode) to handle both pydantic-ai native events and AgentPool-specific events
- [ ] 9.5 Write tests verifying correct event types are emitted during streaming
- [ ] 9.6 Run full test suite for event streaming functionality
