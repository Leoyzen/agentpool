# Hook Audit: Hooks vs Capabilities

## Purpose

Audit existing hooks (`pre_run`, `post_run`, `pre_tool_use`, `post_tool_use`) for overlap with pydantic-ai Capability hooks (`wrap_node_run`, `before_model_request`, `after_node_run`, `wrap_tool_execute`, `before_tool_execute`, `after_tool_execute`).

## Audit Results

| Hook | Capability Equivalent | Decision | Rationale |
|------|----------------------|----------|-----------|
| `pre_run` | `wrap_node_run` | **Keep** | `pre_run` fires before the entire agent run starts (once per run). `wrap_node_run` fires before each node iteration (potentially many times per run). Different semantics: `pre_run` is for run-level setup (session init, context propagation), not per-node interception. |
| `post_run` | `after_node_run` | **Keep** | `post_run` fires after the entire agent run completes (once per run). `after_node_run` fires after each node. Different semantics: `post_run` is for run-level cleanup (logging, storage), not per-node interception. |
| `pre_tool_use` | `before_tool_execute` / `wrap_tool_execute` | **Migrate** | `pre_tool_use` is fully replaced by `_ToolInterceptCapability` which owns all tool interception (confirmation, approval, blocking). Already migrated in PR #106. The hook system still dispatches to `_ToolInterceptCapability` internally. |
| `post_tool_use` | `after_tool_execute` / `wrap_tool_execute` | **Migrate** | `post_tool_use` is fully replaced by `_ToolInterceptCapability`'s `wrap_tool_execute` which runs after tool execution for result processing. Already migrated in PR #106. |

## Summary

- **2 hooks kept** (`pre_run`, `post_run`): Run-level lifecycle hooks with no direct Capability equivalent. These fire once per agent run, not per-node or per-tool.
- **2 hooks migrated** (`pre_tool_use`, `post_tool_use`): Already replaced by `_ToolInterceptCapability` (PR #106). The hook dispatch system still routes to these hooks, but the implementation is now Capability-based.

## Migration Status

| Hook | Status | Implementation |
|------|--------|----------------|
| `pre_run` | Keep | `hooks/base.py` — run-level setup |
| `post_run` | Keep | `hooks/base.py` — run-level cleanup |
| `pre_tool_use` | Migrated | `_ToolInterceptCapability` in `agents/native_agent/hook_manager.py` |
| `post_tool_use` | Migrated | `_ToolInterceptCapability` in `agents/native_agent/hook_manager.py` |

## Notes

- `wrap_node_run` is used by `LoopDetectionCapability` and `MemoryCapability` for per-node interception.
- `before_model_request` is used by `TokenBudgetCapability`, `DynamicContextCapability`, `MemoryCapability`, and `SkillActivationCapability`.
- `after_node_run` is used by `MemoryCapability` for persistence.
- `wrap_tool_execute` is used by `ToolOutputBudgetCapability` for output truncation.
- The hook system (`hooks/agent_hooks.py`) is deprecated but still functional for backward compatibility. New code should use Capabilities.
