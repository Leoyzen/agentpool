# PydanticAI Thinning Refactor — Migration Guide

This guide covers the deprecations introduced by the PydanticAI thinning refactor and how to migrate existing code.

## Overview

The refactor makes AgentPool "thinner" at the agent-engine layer by delegating to PydanticAI's native capabilities. Old APIs remain functional with `DeprecationWarning` — no immediate breakage.

## 1. Hooks: `AgentHooks.as_capability()` → `HooksCapabilityAdapter`

**Before:**
```python
from agentpool.hooks import AgentHooks, CallableHook

hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=my_hook)])
capability = hooks.as_capability()  # DeprecationWarning
```

**After:**
```python
from agentpool.agents.native_agent.hooks_capability_adapter import HooksCapabilityAdapter

# Option A: From existing AgentHooks (transparent migration)
adapter = HooksCapabilityAdapter.from_agent_hooks(hooks)
capability = adapter.build()

# Option B: Direct construction (preferred for new code)
adapter = HooksCapabilityAdapter(
    before_run=[my_hook],
)
capability = adapter.build()
```

**YAML config**: No changes needed. `CallableHook`, `CommandHook`, `PromptHook` remain functional — the adapter extracts their `fn`, `matcher`, and `input_match` transparently.

## 2. ProcessHistoryAdapter

**Status**: `get_agentlet()` already uses `pydantic_ai.capabilities.ProcessHistory` directly (line 869 of `agent.py`). `ProcessHistoryAdapter` is deprecated but remains for manual usage.

**Before:**
```python
from agentpool.agents.native_agent.process_history_capability import ProcessHistoryAdapter
caps = ProcessHistoryAdapter.from_processors(my_processors)
```

**After:**
```python
from pydantic_ai.capabilities import ProcessHistory
caps = [ProcessHistory(p) for p in my_processors]
```

## 3. PromptInjectionManager

**No changes needed.** The native agent path already uses `PendingMessageDrainCapability` for follow-up queue delivery. `inject()`/`consume()` remains for tool result augmentation. `queue()`/`pop_queued()`/`flush_pending_to_queue()` remains for ACP agents only.

## 4. ToolKind

**Status**: Deprecated. Use string-based tool name patterns for config validation.

**Before:**
```python
from agentpool.tools.base import ToolKind
tool = FunctionTool(name="bash", description="d", callable=fn, category="execute")
```

**After:**
```python
tool = FunctionTool(name="bash", description="d", callable=fn)
# Use tool name patterns for validation: allowed_tools: ["bash", "read"]
```

## 5. ToolResult.structured_content

**Status**: Deprecated. Use PydanticAI's `ToolReturn` natively.

**Before:**
```python
result = ToolResult(content="summary", structured_content={"key": "value"})
```

**After:**
```python
from pydantic_ai.messages import ToolReturn
return ToolReturn(content="summary", structured_content={"key": "value"})
```

## 6. Event Subclasses (PartStartEvent/PartDeltaEvent)

**Status**: Deprecated. `session_id` should be accessed via `AgentContext` or `RunContext.deps`.

**Before:**
```python
event = PartStartEvent(index=0, part=TextPart(content="hi"))
session_id = event.session_id
```

**After:**
```python
# Use PydanticAI's event directly
from pydantic_ai import PartStartEvent
event = PartStartEvent(index=0, part=TextPart(content="hi"))
# Get session_id from context, not from event payload
session_id = run_ctx.session_id
```

## Timeline

- **Current**: Deprecation warnings emitted, all old APIs functional
- **v0.5.0**: Deprecated APIs removed
