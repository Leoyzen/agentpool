## 1. Pre-Migration: Regression Test Baseline

- [x] 1.1 Write regression tests for all existing hook combination scenarios (deny > ask > allow priority, parallel hooks, timeout behavior) in `tests/hooks/test_hook_combining.py`
- [x] 1.2 Write regression tests for `ProcessHistoryAdapter` behavior (caching, signature validation, processor ordering) in `tests/agents/test_process_history.py`
- [x] 1.3 Write regression tests for `PromptInjectionManager` native path (inject/consume, queue/pop_queued, flush_pending_to_queue) in `tests/agents/test_prompt_injection.py`
- [x] 1.4 Write regression tests for `Tool.to_pydantic_ai()` conversion (schema overrides, deferred metadata, approval wrapping) in `tests/tools/test_tool_conversion.py`
- [x] 1.5 Write regression tests for event subclass behavior (PartStartEvent/PartDeltaEvent with session_id, ToolCallStartEvent/ToolCallCompleteEvent) in `tests/agents/test_events.py`
- [x] 1.6 Run full test suite to establish green baseline: `uv run pytest -m unit`

## 2. Phase 1: Hooks Migration

- [x] 2.1 Implement `HooksCapabilityAdapter` that wraps multiple AgentPool hooks into a single `pydantic_ai.capabilities.Hooks` instance in `src/agentpool/agents/native_agent/hooks_capability_adapter.py`
- [x] 2.2 Implement priority combining logic (deny > ask > allow) inside `HooksCapabilityAdapter` callbacks, preserving exact semantics of current parallel hook combining
- [x] 2.3 Migrate `CallableHook` to register its logic as a `Hooks` callback (no inheritance from custom `Hook` base class)
- [x] 2.4 Migrate `CommandHook` as a thin adapter that spawns subprocesses inside `Hooks.before_tool_execute` callback
- [x] 2.5 Migrate `PromptHook` as a thin adapter that runs LLM evaluation inside `Hooks.before_tool_execute` callback
- [x] 2.6 Update `NativeAgentHookManager.as_capability()` to return `HooksCapabilityAdapter` instead of wrapping `AgentHooks`
- [x] 2.7 Remove `Hook` base class, `CallableHook` class hierarchy, regex matchers, timeout handling from `src/agentpool/hooks/`
- [x] 2.8 Add deprecation shim for old-format YAML hook config (`matcher`/`event`/`timeout` → callback references) with `DeprecationWarning`
- [x] 2.9 Update `agentpool_config` hook config models to use callback references (`before_run`/`after_run`/`before_tool_execute`/`after_tool_execute`)
- [x] 2.10 Run hook regression tests and verify all pass: `uv run pytest tests/hooks/ -vv`
- [x] 2.11 Run full test suite: `uv run pytest -m unit`

## 3. Phase 2: ProcessHistory + PromptInjectionManager

- [x] 3.1 Replace `ProcessHistoryAdapter` with direct `pydantic_ai.capabilities.ProcessHistory` capability in `get_agentlet()` assembly
- [x] 3.2 Register custom history processors (compaction, token trimming) as callbacks on `ProcessHistory` capability
- [x] 3.3 Remove `ProcessHistoryAdapter` class and its caching/signature validation logic from `src/agentpool/agents/native_agent/process_history_capability.py`
- [x] 3.4 Remove `PromptInjectionManager.queue()`/`pop_queued()`/`flush_pending_to_queue()` calls from native agent path in `base_agent.py` and `TurnRunner`
- [x] 3.5 Keep `PromptInjectionManager.inject()`/`consume()` for tool result augmentation (update `Hooks` `after_tool_execute` callback to call `consume()`)
- [x] 3.6 Keep `PromptInjectionManager.queue()`/`pop_queued()`/`flush_pending_to_queue()` for ACP agent path only
- [x] 3.7 Remove native-agent-specific `_post_turn_prompts` and `_injection_locks` from `RunExecutor` if present
- [x] 3.8 Remove native-agent follow-up loop from `BaseAgent._run_stream_once()` (the `while has_queued()` branch for native agents)
- [x] 3.9 Run ProcessHistory and PromptInjection regression tests: `uv run pytest tests/agents/test_process_history.py tests/agents/test_prompt_injection.py -vv`
- [x] 3.10 Run full test suite: `uv run pytest -m unit`

## 4. Phase 3: Tool Thinning

- [x] 4.1 Remove `ToolKind` enum and all references from `src/agentpool/tools/base.py`
- [x] 4.2 Replace `ToolKind`-based config validation with string-based tool name patterns in `agentpool_config`
- [x] 4.3 Remove `ToolResult.structured_content` field, update tool implementations to use PydanticAI's `ToolReturn` natively
- [x] 4.4 Simplify `Tool.to_pydantic_ai()` to direct 1:1 mapping (target: <20 lines)
- [x] 4.5 Use PydanticAI's `requires_approval=True` directly for non-deferred confirmation tools
- [x] 4.6 Keep `ApprovalRequiredToolset` wrapping only for deferred execution tools
- [x] 4.7 Remove redundant metadata fields from `Tool` dataclass that `pydantic_ai.tools.Tool` already provides
- [x] 4.8 Run tool regression tests: `uv run pytest tests/tools/ -vv`
- [x] 4.9 Run full test suite: `uv run pytest -m unit`

## 5. Phase 3: Event Passthrough

- [x] 5.1 Remove `PartStartEvent(PyAIPartStartEvent)` and `PartDeltaEvent(PyAIPartDeltaEvent)` subclasses from `src/agentpool/agents/events/events.py`
- [x] 5.2 Audit all `event.session_id` access points and replace with `run_ctx.session_id` or `AgentContext.session_id` lookups
- [x] 5.3 Update `RunExecutor` to forward PydanticAI `PartStartEvent`/`PartDeltaEvent` as-is without wrapping
- [x] 5.4 Convert `ToolCallStartEvent` and `ToolCallCompleteEvent` from PydanticAI event subclasses to plain dataclass instances constructed by `RunExecutor`
- [x] 5.5 Update `EventBus` event routing to handle plain PydanticAI event types
- [x] 5.6 Update protocol server event consumers (`ProtocolEventConsumerMixin` implementations) to get `session_id` from context, not event payload
- [x] 5.7 Update `RichAgentStreamEvent` union type to include raw `AgentStreamEvent` instead of AgentPool subclasses
- [x] 5.8 Run event regression tests: `uv run pytest tests/agents/test_events.py -vv`
- [x] 5.9 Run full test suite: `uv run pytest -m unit`

## 6. Post-Migration: Cleanup & Verification

- [x] 6.1 Run `uv run ruff check src/` and fix all lint errors
- [x] 6.2 Run `uv run ruff format --check src/` and format if needed
- [x] 6.3 Run `uv run --no-group docs mypy src/` and fix all type errors
- [x] 6.4 Run full test suite with coverage: `uv run pytest --cov-report=term-missing`
- [x] 6.5 Verify no `pydantic_ai._internal` or `pydantic_ai._function_schema` imports remain in non-bridge code
- [x] 6.6 Update `AGENTS.md` documentation to reflect new architecture (Hooks delegation, ProcessHistory direct usage, event passthrough)
- [x] 6.7 Update CHANGELOG with breaking changes (Hook config schema, ToolKind removal, event subclass removal)
- [x] 6.8 Write migration guide for YAML config changes in `docs/migration/pydanticai-thinning.md`
- [x] 6.9 Run integration tests: `uv run pytest -m integration`
- [x] 6.10 Final full suite run: `uv run pytest`
