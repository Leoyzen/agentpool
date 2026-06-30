"""Regression tests for hook combination scenarios.

These tests verify the deny > ask > allow priority semantics in
``AgentHooks._run_hooks`` — the core aggregation algorithm that
combines results from multiple parallel hooks.

They serve as a **behavioral baseline** before the migration to
``pydantic_ai.capabilities.Hooks`` so we can prove parity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from agentpool.hooks import AgentHooks, CallableHook


if TYPE_CHECKING:
    from agentpool.hooks import HookResult


# ---------------------------------------------------------------------------
# Helper hooks
# ---------------------------------------------------------------------------

hook_calls: list[tuple[str, dict[str, Any]]] = []


def _reset() -> None:
    hook_calls.clear()


def _allow(**kw: Any) -> HookResult:
    hook_calls.append(("allow", dict(kw)))
    return {"decision": "allow"}


def _deny(**kw: Any) -> HookResult:
    hook_calls.append(("deny", dict(kw)))
    return {"decision": "deny", "reason": "no"}


def _ask(**kw: Any) -> HookResult:
    hook_calls.append(("ask", dict(kw)))
    return {"decision": "ask", "reason": "not sure"}


def _modify_input(**kw: Any) -> HookResult:
    hook_calls.append(("modify", dict(kw)))
    return {"decision": "allow", "modified_input": {"key": "val1"}}


def _modify_input_override(**kw: Any) -> HookResult:
    hook_calls.append(("modify_override", dict(kw)))
    return {"decision": "allow", "modified_input": {"key": "val2", "extra": True}}


def _additional_context(**kw: Any) -> HookResult:
    hook_calls.append(("context", dict(kw)))
    return {"decision": "allow", "additional_context": "ctx-A"}


def _additional_context_b(**kw: Any) -> HookResult:
    hook_calls.append(("context_b", dict(kw)))
    return {"decision": "allow", "additional_context": "ctx-B"}


def _modified_output(**kw: Any) -> HookResult:
    hook_calls.append(("output", dict(kw)))
    return {"decision": "allow", "modified_output": "REPLACED"}


def _continue_false(**kw: Any) -> HookResult:
    hook_calls.append(("continue_false", dict(kw)))
    return {"decision": "allow", "continue_": False}


def _raise_hook(**kw: Any) -> HookResult:
    hook_calls.append(("raise", dict(kw)))
    msg = "boom"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Priority: deny > ask > allow
# ---------------------------------------------------------------------------


async def test_priority_deny_over_allow():
    """A deny result from ANY hook overrides all allows."""
    _reset()
    hooks = AgentHooks(
        pre_run=[
            CallableHook(event="pre_run", fn=_allow),
            CallableHook(event="pre_run", fn=_deny),
            CallableHook(event="pre_run", fn=_allow),
        ],
    )
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert result["decision"] == "deny"
    assert "no" in result.get("reason", "")
    assert len(hook_calls) == 3  # all hooks executed


async def test_priority_deny_over_ask():
    """Deny overrides ask."""
    _reset()
    hooks = AgentHooks(
        pre_run=[
            CallableHook(event="pre_run", fn=_ask),
            CallableHook(event="pre_run", fn=_deny),
        ],
    )
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert result["decision"] == "deny"


async def test_priority_ask_over_allow():
    """Ask takes effect only when no deny."""
    _reset()
    hooks = AgentHooks(
        pre_run=[
            CallableHook(event="pre_run", fn=_allow),
            CallableHook(event="pre_run", fn=_ask),
        ],
    )
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert result["decision"] == "ask"


async def test_all_allow():
    """All allow → combined allow."""
    _reset()
    hooks = AgentHooks(
        pre_run=[
            CallableHook(event="pre_run", fn=_allow),
            CallableHook(event="pre_run", fn=_allow),
        ],
    )
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert result["decision"] == "allow"


async def test_all_ask():
    """All ask → combined ask."""
    _reset()
    hooks = AgentHooks(
        pre_run=[
            CallableHook(event="pre_run", fn=_ask),
            CallableHook(event="pre_run", fn=_ask),
        ],
    )
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert result["decision"] == "ask"


# ---------------------------------------------------------------------------
# Reason concatenation
# ---------------------------------------------------------------------------


async def test_reasons_concatenated_with_semicolon():
    """Multiple reasons are joined with '; '."""
    _reset()
    hooks = AgentHooks(
        pre_run=[
            CallableHook(event="pre_run", fn=_deny),
            CallableHook(event="pre_run", fn=_ask),
        ],
    )
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert "no" in result["reason"]
    assert "not sure" in result["reason"]
    assert "; " in result["reason"]


# ---------------------------------------------------------------------------
# modified_input merging
# ---------------------------------------------------------------------------


async def test_modified_input_merges_across_hooks():
    """modified_input from multiple hooks merges via dict.update (later wins)."""
    _reset()
    hooks = AgentHooks(
        pre_tool_use=[
            CallableHook(event="pre_tool_use", fn=_modify_input),
            CallableHook(event="pre_tool_use", fn=_modify_input_override),
        ],
    )
    result = await hooks.run_pre_tool_hooks(
        agent_name="a", tool_name="t", tool_input={"orig": 1}
    )
    modified = result.get("modified_input")
    assert modified is not None
    assert modified["key"] == "val2"  # second hook overrides
    assert modified["extra"] is True  # from second hook
    assert modified.get("orig") is None  # original tool_input not included


# ---------------------------------------------------------------------------
# modified_output — full replacement, later wins
# ---------------------------------------------------------------------------


async def test_modified_output_replacement():
    """modified_output is a full replacement; later hook wins."""
    _reset()

    def output_a(**kw: Any) -> HookResult:
        return {"decision": "allow", "modified_output": "A"}

    def output_b(**kw: Any) -> HookResult:
        return {"decision": "allow", "modified_output": "B"}

    hooks = AgentHooks(
        post_tool_use=[
            CallableHook(event="post_tool_use", fn=output_a),
            CallableHook(event="post_tool_use", fn=output_b),
        ],
    )
    result = await hooks.run_post_tool_hooks(
        agent_name="a",
        tool_name="t",
        tool_input={},
        tool_output="original",
        duration_ms=1.0,
    )
    assert result.get("modified_output") == "B"


# ---------------------------------------------------------------------------
# additional_context concatenation
# ---------------------------------------------------------------------------


async def test_additional_context_concatenated_with_newline():
    """additional_context from multiple hooks joined with '\\n'."""
    _reset()
    hooks = AgentHooks(
        post_tool_use=[
            CallableHook(event="post_tool_use", fn=_additional_context),
            CallableHook(event="post_tool_use", fn=_additional_context_b),
        ],
    )
    result = await hooks.run_post_tool_hooks(
        agent_name="a",
        tool_name="t",
        tool_input={},
        tool_output="x",
        duration_ms=0.0,
    )
    ctx = result.get("additional_context", "")
    assert "ctx-A" in ctx
    assert "ctx-B" in ctx
    assert "\n" in ctx


# ---------------------------------------------------------------------------
# continue_ aggregation
# ---------------------------------------------------------------------------


async def test_continue_false_from_any_hook():
    """If any hook sets continue_=False, combined result is False."""
    _reset()
    hooks = AgentHooks(
        post_tool_use=[
            CallableHook(event="post_tool_use", fn=_allow),
            CallableHook(event="post_tool_use", fn=_continue_false),
        ],
    )
    result = await hooks.run_post_tool_hooks(
        agent_name="a",
        tool_name="t",
        tool_input={},
        tool_output="x",
        duration_ms=0.0,
    )
    assert result.get("continue_") is False


async def test_continue_true_when_all_allow():
    """No hook sets continue_=False → not present in result."""
    _reset()
    hooks = AgentHooks(
        post_tool_use=[
            CallableHook(event="post_tool_use", fn=_allow),
        ],
    )
    result = await hooks.run_post_tool_hooks(
        agent_name="a",
        tool_name="t",
        tool_input={},
        tool_output="x",
        duration_ms=0.0,
    )
    assert "continue_" not in result or result.get("continue_") is not False


# ---------------------------------------------------------------------------
# Exception resilience
# ---------------------------------------------------------------------------


async def test_exception_in_one_hook_does_not_block():
    """A hook that raises is logged and skipped, others still run."""
    _reset()
    hooks = AgentHooks(
        pre_run=[
            CallableHook(event="pre_run", fn=_allow),
            CallableHook(event="pre_run", fn=_raise_hook),
            CallableHook(event="pre_run", fn=_allow),
        ],
    )
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert result["decision"] == "allow"  # raising hook skipped
    assert len(hook_calls) == 3  # all three attempted


async def test_exception_with_deny_from_another():
    """Exception in one hook + deny from another → deny still wins."""
    _reset()
    hooks = AgentHooks(
        pre_run=[
            CallableHook(event="pre_run", fn=_raise_hook),
            CallableHook(event="pre_run", fn=_deny),
        ],
    )
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert result["decision"] == "deny"


# ---------------------------------------------------------------------------
# Empty / no-match scenarios
# ---------------------------------------------------------------------------


async def test_empty_hooks_returns_allow():
    """No hooks configured → allow."""
    _reset()
    hooks = AgentHooks()
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert result["decision"] == "allow"


async def test_no_matching_hooks_returns_allow():
    """All hooks filtered out by matcher → allow."""
    _reset()
    hooks = AgentHooks(
        pre_tool_use=[
            CallableHook(event="pre_tool_use", fn=_deny, matcher="other_tool"),
        ],
    )
    result = await hooks.run_pre_tool_hooks(
        agent_name="a", tool_name="my_tool", tool_input={}
    )
    assert result["decision"] == "allow"


# ---------------------------------------------------------------------------
# CallableHook return type normalization
# ---------------------------------------------------------------------------


async def test_callable_hook_returns_none_defaults_to_allow():
    """CallableHook returning None → allow."""
    _reset()

    def returns_none(**kw: Any) -> None:
        hook_calls.append(("none", dict(kw)))

    hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=returns_none)])
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert result["decision"] == "allow"


async def test_callable_hook_returns_string_becomes_additional_context():
    """CallableHook returning a string → allow + additional_context."""

    def returns_str(**kw: Any) -> str:
        return "injected text"

    hooks = AgentHooks(post_tool_use=[CallableHook(event="post_tool_use", fn=returns_str)])
    result = await hooks.run_post_tool_hooks(
        agent_name="a",
        tool_name="t",
        tool_input={},
        tool_output="x",
        duration_ms=0.0,
    )
    assert result["decision"] == "allow"
    assert result.get("additional_context") == "injected text"


async def test_callable_hook_returns_bool_true():
    """CallableHook returning True → allow."""

    def returns_true(**kw: Any) -> bool:
        return True

    hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=returns_true)])
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert result["decision"] == "allow"


async def test_callable_hook_returns_bool_false():
    """CallableHook returning False → deny."""

    def returns_false(**kw: Any) -> bool:
        return False

    hooks = AgentHooks(pre_run=[CallableHook(event="pre_run", fn=returns_false)])
    result = await hooks.run_pre_run_hooks(agent_name="a", prompt="hi")
    assert result["decision"] == "deny"


# ---------------------------------------------------------------------------
# Matcher semantics
# ---------------------------------------------------------------------------


def test_matcher_star_matches_all():
    """matcher='*' compiles to None (matches all)."""
    hook = CallableHook(event="pre_tool_use", fn=_allow, matcher="*")
    assert hook._pattern is None
    assert hook.matches({"event": "pre_tool_use", "tool_name": "anything"})


def test_matcher_none_matches_all():
    """matcher=None compiles to None (matches all)."""
    hook = CallableHook(event="pre_tool_use", fn=_allow, matcher=None)
    assert hook._pattern is None
    assert hook.matches({"event": "pre_tool_use", "tool_name": "anything"})


def test_matcher_uses_search_not_fullmatch():
    """Matcher uses re.search (substring match)."""
    hook = CallableHook(event="pre_tool_use", fn=_allow, matcher="bash")
    assert hook.matches({"event": "pre_tool_use", "tool_name": "run_bash_command"})
    assert hook.matches({"event": "pre_tool_use", "tool_name": "bash"})
    assert not hook.matches({"event": "pre_tool_use", "tool_name": "python"})


def test_matcher_ignored_for_non_tool_events():
    """Matcher only applies to pre_tool_use/post_tool_use events."""
    hook = CallableHook(event="pre_run", fn=_allow, matcher="bash")
    # For pre_run, matcher is not checked — should match regardless
    assert hook.matches({"event": "pre_run", "prompt": "hello"})


def test_disabled_hook_does_not_match():
    """Disabled hook never matches."""
    hook = CallableHook(event="pre_run", fn=_allow, enabled=False)
    assert not hook.matches({"event": "pre_run", "prompt": "hi"})


# ---------------------------------------------------------------------------
# input_match semantics
# ---------------------------------------------------------------------------


def test_input_match_all_must_match():
    """All input_match patterns must match for the hook to trigger."""
    hook = CallableHook(
        event="pre_tool_use",
        fn=_allow,
        matcher="task",
        input_match={"mode": "^libarian$", "tag": "^plan$"},
    )
    assert hook.matches(
        {
            "event": "pre_tool_use",
            "tool_name": "task",
            "tool_input": {"mode": "libarian", "tag": "plan"},
        }
    )
    assert not hook.matches(
        {
            "event": "pre_tool_use",
            "tool_name": "task",
            "tool_input": {"mode": "other", "tag": "plan"},
        }
    )


def test_input_match_missing_field_rejects():
    """Missing field in tool_input → no match."""
    hook = CallableHook(
        event="pre_tool_use",
        fn=_allow,
        matcher="task",
        input_match={"tag": "^plan$"},
    )
    assert not hook.matches(
        {"event": "pre_tool_use", "tool_name": "task", "tool_input": {}}
    )


def test_input_match_no_tool_input_treated_as_empty():
    """Missing tool_input entirely → input_match sees empty dict → no match."""
    hook = CallableHook(
        event="pre_tool_use",
        fn=_allow,
        matcher="task",
        input_match={"tag": "^plan$"},
    )
    # tool_input key absent from HookInput
    assert not hook.matches({"event": "pre_tool_use", "tool_name": "task"})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
