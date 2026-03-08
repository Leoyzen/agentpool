"""Helper functions for OpenCode SQLite storage provider.

Stateless conversion and utility functions for working with OpenCode's
SQLite-based format. Converts between raw database rows and domain models.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from agentpool.log import get_logger
from opencode_sdk.models.message import MessageInfo
from opencode_sdk.models.parts import Part, ReasoningPart, TextPart


logger = get_logger(__name__)

_message_info_adapter: TypeAdapter[MessageInfo] = TypeAdapter(MessageInfo)
_part_adapter: TypeAdapter[Part] = TypeAdapter(Part)


def parse_message_info(data: dict[str, Any], *, message_id: str, session_id: str) -> MessageInfo:
    """Parse a message JSON data dict into a typed MessageInfo model.

    Injects the DB column fields (id, sessionID) into the data dict before
    validation, matching how OpenCode itself reconstructs messages from DB rows.

    Args:
        data: The JSON 'data' field from the message table
        message_id: Message ID from the DB id column
        session_id: Session ID from the DB session_id column

    Returns:
        Validated UserMessage or AssistantMessage
    """
    data["id"] = message_id
    data["sessionID"] = session_id
    return _message_info_adapter.validate_python(data)


def parse_part(data: dict[str, Any], *, part_id: str, message_id: str, session_id: str) -> Part:
    """Parse a part JSON data dict into a typed Part model.

    Injects the DB column fields (id, messageID, sessionID) into the data dict
    before validation, matching how OpenCode itself reconstructs parts from DB rows.

    Args:
        data: The JSON 'data' field from the part table
        part_id: Part ID from the DB id column
        message_id: Message ID from the DB message_id column
        session_id: Session ID from the DB session_id column

    Returns:
        Validated Part (TextPart, ToolPart, ReasoningPart, etc.)
    """
    data["id"] = part_id
    data["messageID"] = message_id
    data["sessionID"] = session_id
    return _part_adapter.validate_python(data)


def extract_text_content(parts: list[Part]) -> str:
    """Extract text content from typed parts for display.

    Groups consecutive reasoning parts into a single <thinking> block
    and only wraps them if there are also non-reasoning parts present.

    Args:
        parts: List of typed Part models

    Returns:
        Combined text content from all text and reasoning parts
    """
    text_segments: list[str] = []
    reasoning_segments: list[str] = []
    has_text = False

    for part in parts:
        if isinstance(part, TextPart):
            if part.text:
                has_text = True
                # Flush any accumulated reasoning before this text
                if reasoning_segments:
                    combined = "\n".join(reasoning_segments)
                    text_segments.append(f"<thinking>\n{combined}\n</thinking>")
                    reasoning_segments.clear()
                text_segments.append(part.text)
        elif isinstance(part, ReasoningPart) and part.text:
            reasoning_segments.append(part.text)

    # Flush remaining reasoning
    if reasoning_segments:
        combined = "\n".join(reasoning_segments)
        if has_text:
            text_segments.append(f"<thinking>\n{combined}\n</thinking>")
        else:
            # Entire message is thinking — no need for wrapper tags
            text_segments.append(combined)

    return "\n".join(text_segments)
