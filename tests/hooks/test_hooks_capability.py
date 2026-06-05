"""Tests for AgentHooks.as_capability() mapping to pydantic-ai Hooks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.run import AgentRunResult
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_ai.usage import RunUsage

from agentpool.hooks import AgentHooks, CallableHook
from agentpool.hooks.base import HookResult


if TYPE_CHECKING:
    from collections.abc import Sequence


# Simple mock deps with node_name and run_ctx
class MockDeps:
    """Mock deps for RunContext."""

    def __init__(self, node_name: str = "test_agent", session_id: str | None = None):
        self.node_name = node_name
        self.run_ctx = MockRunCtx(session_id) if session_id else None


class MockRunCtx:
    """Mock run context with session_id."""

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id


def make_run_context(deps: Any = ...) -> RunContext[Any]:
    """Create a RunContext with mock deps."""
    actual_deps = MockDeps() if deps is ... else deps
    return RunContext(
        deps=actual_deps,
        model=TestModel(),
        usage=RunUsage(),
    )


# Hook tracking state
hook_calls: list[tuple[str, Any]] = []


def reset_hook_state():
    """Reset hook tracking state."""
    hook_calls.clear()


def allow_hook(**kwargs) -> HookResult:
    """Hook that allows the action."""
    hook_calls.append(("allow", kwargs.get("event")))
    return {"decision": "allow"}


def deny_hook(**kwargs) -> HookResult:
    """Hook that denies the action."""
    hook_calls.append(("deny", kwargs.get("event")))
    return {"decision": "deny", "reason": "Denied by test hook"}


def record_hook(**kwargs) -> HookResult:
    """Hook that records all input data."""
    hook_calls.append(("record", dict(kwargs)))
    return {"decision": "allow"}


def modify_input_hook(**kwargs) -> HookResult:
    """Hook that modifies tool input."""
    hook_calls.append(("modify", kwargs.get("tool_input")))
    return {"decision": "allow", "modified_input": {"modified": True}}


# Tests for as_capability basics


def test_as_capability_returns_hooks_instance():
    """Test that as_capability returns a pydantic-ai Hooks instance."""
    hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=allow_hook)])
    capability = hooks.as_capability()
    assert isinstance(capability, Hooks)


def test_empty_hooks_returns_empty_hooks():
    """Test that empty AgentHooks returns empty Hooks."""
    hooks = AgentHooks()
    capability = hooks.as_capability()
    assert isinstance(capability, Hooks)
    assert capability._registry == {}


def test_has_hooks_with_capability():
    """Test has_hooks is True when hooks configured."""
    hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=allow_hook)])
    assert hooks.has_hooks()
    capability = hooks.as_capability()
    assert "before_run" in capability._registry


# Tests for before_run / pre_run mapping


async def test_before_run_adapter_calls_pre_run_hooks():
    """Test before_run adapter invokes pre_run hooks."""
    reset_hook_state()

    agent_hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=record_hook)])
    capability = agent_hooks.as_capability()
    ctx = make_run_context()

    await capability.before_run(ctx)

    assert len(hook_calls) == 1
    event_type, data = hook_calls[0]
    assert event_type == "record"
    assert data["event"] == "pre_run"


async def test_before_run_adapter_with_session_id():
    """Test before_run adapter passes session_id from deps."""
    reset_hook_state()

    agent_hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=record_hook)])
    capability = agent_hooks.as_capability()
    ctx = make_run_context(deps=MockDeps(session_id="sess-123"))

    await capability.before_run(ctx)

    assert len(hook_calls) == 1
    _event_type, data = hook_calls[0]
    assert data["session_id"] == "sess-123"


async def test_before_run_adapter_deny_raises():
    """Test before_run adapter raises RuntimeError on deny."""
    reset_hook_state()

    agent_hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=deny_hook)])
    capability = agent_hooks.as_capability()
    ctx = make_run_context()

    with pytest.raises(RuntimeError, match="Run blocked"):
        await capability.before_run(ctx)

    assert len(hook_calls) == 1
    assert hook_calls[0][0] == "deny"


async def test_before_run_adapter_no_hooks():
    """Test that AgentHooks without pre_run doesn't register before_run."""
    agent_hooks = AgentHooks(post_run=[CallableHook(event="post_run", fn=allow_hook)])
    capability = agent_hooks.as_capability()
    assert "before_run" not in capability._registry


