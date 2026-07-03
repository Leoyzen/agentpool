"""Tests for pdai Capability implementations (Phase 6)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentpool.capabilities.dynamic_context import DynamicContextCapability
from agentpool.capabilities.loop_detection import (
    LoopDetectionCapability,
    LoopDetectionError,
)
from agentpool.capabilities.memory import MemoryCapability, _inject_into_system_prompt
from agentpool.capabilities.skill_activation import SkillActivationCapability
from agentpool.capabilities.token_budget import (
    TokenBudgetCapability,
    TokenBudgetExceededError,
)
from agentpool.capabilities.tool_output_budget import ToolOutputBudgetCapability


def _make_request_context(messages: list) -> MagicMock:
    """Create a mock ModelRequestContext with messages."""
    rc = MagicMock()
    rc.messages = messages
    return rc


# =============================================================================
# LoopDetectionCapability
# =============================================================================


class TestLoopDetection:
    """Test loop detection capability."""

    def test_init_default(self) -> None:
        cap = LoopDetectionCapability()
        assert cap.max_depth == 10

    def test_init_custom_depth(self) -> None:
        cap = LoopDetectionCapability(max_depth=5)
        assert cap.max_depth == 5

    def test_init_invalid_depth_raises(self) -> None:
        with pytest.raises(ValueError, match="max_depth"):
            LoopDetectionCapability(max_depth=0)

    def test_has_wrap_node_run(self) -> None:
        cap = LoopDetectionCapability()
        assert cap.has_wrap_node_run is True

    async def test_for_run_creates_fresh_copy(self) -> None:
        cap = LoopDetectionCapability(max_depth=7)
        fresh = await cap.for_run(None)  # type: ignore[arg-type]
        assert fresh.max_depth == 7

    def test_loop_detection_error_message(self) -> None:
        err = LoopDetectionError(depth=15, max_depth=10)
        assert "15" in str(err)
        assert "10" in str(err)

    async def test_wrap_node_run_calls_handler(self) -> None:
        """Verify wrap_node_run calls handler and resets depth on exit."""
        cap = LoopDetectionCapability(max_depth=10)

        async def handler(node):  # type: ignore[no-untyped-def]
            return "result"  # type: ignore[return-value]

        result = await cap.wrap_node_run(
            ctx=None,  # type: ignore[arg-type]
            node=None,  # type: ignore[arg-type]
            handler=handler,
        )
        assert result == "result"

    async def test_wrap_node_run_raises_at_max_depth(self) -> None:
        """Verify LoopDetectionError is raised when depth exceeds max."""
        from agentpool.capabilities.loop_detection import _delegation_depth

        # Set the contextvar near the limit
        token = _delegation_depth.set(10)
        try:
            cap = LoopDetectionCapability(max_depth=10)

            async def handler(node):  # type: ignore[no-untyped-def]
                return "should not reach"  # type: ignore[return-value]

            with pytest.raises(LoopDetectionError) as exc_info:
                await cap.wrap_node_run(
                    ctx=None,  # type: ignore[arg-type]
                    node=None,  # type: ignore[arg-type]
                    handler=handler,
                )
            assert exc_info.value.depth == 11
            assert exc_info.value.max_depth == 10
        finally:
            _delegation_depth.reset(token)


# =============================================================================
# TokenBudgetCapability
# =============================================================================


class TestTokenBudget:
    """Test token budget capability."""

    def test_init_default(self) -> None:
        cap = TokenBudgetCapability()
        assert cap.max_tokens == 100_000
        assert cap._used_tokens == 0

    def test_init_custom_budget(self) -> None:
        cap = TokenBudgetCapability(max_tokens=5000)
        assert cap.max_tokens == 5000

    def test_init_invalid_budget_raises(self) -> None:
        with pytest.raises(ValueError, match="max_tokens"):
            TokenBudgetCapability(max_tokens=0)

    def test_has_wrap_node_run_false(self) -> None:
        cap = TokenBudgetCapability()
        assert cap.has_wrap_node_run is False

    async def test_for_run_creates_fresh_copy(self) -> None:
        cap = TokenBudgetCapability(max_tokens=5000)
        cap._used_tokens = 3000
        fresh = await cap.for_run(None)  # type: ignore[arg-type]
        assert fresh.max_tokens == 5000
        assert fresh._used_tokens == 0

    def test_budget_exceeded_error_message(self) -> None:
        err = TokenBudgetExceededError(used=15000, budget=10000)
        assert "15000" in str(err)
        assert "10000" in str(err)


# =============================================================================
# ToolOutputBudgetCapability
# =============================================================================


class TestToolOutputBudget:
    """Test tool output budget capability."""

    def test_init_default(self) -> None:
        cap = ToolOutputBudgetCapability()
        assert cap.max_output_chars == 10_000

    def test_init_custom_budget(self) -> None:
        cap = ToolOutputBudgetCapability(max_output_chars=500)
        assert cap.max_output_chars == 500

    def test_init_invalid_budget_raises(self) -> None:
        with pytest.raises(ValueError, match="max_output_chars"):
            ToolOutputBudgetCapability(max_output_chars=10)

    def test_has_wrap_node_run_false(self) -> None:
        cap = ToolOutputBudgetCapability()
        assert cap.has_wrap_node_run is False

    async def test_for_run_creates_fresh_copy(self) -> None:
        cap = ToolOutputBudgetCapability(max_output_chars=500)
        fresh = await cap.for_run(None)  # type: ignore[arg-type]
        assert fresh.max_output_chars == 500

    def test_truncate_short_string_unchanged(self) -> None:
        cap = ToolOutputBudgetCapability(max_output_chars=500)
        result = cap._truncate("short text")
        assert result == "short text"

    def test_truncate_long_string_cut(self) -> None:
        cap = ToolOutputBudgetCapability(max_output_chars=100)
        long_text = "x" * 200
        result = cap._truncate(long_text)
        assert len(result) < len(long_text)
        assert "truncated" in result.lower()

    async def test_wrap_tool_execute_truncates_string(self) -> None:
        """Verify wrap_tool_execute truncates via _truncate for strings."""
        cap = ToolOutputBudgetCapability(max_output_chars=200)

        async def handler(args):  # type: ignore[no-untyped-def]
            return "x" * 500

        result = await cap.wrap_tool_execute(
            ctx=None,  # type: ignore[arg-type]
            call=None,  # type: ignore[arg-type]
            tool_def=None,  # type: ignore[arg-type]
            args=None,
            handler=handler,
        )
        assert len(result) < 500
        assert "truncated" in result.lower()

    async def test_wrap_tool_execute_truncates_list_items(self) -> None:
        """Verify wrap_tool_execute truncates strings inside lists."""
        cap = ToolOutputBudgetCapability(max_output_chars=200)

        async def handler(args):  # type: ignore[no-untyped-def]
            return ["x" * 500, "short"]

        result = await cap.wrap_tool_execute(
            ctx=None,  # type: ignore[arg-type]
            call=None,  # type: ignore[arg-type]
            tool_def=None,  # type: ignore[arg-type]
            args=None,
            handler=handler,
        )
        assert len(result[0]) < 500
        assert result[1] == "short"


# =============================================================================
# DynamicContextCapability
# =============================================================================


class TestDynamicContext:
    """Test dynamic context compaction capability."""

    def test_init_default(self) -> None:
        cap = DynamicContextCapability()
        assert cap.max_messages == 50
        assert cap.compaction_threshold == 0.8

    def test_init_custom(self) -> None:
        cap = DynamicContextCapability(max_messages=100, compaction_threshold=0.5)
        assert cap.max_messages == 100
        assert cap.compaction_threshold == 0.5

    def test_init_invalid_messages_raises(self) -> None:
        with pytest.raises(ValueError, match="max_messages"):
            DynamicContextCapability(max_messages=1)

    def test_init_invalid_threshold_raises(self) -> None:
        with pytest.raises(ValueError, match="compaction_threshold"):
            DynamicContextCapability(compaction_threshold=0.0)
        with pytest.raises(ValueError, match="compaction_threshold"):
            DynamicContextCapability(compaction_threshold=1.5)

    def test_has_wrap_node_run_false(self) -> None:
        cap = DynamicContextCapability()
        assert cap.has_wrap_node_run is False

    async def test_before_model_request_no_compaction_below_threshold(
        self,
    ) -> None:
        """Messages below threshold are returned unchanged."""
        cap = DynamicContextCapability(max_messages=100, compaction_threshold=0.5)
        ctx_mock = MagicMock()
        rc = _make_request_context([])
        result = await cap.before_model_request(ctx_mock, rc)  # type: ignore[arg-type]
        assert result is rc

    async def test_before_model_request_applies_compaction_fn(self) -> None:
        """When above threshold and fn is set, messages are replaced."""
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        cap = DynamicContextCapability(max_messages=4, compaction_threshold=0.5)
        # threshold_count = int(4 * 0.5) = 2; 3 messages > 2

        async def compact(messages, threshold):  # type: ignore[no-untyped-def]
            return [messages[-1]]  # keep only last

        cap.set_compaction_fn(compact)

        msgs = [
            ModelRequest(parts=[UserPromptPart("msg1")]),
            ModelRequest(parts=[UserPromptPart("msg2")]),
            ModelRequest(parts=[UserPromptPart("msg3")]),
        ]
        ctx_mock = MagicMock()
        rc = _make_request_context(msgs)
        result = await cap.before_model_request(ctx_mock, rc)  # type: ignore[arg-type]
        assert len(result.messages) == 1

    async def test_before_model_request_warns_when_no_fn(self) -> None:
        """When above threshold but no fn, log a warning (messages unchanged)."""
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        cap = DynamicContextCapability(max_messages=4, compaction_threshold=0.5)

        msgs = [
            ModelRequest(parts=[UserPromptPart("msg1")]),
            ModelRequest(parts=[UserPromptPart("msg2")]),
            ModelRequest(parts=[UserPromptPart("msg3")]),
        ]
        ctx_mock = MagicMock()
        rc = _make_request_context(msgs)
        result = await cap.before_model_request(ctx_mock, rc)  # type: ignore[arg-type]
        # Messages unchanged
        assert len(result.messages) == 3

    async def test_for_run_creates_fresh_copy(self) -> None:
        cap = DynamicContextCapability(max_messages=100, compaction_threshold=0.5)
        fresh = await cap.for_run(None)  # type: ignore[arg-type]
        assert fresh.max_messages == 100
        assert fresh.compaction_threshold == 0.5


# =============================================================================
# SkillActivationCapability
# =============================================================================


class TestSkillActivation:
    """Test skill activation capability."""

    def test_init_default(self) -> None:
        cap = SkillActivationCapability()
        assert cap._skills == {}

    def test_register_skill(self) -> None:
        cap = SkillActivationCapability()
        cap.register_skill("code-review", "Review code for bugs.")
        assert cap._skills["code-review"] == "Review code for bugs."

    def test_set_matcher(self) -> None:
        cap = SkillActivationCapability()

        async def matcher(msgs, names):  # type: ignore[no-untyped-def]
            return []

        cap.set_matcher(matcher)
        assert cap._matcher_fn is matcher

    def test_has_wrap_node_run_false(self) -> None:
        cap = SkillActivationCapability()
        assert cap.has_wrap_node_run is False

    async def test_before_model_request_injects_skill_into_system_prompt(
        self,
    ) -> None:
        """Verify skill instructions are appended to SystemPromptPart.content."""
        from pydantic_ai.messages import (
            ModelRequest,
            SystemPromptPart,
            UserPromptPart,
        )

        cap = SkillActivationCapability()
        cap.register_skill("python", "Use Python 3.13 best practices.")

        async def matcher(msgs, names):  # type: ignore[no-untyped-def]
            return ["python"]

        cap.set_matcher(matcher)

        sys_part = SystemPromptPart(content="You are a coding assistant.")
        req = ModelRequest(parts=[sys_part, UserPromptPart("Write a function.")])
        ctx_mock = MagicMock()
        rc = _make_request_context([req])

        result = await cap.before_model_request(ctx_mock, rc)  # type: ignore[arg-type]

        # The system prompt should now contain the injected skill text
        assert "Use Python 3.13 best practices." in sys_part.content
        assert "You are a coding assistant." in sys_part.content
        assert result is rc

    async def test_before_model_request_no_match_returns_unchanged(self) -> None:
        """When matcher returns no matches, messages are unchanged."""
        from pydantic_ai.messages import (
            ModelRequest,
            SystemPromptPart,
            UserPromptPart,
        )

        cap = SkillActivationCapability()
        cap.register_skill("python", "Use Python 3.13 best practices.")

        async def matcher(msgs, names):  # type: ignore[no-untyped-def]
            return []

        cap.set_matcher(matcher)

        sys_part = SystemPromptPart(content="original prompt")
        req = ModelRequest(parts=[sys_part, UserPromptPart("hello")])
        ctx_mock = MagicMock()
        rc = _make_request_context([req])

        await cap.before_model_request(ctx_mock, rc)  # type: ignore[arg-type]
        assert sys_part.content == "original prompt"

    async def test_before_model_request_no_skills_returns_unchanged(self) -> None:
        """When no skills registered, returns unchanged."""
        cap = SkillActivationCapability()
        ctx_mock = MagicMock()
        rc = _make_request_context([])
        result = await cap.before_model_request(ctx_mock, rc)  # type: ignore[arg-type]
        assert result is rc

    async def test_for_run_shares_skills(self) -> None:
        cap = SkillActivationCapability()
        cap.register_skill("python", "instructions")
        fresh = await cap.for_run(None)  # type: ignore[arg-type]
        assert fresh._skills is cap._skills


# =============================================================================
# MemoryCapability
# =============================================================================


class TestMemory:
    """Test memory capability."""

    def test_init_default(self) -> None:
        cap = MemoryCapability()
        assert cap._store == {}

    def test_set_extract_fn(self) -> None:
        cap = MemoryCapability()

        async def extract(result):  # type: ignore[no-untyped-def]
            return {}

        cap.set_extract_fn(extract)
        assert cap._extract_fn is extract

    def test_set_inject_fn(self) -> None:
        cap = MemoryCapability()

        async def inject(store, messages):  # type: ignore[no-untyped-def]
            return ""

        cap.set_inject_fn(inject)
        assert cap._inject_fn is inject

    def test_has_wrap_node_run_false(self) -> None:
        cap = MemoryCapability()
        assert cap.has_wrap_node_run is False

    async def test_after_node_run_extracts_and_stores(self) -> None:
        """Verify after_node_run calls extract_fn and updates store."""
        cap = MemoryCapability()

        async def extract(result):  # type: ignore[no-untyped-def]
            return {"key1": "value1"}

        cap.set_extract_fn(extract)

        ctx_mock = MagicMock()
        result_mock = MagicMock()
        result = await cap.after_node_run(
            ctx_mock,
            node=None,
            result=result_mock,  # type: ignore[arg-type]
        )
        assert cap._store == {"key1": "value1"}
        assert result is result_mock

    async def test_after_node_run_no_extract_fn_returns_unchanged(self) -> None:
        """When no extract_fn, result is returned unchanged."""
        cap = MemoryCapability()
        ctx_mock = MagicMock()
        result_mock = MagicMock()
        result = await cap.after_node_run(
            ctx_mock,
            node=None,
            result=result_mock,  # type: ignore[arg-type]
        )
        assert result is result_mock
        assert cap._store == {}

    async def test_before_model_request_injects_into_system_prompt(self) -> None:
        """Verify memory is injected into SystemPromptPart.content."""
        from pydantic_ai.messages import (
            ModelRequest,
            SystemPromptPart,
            UserPromptPart,
        )

        cap = MemoryCapability()
        cap._store = {"user_name": "Alice"}

        async def inject(store, messages):  # type: ignore[no-untyped-def]
            return f"User name: {store['user_name']}"

        cap.set_inject_fn(inject)

        sys_part = SystemPromptPart(content="You are a helpful assistant.")
        req = ModelRequest(parts=[sys_part, UserPromptPart("What's my name?")])
        ctx_mock = MagicMock()
        rc = _make_request_context([req])

        result = await cap.before_model_request(ctx_mock, rc)  # type: ignore[arg-type]

        assert "User name: Alice" in sys_part.content
        assert "You are a helpful assistant." in sys_part.content
        assert result is rc

    async def test_before_model_request_empty_store_returns_unchanged(
        self,
    ) -> None:
        """When store is empty, returns unchanged."""
        cap = MemoryCapability()
        ctx_mock = MagicMock()
        rc = _make_request_context([])
        result = await cap.before_model_request(ctx_mock, rc)  # type: ignore[arg-type]
        assert result is rc

    async def test_before_model_request_no_inject_fn_returns_unchanged(
        self,
    ) -> None:
        """When no inject_fn, returns unchanged even if store has data."""
        cap = MemoryCapability()
        cap._store = {"key": "value"}
        ctx_mock = MagicMock()
        rc = _make_request_context([])
        result = await cap.before_model_request(ctx_mock, rc)  # type: ignore[arg-type]
        assert result is rc

    async def test_for_run_shares_store_reference(self) -> None:
        """Verify for_run() shares the same dict (persists across runs)."""
        cap = MemoryCapability()
        cap._store = {"key": "value"}

        async def extract(result):  # type: ignore[no-untyped-def]
            return {}

        async def inject(store, messages):  # type: ignore[no-untyped-def]
            return ""

        cap.set_extract_fn(extract)
        cap.set_inject_fn(inject)

        fresh = await cap.for_run(None)  # type: ignore[arg-type]
        assert fresh._store is cap._store  # Same reference, not a copy
        assert fresh._extract_fn is cap._extract_fn
        assert fresh._inject_fn is cap._inject_fn

    async def test_for_run_persists_memory_across_runs(self) -> None:
        """Verify memories extracted in a run persist to subsequent runs."""
        cap = MemoryCapability()

        async def extract(result):  # type: ignore[no-untyped-def]
            return {"memory_key": "memory_value"}

        cap.set_extract_fn(extract)

        # Simulate first run
        run1 = await cap.for_run(None)  # type: ignore[arg-type]
        ctx_mock = MagicMock()
        result_mock = MagicMock()
        await run1.after_node_run(
            ctx_mock,
            node=None,
            result=result_mock,  # type: ignore[arg-type]
        )

        # Second run should see the memory from first run
        run2 = await cap.for_run(None)  # type: ignore[arg-type]
        assert run2._store == {"memory_key": "memory_value"}


# =============================================================================
# _inject_into_system_prompt helper
# =============================================================================


class TestInjectIntoSystemPrompt:
    """Test the shared _inject_into_system_prompt helper."""

    def test_inject_into_existing_system_prompt(self) -> None:
        from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart

        sys_part = SystemPromptPart(content="original")
        req = ModelRequest(parts=[sys_part, UserPromptPart("hello")])

        result = _inject_into_system_prompt([req], "injected text")
        assert result is True
        assert "original" in sys_part.content
        assert "injected text" in sys_part.content

    def test_inject_skips_if_already_present(self) -> None:
        from pydantic_ai.messages import ModelRequest, SystemPromptPart

        sys_part = SystemPromptPart(content="has injected text already")
        req = ModelRequest(parts=[sys_part])

        result = _inject_into_system_prompt([req], "injected text")
        assert result is True
        # Content should be unchanged since the text was already present
        assert sys_part.content == "has injected text already"

    def test_inject_returns_false_when_no_system_prompt(self) -> None:
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        req = ModelRequest(parts=[UserPromptPart("no system prompt here")])

        result = _inject_into_system_prompt([req], "injected text")
        assert result is False
