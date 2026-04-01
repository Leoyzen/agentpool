"""OpenAI-compatible API models for AgentPool chat completions."""

from __future__ import annotations

from typing import Any, Literal

from openai.types.chat import (
    ChatCompletion as ChatCompletionResponse,
    ChatCompletionChunk,
    ChatCompletionMessage,
    ChatCompletionMessageToolCall as ToolCall,
)
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_function_tool_call import (
    Function as FunctionCall,
)
from openai.types.completion_usage import CompletionUsage
from pydantic import Field
from schemez import Schema


__all__ = [
    "ChatCompletionChunk",
    "ChatCompletionMessage",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "Choice",
    "CompletionUsage",
    "FunctionCall",
    "OpenAIMessage",
    "OpenAIModelInfo",
    "ToolCall",
]


class OpenAIModelInfo(Schema):
    """OpenAI model info format."""

    id: str
    object: str = "model"
    owned_by: str = "agentpool"
    created: int
    description: str | None = None
    permissions: list[str] = Field(default_factory=list)


class OpenAIMessage(Schema):
    """OpenAI chat message format (for request input).

    Covers all roles in a single model for easy request parsing.
    """

    role: Literal["system", "user", "assistant", "tool", "function"]
    content: str | None = None
    name: str | None = None
    function_call: FunctionCall | None = None
    tool_calls: list[ToolCall] | None = None


class ChatCompletionRequest(Schema):
    """OpenAI chat completion request."""

    model: str
    messages: list[OpenAIMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | None = Field(default="auto")
