"""Auto conversation ingestion helpers for VikingCapability.

Provides pure-function message sanitization and conversation-pair extraction,
plus an async ingestion coroutine that writes conversation turns to a Viking
session via the SDK client.

These helpers are called from ``VikingCapability._handle_auto_ingest()`` during
``before_model_request`` (lazy ingestion of the previous turn) and flushed
synchronously in ``after_run()``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Sequence


# Regex patterns for stripping injected XML blocks.
# ``re.DOTALL`` ensures the pattern matches across newlines.
_RECALL_RE = re.compile(r"<openviking-recall>.*?</openviking-recall>", re.DOTALL)
_PROFILE_RE = re.compile(r"<openviking-profile>.*?</openviking-profile>", re.DOTALL)

_REPLACEMENT = "[recalled context omitted]"


def _sanitize_message(content: str, enabled: bool = True) -> str:
    """Strip injected Viking XML blocks from message content.

    Replaces ``<openviking-recall>...</openviking-recall>`` and
    ``<openviking-profile>...</openviking-profile>`` blocks with
    ``[recalled context omitted]`` to prevent feedback loops where
    recalled context is re-ingested as original conversation.

    Args:
        content: The raw message content string.
        enabled: When ``False``, return the content unchanged.

    Returns:
        The sanitized content string.
    """
    if not enabled:
        return content
    sanitized = _RECALL_RE.sub(_REPLACEMENT, content)
    return _PROFILE_RE.sub(_REPLACEMENT, sanitized)


def _extract_conversation_pairs(
    messages: Sequence[Any],
    start_idx: int,
) -> list[dict[str, str]]:
    """Extract user/assistant conversation pairs from model messages.

    Scans ``messages`` starting at ``start_idx``, extracting text content
    from ``ModelRequest`` (user prompts) and ``ModelResponse`` (assistant
    text) objects. Only string content is extracted — binary content and
    tool calls are skipped.

    Args:
        messages: Sequence of ``ModelRequest`` / ``ModelResponse`` objects.
        start_idx: Index to start scanning from (inclusive).

    Returns:
        A list of ``{"role": "user"|"assistant", "content": str}`` dicts
        representing the conversation since ``start_idx``.
    """
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    pairs: list[dict[str, str]] = []
    for msg in messages[start_idx:]:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    content = part.content
                    if isinstance(content, str):
                        pairs.append({"role": "user", "content": content})
        elif isinstance(msg, ModelResponse):
            text_parts = [p for p in msg.parts if isinstance(p, TextPart)]
            if text_parts:
                combined = "\n".join(p.content for p in text_parts)
                pairs.append({"role": "assistant", "content": combined})
    return pairs


async def _ingest_conversation(
    client: Any,
    messages: list[dict[str, str]],
    *,
    session_id: str,
    source_type: str = "wolfharness",
    keep_recent_turns: int = 0,
) -> None:
    """Write conversation messages to a Viking session.

    Creates a new session, adds each message, and commits with the
    configured retention policy. Errors are caught and logged by the
    caller — this function raises on failure.

    Args:
        client: The Viking SDK ``AsyncHTTPClient`` instance.
        messages: Conversation pairs from ``_extract_conversation_pairs``.
        session_id: Viking session ID for the new session.
        source_type: Source type metadata for the session.
        keep_recent_turns: Number of recent turns to retain after commit.
            When 0, no retention parameter is passed to ``commit_session``.
    """
    await client.create_session(session_id=session_id)
    for msg in messages:
        await client.add_message(session_id, msg["role"], msg["content"])
    commit_kwargs: dict[str, Any] = {}
    if keep_recent_turns > 0:
        commit_kwargs["keep_recent_turn_count"] = keep_recent_turns
    await client.commit_session(session_id, **commit_kwargs)
