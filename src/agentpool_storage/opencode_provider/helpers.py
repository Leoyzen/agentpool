"""Helper functions for OpenCode SQLite storage provider.

Stateless conversion and utility functions for working with OpenCode's
SQLite-based format. Converts between raw database rows and domain models.
"""

from __future__ import annotations

import base64
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic_ai import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    ModelRequest,
    ModelResponse,
    RequestUsage,
    RunUsage,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
    VideoUrl,
)

from agentpool.log import get_logger
from agentpool.messaging import ChatMessage, TokenCost
from agentpool.utils.time_utils import ms_to_datetime


if TYPE_CHECKING:
    from datetime import datetime

    from pydantic_ai import MultiModalContent
    from pydantic_ai.messages import UserContent


logger = get_logger(__name__)


# ── Row type aliases ──────────────────────────────────────────────────────────
# These match the SQLite column layout for type-safe row access.

# session row: (id, project_id, parent_id, slug, directory, title, version,
#   share_url, summary_additions, summary_deletions, summary_files,
#   summary_diffs, revert, permission, time_created, time_updated,
#   time_compacting, time_archived)
# We access by column name via sqlite3.Row

# message row: (id, session_id, time_created, time_updated, data)
# part row: (id, message_id, session_id, time_created, time_updated, data)


def to_user_content(url: str, mime: str) -> MultiModalContent:
    """Convert a URL + MIME type to the appropriate pydantic-ai content type."""
    if mime.startswith("image/"):
        mime_typ = mime if mime != "image/*" else None
        return ImageUrl(url=url, media_type=mime_typ)
    if mime.startswith("audio/"):
        mime_typ = mime if mime != "audio/*" else None
        return AudioUrl(url=url, media_type=mime_typ)
    if mime.startswith("video/"):
        mime_typ = mime if mime != "video/*" else None
        return VideoUrl(url=url, media_type=mime_typ)
    if mime == "application/pdf" or mime.startswith("application/"):
        return DocumentUrl(url=url, media_type=mime)
    # Unknown MIME type - treat as document
    return DocumentUrl(url=url, media_type=mime)


def extract_text_content(parts: list[dict[str, Any]]) -> str:
    """Extract text content from part data dicts for display.

    Groups consecutive reasoning parts into a single <thinking> block
    and only wraps them if there are also non-reasoning parts present.

    Args:
        parts: List of part data dicts (the JSON 'data' field from DB)

    Returns:
        Combined text content from all text and reasoning parts
    """
    text_segments: list[str] = []
    reasoning_segments: list[str] = []
    has_text = False

    for part in parts:
        part_type = part.get("type", "")
        if part_type == "text":
            text = part.get("text", "")
            if text:
                has_text = True
                # Flush any accumulated reasoning before this text
                if reasoning_segments:
                    combined = "\n".join(reasoning_segments)
                    text_segments.append(f"<thinking>\n{combined}\n</thinking>")
                    reasoning_segments.clear()
                text_segments.append(text)
        elif part_type == "reasoning":
            text = part.get("text", "")
            if text:
                reasoning_segments.append(text)

    # Flush remaining reasoning
    if reasoning_segments:
        combined = "\n".join(reasoning_segments)
        if has_text:
            text_segments.append(f"<thinking>\n{combined}\n</thinking>")
        else:
            # Entire message is thinking — no need for wrapper tags
            text_segments.append(combined)

    return "\n".join(text_segments)


def _build_user_pydantic_messages(
    parts: list[dict[str, Any]],
    timestamp: datetime,
) -> list[ModelRequest | ModelResponse]:
    """Build ModelRequest from user message parts."""
    user_content: list[UserContent] = []
    for part in parts:
        part_type = part.get("type", "")
        if part_type == "text":
            text = part.get("text", "")
            if text:
                user_content.append(text)
        elif part_type == "file":
            url = part.get("url", "")
            mime = part.get("mime", "application/octet-stream")
            if url.startswith("data:") and ";base64," in url:
                mime_part, b64_data = url.split(";base64,", 1)
                media_type = mime_part.replace("data:", "")
                data = base64.b64decode(b64_data)
                user_content.append(BinaryContent(data=data, media_type=media_type))
            elif url:
                content_item = to_user_content(url, mime)
                user_content.append(content_item)
    if user_content:
        user_part = UserPromptPart(content=user_content, timestamp=timestamp)
        return [ModelRequest(parts=[user_part], timestamp=timestamp)]
    return []


