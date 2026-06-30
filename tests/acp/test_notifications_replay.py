"""Unit tests for ACPNotifications.replay() method."""

from __future__ import annotations

from unittest.mock import AsyncMock

from pydantic_ai import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.messages import BinaryContent
import pytest

from acp.agent.notifications import ACPNotifications
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AudioContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    SessionNotification,
    TextContentBlock,
    ToolCallStart,
    UserMessageChunk,
)


@pytest.fixture
def mock_client():
    """Create a mock ACP client that captures sent notifications."""
    client = AsyncMock()
    client.session_update = AsyncMock()
    client.ext_notification = AsyncMock()
    return client


@pytest.fixture
def notifications(mock_client):
    """Create an ACPNotifications instance with a mock client."""
    return ACPNotifications(client=mock_client, session_id="test-session")


@pytest.fixture
def batch_notifications(mock_client):
    """Create an ACPNotifications with batch support enabled."""
    n = ACPNotifications(client=mock_client, session_id="test-session")
    n.set_batch_support(True)
    return n


@pytest.mark.unit
async def test_replay_model_request_with_user_prompt_part(notifications, mock_client):
    """Test replay of ModelRequest with simple UserPromptPart (string content)."""
    messages = [ModelRequest(parts=[UserPromptPart(content="Hello, assistant!")])]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 1
    call_args = mock_client.session_update.call_args[0][0]
    assert isinstance(call_args, SessionNotification)
    assert call_args.session_id == "test-session"
    assert isinstance(call_args.update, UserMessageChunk)
    assert isinstance(call_args.update.content, TextContentBlock)
    assert call_args.update.content.text == "Hello, assistant!"


@pytest.mark.unit
async def test_replay_model_response_with_text_part(notifications, mock_client):
    """Test replay of ModelResponse with TextPart."""
    messages = [ModelResponse(parts=[TextPart(content="Hello, user!")])]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 1
    call_args = mock_client.session_update.call_args[0][0]
    assert isinstance(call_args, SessionNotification)
    assert isinstance(call_args.update, AgentMessageChunk)
    assert isinstance(call_args.update.content, TextContentBlock)
    assert call_args.update.content.text == "Hello, user!"


@pytest.mark.unit
async def test_replay_model_response_with_thinking_part(notifications, mock_client):
    """Test replay of ModelResponse with ThinkingPart."""
    messages = [ModelResponse(parts=[ThinkingPart(content="Let me think...")])]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 1
    call_args = mock_client.session_update.call_args[0][0]
    assert isinstance(call_args, SessionNotification)
    assert isinstance(call_args.update, AgentThoughtChunk)
    assert isinstance(call_args.update.content, TextContentBlock)
    assert call_args.update.content.text == "Let me think..."


@pytest.mark.unit
async def test_replay_model_response_with_tool_call_part(notifications, mock_client):
    """Test replay of ModelResponse with ToolCallPart sends tool_call_start."""
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_call_id="tc-1",
                    tool_name="read_file",
                    args={"path": "/tmp/test.txt"},
                )
            ]
        )
    ]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 1
    call_args = mock_client.session_update.call_args[0][0]
    assert isinstance(call_args, SessionNotification)
    assert isinstance(call_args.update, ToolCallStart)
    assert call_args.update.tool_call_id == "tc-1"
    assert call_args.update.title == "Reading: /tmp/test.txt"
    assert call_args.update.raw_input == {"path": "/tmp/test.txt"}


@pytest.mark.unit
async def test_replay_model_request_with_tool_return_part(notifications, mock_client):
    """Test replay of ModelRequest with ToolReturnPart sends tool_call_update."""
    # First, simulate a tool call was stored
    notifications._tool_call_inputs["tc-1"] = {"path": "/tmp/test.txt"}

    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_call_id="tc-1",
                    tool_name="read_file",
                    content="file contents",
                )
            ]
        )
    ]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 1
    call_args = mock_client.session_update.call_args[0][0]
    assert isinstance(call_args, SessionNotification)
    assert call_args.update.session_update == "tool_call_update"
    assert call_args.update.tool_call_id == "tc-1"
    assert call_args.update.status == "completed"
    assert call_args.update.title == "Reading: /tmp/test.txt"


