"""Models for OpenAI responses endpoint."""

from __future__ import annotations

from typing import Any

from openai.types.responses import EasyInputMessageParam
from pydantic import Field
from schemez import Schema


class ResponseRequest(Schema):
    """Request for /v1/responses endpoint."""

    model: str
    input: str | list[EasyInputMessageParam]
    instructions: str | None = None
    stream: bool = False
    temperature: float = 1.0
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: str = "auto"
    max_output_tokens: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
