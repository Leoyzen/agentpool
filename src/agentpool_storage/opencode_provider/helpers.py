"""Helper functions for OpenCode SQLite storage provider.

Stateless conversion and utility functions for working with OpenCode's
SQLite-based format. Converts between raw database rows and domain models.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic_ai import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    RequestUsage,
    RunUsage,
    TextPart as PydanticTextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agentpool.log import get_logger
from agentpool.messaging import ChatMessage, TokenCost
from agentpool.utils.pydantic_ai_helpers import to_user_content
from agentpool.utils.time_utils import ms_to_datetime
from opencode_sdk.helpers import extract_text_content
from opencode_sdk.models import (
    AssistantMessage,
    FilePart,
    ReasoningPart,
    TextPart,
    ToolPart,
    ToolStateCompleted,
    UserMessage,
)


if TYPE_CHECKING:
    from datetime import datetime

    from pydantic_ai.messages import UserContent

    from opencode_sdk.models import MessageInfo, Part


logger = get_logger(__name__)


def _build_user_pydantic_messages(
    parts: list[Part],
    timestamp: datetime,
) -> list[ModelRequest | ModelResponse]:
    """Build ModelRequest from user message parts."""
    user_content: list[UserContent] = []
    for part in parts:
        match part:
            case TextPart(text=text) if text:
                user_content.append(text)
            case FilePart(url=url) if url.startswith("data:") and ";base64," in url:
                user_content.append(BinaryContent.from_data_uri(url))
            case FilePart(url=url, mime=mime) if url:
                user_content.append(to_user_content(url, mime))
    if user_content:
        user_part = UserPromptPart(content=user_content, timestamp=timestamp)
        return [ModelRequest(parts=[user_part], timestamp=timestamp)]
    return []


def _build_assistant_pydantic_messages(
    msg: AssistantMessage,
    parts: list[Part],
    timestamp: datetime,
) -> list[ModelRequest | ModelResponse]:
    """Build ModelResponse (+ optional ModelRequest for tool returns) from assistant parts."""
    result: list[ModelRequest | ModelResponse] = []
    response_parts: list[PydanticTextPart | ToolCallPart | ThinkingPart] = []
    tool_return_parts: list[ToolReturnPart] = []

    tokens = msg.tokens
    cache = tokens.cache
    usage = RequestUsage(
        input_tokens=tokens.input,
        output_tokens=tokens.output,
        cache_read_tokens=cache.read,
        cache_write_tokens=cache.write,
    )

    for part in parts:
        match part:
            case TextPart(text=text) if text:
                response_parts.append(PydanticTextPart(content=text))
            case ReasoningPart(text=text) if text:
                response_parts.append(ThinkingPart(content=text))
            case ToolPart(tool=tool, call_id=call_id, state=state):
                tc_part = ToolCallPart(tool_name=tool, args=state.input, tool_call_id=call_id)
                response_parts.append(tc_part)
                if isinstance(state, ToolStateCompleted) and state.output:
                    tr_part = ToolReturnPart(
                        tool_name=tool,
                        content=state.output,
                        tool_call_id=call_id,
                        timestamp=timestamp,
                    )
                    tool_return_parts.append(tr_part)

    if response_parts:
        model_response = ModelResponse(
            parts=response_parts,
            usage=usage,
            model_name=msg.model_id,
            timestamp=timestamp,
        )
        result.append(model_response)

    if tool_return_parts:
        result.append(ModelRequest(parts=tool_return_parts, timestamp=timestamp))

    return result


def build_pydantic_messages(
    msg: MessageInfo,
    parts: list[Part],
    timestamp: datetime,
) -> list[ModelRequest | ModelResponse]:
    """Build pydantic-ai messages from typed OpenCode models.

    In OpenCode's model, assistant messages contain both tool calls AND their
    results in the same message. We split these into:
    - ModelResponse with ToolCallPart (the call)
    - ModelRequest with ToolReturnPart (the result)

    Args:
        msg: Typed UserMessage or AssistantMessage
        parts: List of typed Part models
        timestamp: Message timestamp

    Returns:
        List of pydantic-ai messages (ModelRequest and/or ModelResponse)
    """
    if isinstance(msg, UserMessage):
        return _build_user_pydantic_messages(parts, timestamp)
    return _build_assistant_pydantic_messages(msg, parts, timestamp)


def to_chat_message(
    *,
    msg: MessageInfo,
    parts: list[Part],
) -> ChatMessage[str]:
    """Convert typed OpenCode message + parts to ChatMessage.

    Args:
        msg: Typed UserMessage or AssistantMessage
        parts: List of typed Part models

    Returns:
        ChatMessage with content, pydantic messages, cost info etc.
    """
    timestamp = ms_to_datetime(msg.time.created)
    content = extract_text_content(parts)
    pydantic_messages = build_pydantic_messages(msg, parts, timestamp)

    cost_info = None
    provider_details: dict[str, Any] = {}
    parent_id: str | None = None
    model_name: str | None = None
    agent_name: str | None = msg.agent if msg.agent != "default" else None

    if isinstance(msg, AssistantMessage):
        tokens = msg.tokens
        cache = tokens.cache
        input_tokens = tokens.input + cache.read
        output_tokens = tokens.output
        if input_tokens or output_tokens:
            usage = RunUsage(input_tokens=input_tokens, output_tokens=output_tokens)
            cost = Decimal(str(msg.cost))
            cost_info = TokenCost(token_usage=usage, total_cost=cost)
        if msg.finish:
            provider_details["finish_reason"] = msg.finish
        parent_id = msg.parent_id
        model_name = msg.model_id
        agent_name = msg.agent if msg.agent != "default" else None
    elif isinstance(msg, UserMessage) and msg.model is not None:
        model_name = msg.model.model_id

    return ChatMessage[str](
        content=content,
        session_id=msg.session_id,
        role=msg.role,
        message_id=msg.id,
        name=agent_name,
        model_name=model_name,
        cost_info=cost_info,
        timestamp=timestamp,
        parent_id=parent_id,
        messages=pydantic_messages,
        provider_details=provider_details,
    )