# Tests for after_run / post_run mapping


async def test_after_run_adapter_calls_post_run_hooks():
    """Test after_run adapter invokes post_run hooks."""
    reset_hook_state()

    agent_hooks = AgentHooks(post_run=[CallableHook(event="post_run", fn=record_hook)])
    capability = agent_hooks.as_capability()
    ctx = make_run_context()
    result = AgentRunResult(output="test-output")

    returned = await capability.after_run(ctx, result=result)

    assert returned is result
    assert len(hook_calls) == 1
    event_type, data = hook_calls[0]
    assert event_type == "record"
    assert data["event"] == "post_run"
    assert data["result"] is result


async def test_after_run_adapter_passes_agent_name():
    """Test after_run adapter passes agent_name from deps."""
    reset_hook_state()

    agent_hooks = AgentHooks(post_run=[CallableHook(event="post_run", fn=record_hook)])
    capability = agent_hooks.as_capability()
    ctx = make_run_context(deps=MockDeps(node_name="my-agent"))
    result = AgentRunResult(output="test")

    await capability.after_run(ctx, result=result)

    _event_type, data = hook_calls[0]
    assert data["agent_name"] == "my-agent"


# Tests for before_tool_execute / pre_tool_use mapping


async def test_before_tool_execute_adapter_calls_pre_tool_hooks():
    """Test before_tool_execute adapter invokes pre_tool_use hooks."""
    reset_hook_state()

    agent_hooks = AgentHooks(
        pre_tool_use=[CallableHook(event="pre_tool_use", fn=record_hook)]
    )
    capability = agent_hooks.as_capability()
    ctx = make_run_context()
    call = ToolCallPart(tool_name="test_tool", args={"x": 1})
    tool_def = ToolDefinition(name="test_tool")
    args = {"x": 1}

    returned = await capability.before_tool_execute(
        ctx, call=call, tool_def=tool_def, args=args
    )

    assert returned == args
    assert len(hook_calls) == 1
    event_type, data = hook_calls[0]
    assert event_type == "record"
    assert data["event"] == "pre_tool_use"
    assert data["tool_name"] == "test_tool"
    assert data["tool_input"] == {"x": 1}


async def test_before_tool_execute_adapter_deny_raises():
    """Test before_tool_execute adapter raises RuntimeError on deny."""
    reset_hook_state()

    agent_hooks = AgentHooks(
        pre_tool_use=[CallableHook(event="pre_tool_use", fn=deny_hook)]
    )
    capability = agent_hooks.as_capability()
    ctx = make_run_context()
    call = ToolCallPart(tool_name="test_tool", args={"x": 1})
    tool_def = ToolDefinition(name="test_tool")
    args = {"x": 1}

    with pytest.raises(RuntimeError, match="Tool execution blocked"):
        await capability.before_tool_execute(
            ctx, call=call, tool_def=tool_def, args=args
        )


async def test_before_tool_execute_adapter_modified_input():
    """Test before_tool_execute adapter merges modified_input into args."""
    reset_hook_state()

    agent_hooks = AgentHooks(
        pre_tool_use=[CallableHook(event="pre_tool_use", fn=modify_input_hook)]
    )
    capability = agent_hooks.as_capability()
    ctx = make_run_context()
    call = ToolCallPart(tool_name="test_tool", args={"x": 1})
    tool_def = ToolDefinition(name="test_tool")
    args = {"x": 1}

    returned = await capability.before_tool_execute(
        ctx, call=call, tool_def=tool_def, args=args
    )

    assert returned == {"x": 1, "modified": True}


async def test_before_tool_execute_adapter_no_hooks():
    """Test that AgentHooks without pre_tool_use doesn't register before_tool_execute."""
    agent_hooks = AgentHooks(post_tool_use=[CallableHook(event="post_tool_use", fn=allow_hook)])
    capability = agent_hooks.as_capability()
    assert "before_tool_execute" not in capability._registry


