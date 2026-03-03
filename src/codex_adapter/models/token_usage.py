"""Token usage models for Codex."""

from __future__ import annotations

from codex_adapter.models.base import CodexBaseModel


class TokenUsageBreakdown(CodexBaseModel):
    """Token usage breakdown."""

    total_tokens: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int = 0


class ThreadTokenUsage(CodexBaseModel):
    """Thread token usage information."""

    total: TokenUsageBreakdown
    last: TokenUsageBreakdown
    model_context_window: int | None = None


class Usage(CodexBaseModel):
    """Simple token usage (legacy)."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
