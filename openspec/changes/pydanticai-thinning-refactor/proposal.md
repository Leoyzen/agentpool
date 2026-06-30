## Why

AgentPool currently carries ~1,500 lines of custom code that duplicates functionality PydanticAI already provides (Hooks hierarchy, ProcessHistoryAdapter, PromptInjectionManager native path, Tool metadata中间层). This overlap increases maintenance burden, creates subtle behavioral divergences from upstream PydanticAI, and makes it harder to benefit from future PydanticAI improvements. The framework should be "thin" at the agent-engine layer (where PydanticAI is the source of truth) and "thick" only at the orchestration layer (where AgentPool provides unique value: sessions, multi-agent composition, protocol servers, skills).

## What Changes

- **Replace custom Hooks system with `pydantic_ai.capabilities.Hooks`**: Remove the 3-type Hook hierarchy (`CallableHook`/`CommandHook`/`PromptHook`), regex matchers, timeout handling, and parallel result combining. Migrate to PydanticAI's `Hooks` capability with typed callbacks (`before_run`/`after_run`/`before_tool_execute`/`after_tool_execute`). `CommandHook` and `PromptHook` become thin wrappers that internally delegate to `Hooks` callbacks. **BREAKING**: Hook config YAML schema changes (no more `matcher`/`event`/`timeout` fields; replaced by `before`/`after` callback references).

- **Replace `ProcessHistoryAdapter` with PydanticAI's `ProcessHistory` capability**: Remove the custom `ProcessHistoryAdapter` implementation (~200 lines with caching + signature validation). Use `pydantic_ai.capabilities.ProcessHistory` directly. Custom history processors (compaction, etc.) are registered as `ProcessHistory` callbacks instead.

- **Remove `PromptInjectionManager` for native agent path**: Native agents already use `PendingMessageDrainCapability` for follow-up queue (per `pending-message-queue` spec). The `inject()`/`consume()` tool-result augmentation path is preserved, but `queue()`/`pop_queued()`/`flush_pending_to_queue()` are removed for native agents. `PromptInjectionManager` survives only as the ACP-agent manual queue.

- **Simplify `Tool` dataclass to thin wrapper**: Remove `ToolKind` taxonomy, `ToolResult.structured_content`, and redundant metadata fields that PydanticAI's `Tool` already provides. `Tool.to_pydantic_ai()` becomes a direct 1:1 mapping instead of a 60-line conversion. Tool confirmation uses PydanticAI's `requires_approval` natively instead of the custom `ApprovalRequiredToolset` wrapper where possible.

- **Simplify `RunExecutor` event mapping**: PydanticAI's `AgentStreamEvent` already includes `PartStartEvent`/`PartDeltaEvent`/`FunctionToolCallEvent`. AgentPool's custom subclasses (`PartStartEvent`+`session_id`, `PartDeltaEvent`+`session_id`) are replaced by passing `session_id` via context, not by subclassing. `ToolCallStartEvent`/`ToolCallCompleteEvent` become thin wrappers over PydanticAI's `FunctionToolCallEvent`/`FunctionToolResultEvent`.

- **Keep unchanged**: `SessionPool`, `SessionController`, `EventBus`, `TurnRunner` (ACP path), `AgentPool` registry, Skills system, ResourceProvider hierarchy, ACP agent, protocol servers, YAML graph compiler, pydantic-graph integration.

## Capabilities

### New Capabilities

- `pydanticai-hooks-delegation`: AgentPool delegates hook lifecycle to PydanticAI's `Hooks` capability; custom hook types become thin adapters
- `pydanticai-process-history`: AgentPool uses PydanticAI's `ProcessHistory` capability directly for history processing
- `pydanticai-tool-thinning`: AgentPool `Tool` becomes a thin wrapper over `pydantic_ai.tools.Tool`, removing redundant metadata layers
- `pydanticai-event-passthrough`: AgentPool streaming events pass through PydanticAI's `AgentStreamEvent` types directly instead of subclassing

### Modified Capabilities

- `pending-message-queue`: Native agent path no longer uses `PromptInjectionManager.queue()`/`pop_queued()` for follow-up prompts — fully delegated to `PendingMessageDrainCapability`. ACP path unchanged.
- `agentnode-wrapper`: `AgentNode` uses PydanticAI's native event types directly instead of AgentPool-wrapped event subclasses.

## Impact

- **Code reduction**: ~1,000-1,500 lines removed (Hooks hierarchy ~400 lines, ProcessHistoryAdapter ~200 lines, PromptInjectionManager native path ~143 lines, Tool metadata simplification ~300 lines, event subclass removal ~200 lines)
- **Dependencies**: No new dependencies; reduces internal coupling to `pydantic_ai._internal` APIs
- **Breaking changes**: Hook YAML config schema changes; `ToolKind` enum removed; `PartStartEvent`/`PartDeltaEvent` no longer have `session_id` field (use context instead); `ToolResult.structured_content` removed (use PydanticAI's native structured return)
- **Affected files**: `src/agentpool/hooks/`, `src/agentpool/agents/native_agent/agent.py`, `src/agentpool/agents/native_agent/hook_manager.py`, `src/agentpool/agents/native_agent/process_history_capability.py`, `src/agentpool/agents/prompt_injection.py`, `src/agentpool/tools/base.py`, `src/agentpool/agents/events/events.py`, `src/agentpool/orchestrator/run_executor.py`, `src/agentpool/resource_providers/base.py`
- **Test impact**: Hook tests, tool tests, and event tests need updating. Behavioral parity must be verified for all existing scenarios.