@pytest.mark.unit
async def test_replay_mixed_messages(notifications, mock_client):
    """Test replay of mixed ModelRequest and ModelResponse messages."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content="User message 1")]),
        ModelResponse(parts=[TextPart(content="Agent response 1")]),
        ModelRequest(parts=[UserPromptPart(content="User message 2")]),
        ModelResponse(parts=[TextPart(content="Agent response 2")]),
    ]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 4
    updates = [call[0][0].update for call in mock_client.session_update.call_args_list]

    assert isinstance(updates[0], UserMessageChunk)
    assert isinstance(updates[0].content, TextContentBlock)
    assert updates[0].content.text == "User message 1"

    assert isinstance(updates[1], AgentMessageChunk)
    assert isinstance(updates[1].content, TextContentBlock)
    assert updates[1].content.text == "Agent response 1"

    assert isinstance(updates[2], UserMessageChunk)
    assert isinstance(updates[2].content, TextContentBlock)
    assert updates[2].content.text == "User message 2"

    assert isinstance(updates[3], AgentMessageChunk)
    assert isinstance(updates[3].content, TextContentBlock)
    assert updates[3].content.text == "Agent response 2"


@pytest.mark.unit
async def test_replay_empty_messages(notifications, mock_client):
    """Test replay with empty messages list sends no notifications."""
    messages = []

    await notifications.replay(messages)

    mock_client.session_update.assert_not_awaited()


@pytest.mark.unit
async def test_replay_user_prompt_with_image(notifications, mock_client):
    """Test replay of ModelRequest with UserPromptPart containing image BinaryContent."""
    image_content = BinaryContent(data=b"fake-image-data", media_type="image/png")
    messages = [ModelRequest(parts=[UserPromptPart(content=[image_content])])]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 1
    call_args = mock_client.session_update.call_args[0][0]
    assert isinstance(call_args, SessionNotification)
    assert isinstance(call_args.update, UserMessageChunk)
    assert isinstance(call_args.update.content, ImageContentBlock)
    assert call_args.update.content.mime_type == "image/png"


@pytest.mark.unit
async def test_replay_user_prompt_with_audio(notifications, mock_client):
    """Test replay of ModelRequest with UserPromptPart containing audio BinaryContent."""
    audio_content = BinaryContent(data=b"fake-audio-data", media_type="audio/wav")
    messages = [ModelRequest(parts=[UserPromptPart(content=[audio_content])])]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 1
    call_args = mock_client.session_update.call_args[0][0]
    assert isinstance(call_args, SessionNotification)
    assert isinstance(call_args.update, UserMessageChunk)
    assert isinstance(call_args.update.content, AudioContentBlock)
    assert call_args.update.content.mime_type == "audio/wav"


@pytest.mark.unit
async def test_replay_user_prompt_with_resource(notifications, mock_client):
    """Test replay of ModelRequest with UserPromptPart containing resource URL."""
    from pydantic_ai.messages import DocumentUrl

    doc = DocumentUrl(url="https://example.com/doc.pdf", media_type="application/pdf")
    messages = [ModelRequest(parts=[UserPromptPart(content=[doc])])]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 1
    call_args = mock_client.session_update.call_args[0][0]
    assert isinstance(call_args, SessionNotification)
    assert isinstance(call_args.update, UserMessageChunk)
    assert isinstance(call_args.update.content, ResourceContentBlock)
    assert call_args.update.content.uri == "https://example.com/doc.pdf"
    assert call_args.update.content.name == "doc.pdf"


@pytest.mark.unit
async def test_replay_tool_return_with_string_content(notifications, mock_client):
    """Test replay of ToolReturnPart with simple string content."""
    notifications._tool_call_inputs["tc-1"] = {"path": "/tmp/test.txt"}

    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_call_id="tc-1",
                    tool_name="read_file",
                    content="file contents here",
                )
            ]
        )
    ]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 1
    call_args = mock_client.session_update.call_args[0][0]
    assert isinstance(call_args, SessionNotification)
    assert call_args.update.session_update == "tool_call_update"
    assert call_args.update.tool_call_id == "tc-1"
    assert call_args.update.status == "completed"


@pytest.mark.unit
async def test_replay_tool_return_with_path_location(notifications, mock_client):
    """Test replay of ToolReturnPart generates correct file locations."""
    notifications._tool_call_inputs["tc-1"] = {"file_path": "/home/user/doc.md"}

    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_call_id="tc-1",
                    tool_name="edit",
                    content="edited content",
                )
            ]
        )
    ]

    await notifications.replay(messages)

    call_args = mock_client.session_update.call_args[0][0]
    assert call_args.update.locations is not None
    assert len(call_args.update.locations) == 1
    assert call_args.update.locations[0].path == "/home/user/doc.md"


@pytest.mark.unit
async def test_replay_multiple_response_parts(notifications, mock_client):
    """Test replay of ModelResponse with multiple parts (text + thinking + tool call)."""
    messages = [
        ModelResponse(
            parts=[
                TextPart(content="Let me help you"),
                ThinkingPart(content="I should use a tool"),
                ToolCallPart(
                    tool_call_id="tc-2",
                    tool_name="bash",
                    args={"command": "ls -la"},
                ),
            ]
        )
    ]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 3
    updates = [call[0][0].update for call in mock_client.session_update.call_args_list]

    assert isinstance(updates[0], AgentMessageChunk)
    assert isinstance(updates[0].content, TextContentBlock)
    assert updates[0].content.text == "Let me help you"

    assert isinstance(updates[1], AgentThoughtChunk)
    assert isinstance(updates[1].content, TextContentBlock)
    assert updates[1].content.text == "I should use a tool"

    assert isinstance(updates[2], ToolCallStart)
    assert updates[2].tool_call_id == "tc-2"
    assert updates[2].title == "Running: ls -la"


@pytest.mark.unit
async def test_replay_multiple_request_parts(notifications, mock_client):
    """Test replay of ModelRequest with multiple UserPromptParts."""
    messages = [
        ModelRequest(
            parts=[
                UserPromptPart(content="First message"),
                UserPromptPart(content="Second message"),
            ]
        )
    ]

    await notifications.replay(messages)

    assert mock_client.session_update.call_count == 2
    updates = [call[0][0].update for call in mock_client.session_update.call_args_list]

    assert isinstance(updates[0], UserMessageChunk)
    assert isinstance(updates[0].content, TextContentBlock)
    assert updates[0].content.text == "First message"

    assert isinstance(updates[1], UserMessageChunk)
    assert isinstance(updates[1].content, TextContentBlock)
    assert updates[1].content.text == "Second message"


@pytest.mark.unit
async def test_replay_batch_mode_sends_ext_notification(batch_notifications, mock_client):
    """Batch-capable client receives _batch_session_updates, not session/update."""
    messages = [ModelRequest(parts=[UserPromptPart(content="Hello")])]

    await batch_notifications.replay(messages)

    mock_client.session_update.assert_not_awaited()
    mock_client.ext_notification.assert_awaited_once()
    method, params = mock_client.ext_notification.call_args[0]
    assert method == "_batch_session_updates"
    assert params["session_id"] == "test-session"
    assert len(params["updates"]) == 1


@pytest.mark.unit
async def test_replay_fallback_mode_sends_session_update(notifications, mock_client):
    """Non-capable client falls back to sequential session/update."""
    messages = [ModelRequest(parts=[UserPromptPart(content="Hello")])]

    await notifications.replay(messages)

    mock_client.ext_notification.assert_not_awaited()
    assert mock_client.session_update.call_count == 1


@pytest.mark.unit
async def test_replay_batch_preserves_tool_call_ordering(batch_notifications, mock_client):
    """ToolCallStart must appear before ToolCallProgress in the batch."""
    messages = [
        ModelResponse(
            parts=[
                TextPart(content="Let me help"),
                ToolCallPart(
                    tool_call_id="tc-1",
                    tool_name="read_file",
                    args={"path": "/tmp/test.txt"},
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_call_id="tc-1",
                    tool_name="read_file",
                    content="file contents",
                )
            ]
        ),
    ]

    await batch_notifications.replay(messages)

    mock_client.ext_notification.assert_awaited_once()
    _, params = mock_client.ext_notification.call_args[0]
    updates = params["updates"]
    assert len(updates) == 3
    assert updates[0]["sessionUpdate"] == "agent_message_chunk"
    assert updates[1]["sessionUpdate"] == "tool_call"
    assert updates[1]["toolCallId"] == "tc-1"
    assert updates[2]["sessionUpdate"] == "tool_call_update"
    assert updates[2]["toolCallId"] == "tc-1"


@pytest.mark.unit
async def test_replay_batch_custom_size(mock_client):
    """Custom notification_batch_size produces correct chunk count."""
    n = ACPNotifications(client=mock_client, session_id="test", notification_batch_size=5)
    n.set_batch_support(True)
    messages = [ModelRequest(parts=[UserPromptPart(content=f"msg {i}")]) for i in range(12)]

    await n.replay(messages)

    assert mock_client.ext_notification.call_count == 3
    batch_sizes = [
        len(call[0][1]["updates"]) for call in mock_client.ext_notification.call_args_list
    ]
    assert batch_sizes == [5, 5, 2]


@pytest.mark.unit
async def test_collect_request_updates_is_pure(notifications, mock_client):
    """_collect_request_updates returns list without calling client methods."""
    request = ModelRequest(parts=[UserPromptPart(content="Hello")])

    updates = notifications._collect_request_updates(request)

    assert len(updates) == 1
    assert isinstance(updates[0], UserMessageChunk)
    mock_client.session_update.assert_not_awaited()
    mock_client.ext_notification.assert_not_awaited()


@pytest.mark.unit
async def test_replay_empty_messages_batch_mode(batch_notifications, mock_client):
    """Empty messages list produces zero notifications in batch mode."""
    await batch_notifications.replay([])

    mock_client.ext_notification.assert_not_awaited()
    mock_client.session_update.assert_not_awaited()


@pytest.mark.unit
async def test_init_rejects_non_positive_batch_size(mock_client):
    """notification_batch_size must be greater than 0."""
    with pytest.raises(ValueError, match="notification_batch_size must be greater than 0"):
        ACPNotifications(mock_client, "session-1", notification_batch_size=0)

    with pytest.raises(ValueError, match="notification_batch_size must be greater than 0"):
        ACPNotifications(mock_client, "session-1", notification_batch_size=-1)


@pytest.mark.unit
async def test_init_rejects_negative_flush_interval(mock_client):
    """notification_flush_interval must be non-negative."""
    with pytest.raises(ValueError, match="notification_flush_interval must be non-negative"):
        ACPNotifications(mock_client, "session-1", notification_flush_interval=-0.1)
