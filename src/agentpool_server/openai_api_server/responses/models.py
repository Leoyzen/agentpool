"""Models for OpenAI responses endpoint."""

from __future__ import annotations

from typing import Any, Literal

from openai.types.responses import EasyInputMessageParam
from pydantic import Field
from schemez import Schema


ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
ServiceTier = Literal["auto", "default", "flex"]
Truncation = Literal["auto", "disabled"]
GenerateSummary = Literal["auto", "concise", "detailed"]


class Reasoning(Schema):
    """Reasoning/thinking configuration."""

    effort: ReasoningEffort = "medium"
    generate_summary: GenerateSummary | None = None
    summary: GenerateSummary | None = None


class ResponseRequest(Schema):
    """Request for /v1/responses endpoint."""

    model: str
    input: str | list[EasyInputMessageParam] | None = None
    instructions: str | None = None
    previous_response_id: str | None = None
    stream: bool = False
    temperature: float = 1.0
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: str = "auto"
    max_output_tokens: int | None = None
    max_tool_calls: int | None = None
    parallel_tool_calls: bool = True
    reasoning: Reasoning | None = None
    store: bool = True
    truncation: Truncation | None = None
    service_tier: ServiceTier | None = None
    user: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
