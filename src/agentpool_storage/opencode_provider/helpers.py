"""Helper functions for OpenCode SQLite storage provider.

Stateless conversion and utility functions for working with OpenCode's
SQLite-based format. Converts between raw database rows and domain models.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic_ai import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
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

    from pydantic_ai import ModelMessage, UserContent

    from opencode_sdk.models import MessageWithParts, Part


logger = get_logger(__name__)


def _build_user_pydantic_messages(parts: list[Part], timestamp: datetime) -> list[ModelMessage]:
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
) -> list[ModelMessage]:
    """Build ModelResponse (+ optional ModelRequest for tool returns) from assistant parts."""
    result: list[ModelMessage] = []
    response_parts: list[PydanticTextPart | ToolCallPart | ThinkingPart] = []
    tool_return_parts: list[ToolReturnPart] = []

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
            usage=msg.tokens.to_request_usage(),
            model_name=msg.model_id,
            timestamp=timestamp,
        )
        result.append(model_response)

    if tool_return_parts:
        result.append(ModelRequest(parts=tool_return_parts, timestamp=timestamp))

    return result


def to_chat_message(message: MessageWithParts) -> ChatMessage[str]:
    """Convert typed OpenCode message + parts to ChatMessage.

    Args:
        message: Message (with parts)

    Returns:
        ChatMessage with content, pydantic messages, cost info etc.
    """
    from agentpool_server.opencode_server.converters import to_native_finish_reason

    msg = message.info
    timestamp = ms_to_datetime(msg.time.created)
    content = extract_text_content(message.parts)
    agent_name = msg.agent if msg.agent != "default" else None
    match msg:
        case AssistantMessage(
            tokens=tokens,
            finish=finish,
            cost=cost,
            parent_id=parent_id,
            model_id=model_name,
            id=message_id,
            session_id=session_id,
        ):
            return ChatMessage[str](
                content=content,
                session_id=session_id,
                role="assistant",
                message_id=message_id,
                name=agent_name,
                model_name=model_name,
                cost_info=TokenCost(total_cost=Decimal(str(cost))),
                finish_reason=to_native_finish_reason(finish),
                usage=tokens.to_run_usage(),
                timestamp=timestamp,
                parent_id=parent_id,
                messages=_build_assistant_pydantic_messages(msg, message.parts, timestamp),
            )
        case UserMessage(model=model, id=message_id, session_id=session_id):
            return ChatMessage[str](
                content=content,
                session_id=session_id,
                role="user",
                message_id=message_id,
                name=agent_name,
                model_name=model.model_id,
                usage=RunUsage(input_tokens=0, output_tokens=0),
                timestamp=timestamp,
                messages=_build_user_pydantic_messages(message.parts, timestamp),
            )
