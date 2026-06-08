"""Unit tests for SessionPool message history API (Migration B).

Tests get_messages, append_message, truncate_messages, and copy_messages.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import SessionPool


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool with a mocked StorageManager."""
    pool = MagicMock()
    pool.storage = MagicMock()
    pool.storage.get_session_messages = AsyncMock(return_value=[])
    pool.storage.log_message = AsyncMock(return_value=None)
    pool.storage.fork_conversation = AsyncMock(return_value=None)
    pool.storage.truncate_messages = AsyncMock(return_value=0)
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


@pytest.fixture
def session_pool(mock_pool: MagicMock) -> SessionPool:
    """Return a SessionPool backed by the mock pool."""
    return SessionPool(pool=mock_pool)


@pytest.fixture
def sample_message() -> ChatMessage[str]:
    """Return a sample ChatMessage for testing."""
    return ChatMessage(content="hello", role="user", session_id="sess-1")


# ---------------------------------------------------------------------------
# get_messages
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_messages_returns_storage_messages(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """get_messages forwards to storage and returns the result."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    msg = ChatMessage(content="hi", role="user", session_id="sess-1")
    mock_pool.storage.get_session_messages = AsyncMock(return_value=[msg])

    result = await session_pool.get_messages("sess-1")

    assert result == [msg]
    mock_pool.storage.get_session_messages.assert_awaited_once_with("sess-1")


@pytest.mark.anyio
async def test_get_messages_raises_keyerror_for_missing_session(
    session_pool: SessionPool,
) -> None:
    """get_messages raises KeyError when the session does not exist."""
    with pytest.raises(KeyError, match="missing-sess"):
        await session_pool.get_messages("missing-sess")


@pytest.mark.anyio
async def test_get_messages_empty_when_no_storage(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """get_messages returns an empty list when storage is None."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    mock_pool.storage = None

    result = await session_pool.get_messages("sess-1")

    assert result == []


# ---------------------------------------------------------------------------
# append_message
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_append_message_logs_to_storage(
    session_pool: SessionPool,
    mock_pool: MagicMock,
    sample_message: ChatMessage[str],
) -> None:
    """append_message forwards to storage.log_message and returns the message ID."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")

    result = await session_pool.append_message("sess-1", sample_message)

    assert result == sample_message.message_id
    mock_pool.storage.log_message.assert_awaited_once_with(message=sample_message)


@pytest.mark.anyio
async def test_append_message_raises_keyerror_for_missing_session(
    session_pool: SessionPool,
    sample_message: ChatMessage[str],
) -> None:
    """append_message raises KeyError when the session does not exist."""
    with pytest.raises(KeyError, match="missing-sess"):
        await session_pool.append_message("missing-sess", sample_message)


@pytest.mark.anyio
async def test_append_message_without_storage(
    session_pool: SessionPool,
    mock_pool: MagicMock,
    sample_message: ChatMessage[str],
) -> None:
    """append_message returns message_id even when storage is None."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    mock_pool.storage = None

    result = await session_pool.append_message("sess-1", sample_message)

    assert result == sample_message.message_id


# ---------------------------------------------------------------------------
# copy_messages
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_copy_messages_forks_via_storage(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """copy_messages forwards to storage.fork_conversation and returns fork point."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    await session_pool.sessions.get_or_create_session("sess-2", agent_name="agent-a")
    mock_pool.storage.fork_conversation = AsyncMock(return_value="fork-point-id")

    result = await session_pool.copy_messages("sess-1", "sess-2")

    assert result == "fork-point-id"
    mock_pool.storage.fork_conversation.assert_awaited_once_with(
        source_session_id="sess-1",
        new_session_id="sess-2",
        fork_from_message_id=None,
    )


@pytest.mark.anyio
async def test_copy_messages_with_up_to_message_id(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """copy_messages passes up_to_message_id as fork_from_message_id."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    await session_pool.sessions.get_or_create_session("sess-2", agent_name="agent-a")
    mock_pool.storage.fork_conversation = AsyncMock(return_value="msg-123")

    result = await session_pool.copy_messages("sess-1", "sess-2", up_to_message_id="msg-123")

    assert result == "msg-123"
    mock_pool.storage.fork_conversation.assert_awaited_once_with(
        source_session_id="sess-1",
        new_session_id="sess-2",
        fork_from_message_id="msg-123",
    )


@pytest.mark.anyio
async def test_copy_messages_raises_keyerror_for_missing_source(
    session_pool: SessionPool,
) -> None:
    """copy_messages raises KeyError when the source session does not exist."""
    await session_pool.sessions.get_or_create_session("sess-2", agent_name="agent-a")

    with pytest.raises(KeyError, match="missing-source"):
        await session_pool.copy_messages("missing-source", "sess-2")


@pytest.mark.anyio
async def test_copy_messages_raises_keyerror_for_missing_target(
    session_pool: SessionPool,
) -> None:
    """copy_messages raises KeyError when the target session does not exist."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")

    with pytest.raises(KeyError, match="missing-target"):
        await session_pool.copy_messages("sess-1", "missing-target")


@pytest.mark.anyio
async def test_copy_messages_without_storage(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """copy_messages returns None when storage is None."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    await session_pool.sessions.get_or_create_session("sess-2", agent_name="agent-a")
    mock_pool.storage = None

    result = await session_pool.copy_messages("sess-1", "sess-2")

    assert result is None


# ---------------------------------------------------------------------------
# truncate_messages
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_truncate_messages_calls_storage(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """truncate_messages forwards to storage.truncate_messages and returns count."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    mock_pool.storage.truncate_messages = AsyncMock(return_value=3)

    result = await session_pool.truncate_messages("sess-1", "msg-456")

    assert result == 3
    mock_pool.storage.truncate_messages.assert_awaited_once_with("sess-1", "msg-456")


@pytest.mark.anyio
async def test_truncate_messages_raises_keyerror_for_missing_session(
    session_pool: SessionPool,
) -> None:
    """truncate_messages raises KeyError when the session does not exist."""
    with pytest.raises(KeyError, match="missing-sess"):
        await session_pool.truncate_messages("missing-sess", "msg-123")


@pytest.mark.anyio
async def test_truncate_messages_without_storage(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """truncate_messages returns 0 when storage is None."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    mock_pool.storage = None

    result = await session_pool.truncate_messages("sess-1", "msg-123")

    assert result == 0


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cache_get_messages_returns_cached_data(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """Second call to get_messages uses cache; storage is only hit once."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    msg = ChatMessage(content="cached", role="user", session_id="sess-1")
    mock_pool.storage.get_session_messages = AsyncMock(return_value=[msg])

    first = await session_pool.get_messages("sess-1")
    second = await session_pool.get_messages("sess-1")

    assert first == [msg]
    assert second == [msg]
    mock_pool.storage.get_session_messages.assert_awaited_once_with("sess-1")


@pytest.mark.anyio
async def test_cache_append_message_invalidates_cache(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """append_message invalidates cache so the next get_messages hits storage."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    original = ChatMessage(content="original", role="user", session_id="sess-1")
    updated = ChatMessage(content="updated", role="assistant", session_id="sess-1")
    mock_pool.storage.get_session_messages = AsyncMock(side_effect=[[original], [original, updated]])

    first = await session_pool.get_messages("sess-1")
    assert first == [original]

    await session_pool.append_message("sess-1", updated)

    second = await session_pool.get_messages("sess-1")
    assert second == [original, updated]
    assert mock_pool.storage.get_session_messages.await_count == 2


@pytest.mark.anyio
async def test_cache_truncate_messages_invalidates_cache(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """truncate_messages invalidates cache so the next get_messages hits storage."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    original = ChatMessage(content="original", role="user", session_id="sess-1")
    truncated = ChatMessage(content="truncated", role="user", session_id="sess-1")
    mock_pool.storage.get_session_messages = AsyncMock(side_effect=[[original], [truncated]])

    first = await session_pool.get_messages("sess-1")
    assert first == [original]

    await session_pool.truncate_messages("sess-1", "msg-123")

    second = await session_pool.get_messages("sess-1")
    assert second == [truncated]
    assert mock_pool.storage.get_session_messages.await_count == 2


@pytest.mark.anyio
async def test_cache_copy_messages_invalidates_target_cache(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """copy_messages invalidates target session cache."""
    await session_pool.sessions.get_or_create_session("sess-1", agent_name="agent-a")
    await session_pool.sessions.get_or_create_session("sess-2", agent_name="agent-a")
    target_before = ChatMessage(content="before", role="user", session_id="sess-2")
    target_after = ChatMessage(content="after", role="user", session_id="sess-2")
    mock_pool.storage.get_session_messages = AsyncMock(side_effect=[[target_before], [target_after]])

    first = await session_pool.get_messages("sess-2")
    assert first == [target_before]

    await session_pool.copy_messages("sess-1", "sess-2")

    second = await session_pool.get_messages("sess-2")
    assert second == [target_after]
    assert mock_pool.storage.get_session_messages.await_count == 2