def _build_assistant_pydantic_messages(
    msg_data: dict[str, Any],
    parts: list[dict[str, Any]],
    timestamp: datetime,
) -> list[ModelRequest | ModelResponse]:
    """Build ModelResponse (+ optional ModelRequest for tool returns) from assistant parts."""
    result: list[ModelRequest | ModelResponse] = []
    response_parts: list[TextPart | ToolCallPart | ThinkingPart] = []
    tool_return_parts: list[ToolReturnPart] = []

    tokens = msg_data.get("tokens", {})
    cache = tokens.get("cache", {})
    usage = RequestUsage(
        input_tokens=tokens.get("input", 0),
        output_tokens=tokens.get("output", 0),
        cache_read_tokens=cache.get("read", 0),
        cache_write_tokens=cache.get("write", 0),
    )

    for part in parts:
        part_type = part.get("type", "")
        if part_type == "text":
            text = part.get("text", "")
            if text:
                response_parts.append(TextPart(content=text))
        elif part_type == "reasoning":
            text = part.get("text", "")
            if text:
                response_parts.append(ThinkingPart(content=text))
        elif part_type == "tool":
            call_id = part.get("callID", "")
            tool = part.get("tool", "")
            state = part.get("state", {})
            status = state.get("status", "")
            input_data = state.get("input", {})

            tc_part = ToolCallPart(
                tool_name=tool,
                args=input_data,
                tool_call_id=call_id,
            )
            response_parts.append(tc_part)

            if status == "completed":
                output = state.get("output", "")
                if output:
                    return_part = ToolReturnPart(
                        tool_name=tool,
                        content=output,
                        tool_call_id=call_id,
                        timestamp=timestamp,
                    )
                    tool_return_parts.append(return_part)

    if response_parts:
        model_response = ModelResponse(
            parts=response_parts,
            usage=usage,
            model_name=msg_data.get("modelID", ""),
            timestamp=timestamp,
        )
        result.append(model_response)

    if tool_return_parts:
        result.append(ModelRequest(parts=tool_return_parts, timestamp=timestamp))

    return result


def build_pydantic_messages(
    msg_data: dict[str, Any],
    parts: list[dict[str, Any]],
    timestamp: datetime,
) -> list[ModelRequest | ModelResponse]:
    """Build pydantic-ai messages from OpenCode DB data.

    In OpenCode's model, assistant messages contain both tool calls AND their
    results in the same message. We split these into:
    - ModelResponse with ToolCallPart (the call)
    - ModelRequest with ToolReturnPart (the result)

    Args:
        msg_data: The message 'data' JSON from the DB (contains role, tokens, etc.)
        parts: List of part 'data' JSON dicts from the DB
        timestamp: Message timestamp

    Returns:
        List of pydantic-ai messages (ModelRequest and/or ModelResponse)
    """
    role = msg_data.get("role", "")
    if role == "user":
        return _build_user_pydantic_messages(parts, timestamp)
    return _build_assistant_pydantic_messages(msg_data, parts, timestamp)


def to_chat_message(
    *,
    message_id: str,
    session_id: str,
    msg_data: dict[str, Any],
    parts: list[dict[str, Any]],
    time_created: int,
) -> ChatMessage[str]:
    """Convert OpenCode DB message + parts to ChatMessage.

    Args:
        message_id: Message ID from DB
        session_id: Session ID from DB
        msg_data: The message 'data' JSON field
        parts: List of part 'data' JSON dicts
        time_created: Message creation time in ms

    Returns:
        ChatMessage with content, pydantic messages, cost info etc.
    """
    timestamp = ms_to_datetime(time_created)
    content = extract_text_content(parts)
    pydantic_messages = build_pydantic_messages(msg_data, parts, timestamp)

    role = msg_data.get("role", "user")
    cost_info = None
    provider_details: dict[str, Any] = {}
    parent_id: str | None = None
    model_name: str | None = None
    agent_name: str | None = msg_data.get("agent")

    if role == "assistant":
        tokens = msg_data.get("tokens", {})
        cache = tokens.get("cache", {})
        input_tokens = tokens.get("input", 0) + cache.get("read", 0)
        output_tokens = tokens.get("output", 0)
        if input_tokens or output_tokens:
            usage = RunUsage(input_tokens=input_tokens, output_tokens=output_tokens)
            cost = Decimal(str(msg_data.get("cost", 0)))
            cost_info = TokenCost(token_usage=usage, total_cost=cost)
        finish = msg_data.get("finish")
        if finish:
            provider_details["finish_reason"] = finish
        parent_id = msg_data.get("parentID")
        model_name = msg_data.get("modelID")
    else:
        model_ref = msg_data.get("model")
        if isinstance(model_ref, dict):
            model_name = model_ref.get("modelID")

    return ChatMessage[str](
        content=content,
        session_id=session_id,
        role=role,
        message_id=message_id,
        name=agent_name,
        model_name=model_name,
        cost_info=cost_info,
        timestamp=timestamp,
        parent_id=parent_id,
        messages=pydantic_messages,
        provider_details=provider_details,
    )