# Tests for after_tool_execute / post_tool_use mapping


async def test_after_tool_execute_adapter_calls_post_tool_hooks():
    """Test after_tool_execute adapter invokes post_tool_use hooks."""
    reset_hook_state()

    agent_hooks = AgentHooks(
        post_tool_use=[CallableHook(event="post_tool_use", fn=record_hook)]
    )
    capability = agent_hooks.as_capability()
    ctx = make_run_context()
    call = ToolCallPart(tool_name="test_tool", args={"x": 1})
    tool_def = ToolDefinition(name="test_tool")
    args = {"x": 1}
    result = "tool-output"

    returned = await capability.after_tool_execute(
        ctx, call=call, tool_def=tool_def, args=args, result=result
    )

    assert returned == result
    assert len(hook_calls) == 1
    event_type, data = hook_calls[0]
    assert event_type == "record"
    assert data["event"] == "post_tool_use"
    assert data["tool_name"] == "test_tool"
    assert data["tool_output"] == "tool-output"
    assert data["duration_ms"] == 0.0


async def test_after_tool_execute_adapter_passes_session_id():
    """Test after_tool_execute adapter passes session_id from deps."""
    reset_hook_state()

    agent_hooks = AgentHooks(
        post_tool_use=[CallableHook(event="post_tool_use", fn=record_hook)]
    )
    capability = agent_hooks.as_capability()
    ctx = make_run_context(deps=MockDeps(session_id="sess-456"))
    call = ToolCallPart(tool_name="test_tool", args={"x": 1})
    tool_def = ToolDefinition(name="test_tool")
    args = {"x": 1}

    await capability.after_tool_execute(
        ctx, call=call, tool_def=tool_def, args=args, result="out"
    )

    _event_type, data = hook_calls[0]
    assert data["session_id"] == "sess-456"


# Tests for combined hooks


async def test_all_hook_types_combined():
    """Test that all four hook types are registered together."""
    reset_hook_state()

    agent_hooks = AgentHooks(
        pre_run=[CallableHook(event="pre_run", fn=allow_hook)],
        post_run=[CallableHook(event="post_run", fn=allow_hook)],
        pre_tool_use=[CallableHook(event="pre_tool_use", fn=allow_hook)],
        post_tool_use=[CallableHook(event="post_tool_use", fn=allow_hook)],
    )
    capability = agent_hooks.as_capability()

    assert "before_run" in capability._registry
    assert "after_run" in capability._registry
    assert "before_tool_execute" in capability._registry
    assert "after_tool_execute" in capability._registry

    ctx = make_run_context()
    await capability.before_run(ctx)

    result = AgentRunResult(output="test")
    await capability.after_run(ctx, result=result)

    call = ToolCallPart(tool_name="t", args={})
    tool_def = ToolDefinition(name="t")
    await capability.before_tool_execute(ctx, call=call, tool_def=tool_def, args={})
    await capability.after_tool_execute(
        ctx, call=call, tool_def=tool_def, args={}, result="r"
    )

    assert len(hook_calls) == 4


async def test_multiple_hooks_same_event():
    """Test multiple hooks for the same event are all invoked."""
    reset_hook_state()

    agent_hooks = AgentHooks(
        pre_run=[
            CallableHook(event="pre_run", fn=allow_hook),
            CallableHook(event="pre_run", fn=allow_hook),
        ]
    )
    capability = agent_hooks.as_capability()
    ctx = make_run_context()

    await capability.before_run(ctx)

    assert len(hook_calls) == 2
    assert hook_calls[0][0] == "allow"
    assert hook_calls[1][0] == "allow"


async def test_missing_deps_defaults():
    """Test adapter handles missing deps gracefully."""
    reset_hook_state()

    agent_hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=record_hook)])
    capability = agent_hooks.as_capability()
    ctx = make_run_context(deps=None)

    await capability.before_run(ctx)

    assert len(hook_calls) == 1
    _event_type, data = hook_calls[0]
    assert data["agent_name"] == ""
    assert data["session_id"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
