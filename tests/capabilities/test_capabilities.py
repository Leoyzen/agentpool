"""Tests for pdai Capability implementations (Phase 6)."""

from __future__ import annotations

import pytest

from agentpool.capabilities.loop_detection import (
    LoopDetectionCapability,
    LoopDetectionError,
)
from agentpool.capabilities.token_budget import (
    TokenBudgetCapability,
    TokenBudgetExceededError,
)
from agentpool.capabilities.tool_output_budget import ToolOutputBudgetCapability


# =============================================================================
# LoopDetectionCapability
# =============================================================================


class TestLoopDetection:
    """Test loop detection capability."""

    def test_init_default(self) -> None:
        cap = LoopDetectionCapability()
        assert cap.max_depth == 10
        assert cap._depth == 0

    def test_init_custom_depth(self) -> None:
        cap = LoopDetectionCapability(max_depth=5)
        assert cap.max_depth == 5

    def test_init_invalid_depth_raises(self) -> None:
        with pytest.raises(ValueError, match="max_depth"):
            LoopDetectionCapability(max_depth=0)

    def test_has_wrap_node_run(self) -> None:
        cap = LoopDetectionCapability()
        assert cap.has_wrap_node_run is True

    def test_for_run_creates_fresh_copy(self) -> None:
        cap = LoopDetectionCapability(max_depth=7)
        cap._depth = 3
        fresh = cap.for_run(None)  # type: ignore[arg-type]
        assert fresh.max_depth == 7
        assert fresh._depth == 0

    def test_loop_detection_error_message(self) -> None:
        err = LoopDetectionError(depth=15, max_depth=10)
        assert "15" in str(err)
        assert "10" in str(err)


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

    def test_for_run_creates_fresh_copy(self) -> None:
        cap = TokenBudgetCapability(max_tokens=5000)
        cap._used_tokens = 3000
        fresh = cap.for_run(None)  # type: ignore[arg-type]
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

    def test_for_run_creates_fresh_copy(self) -> None:
        cap = ToolOutputBudgetCapability(max_output_chars=500)
        fresh = cap.for_run(None)  # type: ignore[arg-type]
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
