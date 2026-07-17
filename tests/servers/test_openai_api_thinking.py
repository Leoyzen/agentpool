"""Tests for ThinkingPart handling in the OpenAI-compatible API server.

Verifies that thinking/reasoning content is preserved in:
1. Non-streaming chat completions (reasoning_content field)
2. Streaming chat completions (reasoning_content delta in SSE)
3. Responses API (ResponseOutputReasoning in output)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic_ai import (
    ModelResponse,
    RequestUsage,
    TextPart as PydanticTextPart,
    ThinkingPart as PydanticThinkingPart,
)

from agentpool.messaging.messages import ChatMessage
from agentpool_server.openai_api_server.completions.helpers import stream_response
from agentpool_server.openai_api_server.completions.models import ChatCompletionRequest
from agentpool_server.openai_api_server.responses.helpers import handle_request
from agentpool_server.openai_api_server.responses.models import (
    ResponseOutputReasoning,
    ResponseRequest,
)

_DONE = "[DONE]"


def _make_assistant_with_thinking(
    thinking_content: str = "Let me reason about this.",
    text_content: str = "The answer is 42.",
) -> ChatMessage[str]:
    """Create a ChatMessage with both ThinkingPart and TextPart."""
    timestamp = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    model_response = ModelResponse(
        parts=[
            PydanticThinkingPart(content=thinking_content),
            PydanticTextPart(content=text_content),
        ],
        usage=RequestUsage(),
        model_name="test-model",
        timestamp=timestamp,
    )
    return ChatMessage(
        content=text_content,
        role="assistant",
        message_id="msg-test-1",
        session_id="session-test-1",
        timestamp=timestamp,
        messages=[model_response],
        usage=RequestUsage(),
        model_name="test-model",
        provider_name="test-provider",
        finish_reason="stop",
    )


# =============================================================================
# Non-streaming completions: reasoning_content field
# =============================================================================


def test_non_streaming_completion_includes_reasoning_content():
    """Given a ChatMessage with ThinkingPart, When building a non-streaming
    completion response, Then reasoning_content should be populated."""
    from agentpool_server.openai_api_server.completions.models import OpenAIMessage

    msg = _make_assistant_with_thinking(
        thinking_content="Deep reasoning here.",
        text_content="Final answer.",
    )

    # Extract reasoning content the same way server.py does
    from pydantic_ai import ThinkingPart

    reasoning_content: str | None = None
    for model_msg in msg.messages:
        if isinstance(model_msg, dict):
            continue
        for part in model_msg.parts:
            if isinstance(part, ThinkingPart) and part.content:
                reasoning_content = (reasoning_content or "") + part.content

    openai_msg = OpenAIMessage(
        role="assistant",
        content=str(msg.content),
        reasoning_content=reasoning_content,
    )

    assert openai_msg.reasoning_content == "Deep reasoning here."
    assert openai_msg.content == "Final answer."


def test_non_streaming_completion_no_thinking_no_reasoning_content():
    """Given a ChatMessage without ThinkingPart, When building a non-streaming
    completion response, Then reasoning_content should be None."""
    from agentpool_server.openai_api_server.completions.models import OpenAIMessage

    timestamp = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    model_response = ModelResponse(
        parts=[PydanticTextPart(content="No thinking here.")],
        usage=RequestUsage(),
        model_name="test-model",
        timestamp=timestamp,
    )
    msg = ChatMessage(
        content="No thinking here.",
        role="assistant",
        message_id="msg-no-think",
        session_id="s1",
        timestamp=timestamp,
        messages=[model_response],
        usage=RequestUsage(),
        model_name="test-model",
        provider_name="test-provider",
        finish_reason="stop",
    )

    from pydantic_ai import ThinkingPart

    reasoning_content: str | None = None
    for model_msg in msg.messages:
        if isinstance(model_msg, dict):
            continue
        for part in model_msg.parts:
            if isinstance(part, ThinkingPart) and part.content:
                reasoning_content = (reasoning_content or "") + part.content

    openai_msg = OpenAIMessage(
        role="assistant",
        content=str(msg.content),
        reasoning_content=reasoning_content,
    )

    assert openai_msg.reasoning_content is None


# =============================================================================
# Streaming completions: reasoning_content delta in SSE
# =============================================================================


async def _collect_stream_chunks(
    events: Any,
    request: ChatCompletionRequest,
) -> list[dict[str, Any]]:
    """Collect all SSE chunks from stream_response as parsed dicts."""
    import anyenv

    chunks: list[dict[str, Any]] = []
    async for sse_data in stream_response(events, request):
        stripped = sse_data.strip()
        if not stripped.startswith("data: "):
            continue
        payload = stripped[6:]
        if payload == _DONE:
            continue
        chunks.append(anyenv.load_json(payload, return_type=dict))
    return chunks


@pytest.mark.asyncio
async def test_streaming_includes_reasoning_content_delta():
    """Given a stream with ThinkingPartDelta events, When streaming response,
    Then reasoning_content deltas should appear in SSE chunks."""
    from pydantic_ai import PartDeltaEvent, TextPartDelta, ThinkingPartDelta

    async def event_stream():
        yield PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="Reasoning..."))
        yield PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=" more"))
        yield PartDeltaEvent(index=1, delta=TextPartDelta(content_delta="Answer"))

    request = ChatCompletionRequest(model="test-model", messages=[])
    chunks = await _collect_stream_chunks(event_stream(), request)

    # Find chunks with reasoning_content delta
    reasoning_chunks = [
        c
        for c in chunks
        if "reasoning_content" in c.get("choices", [{}])[0].get("delta", {})
    ]
    text_chunks = [
        c
        for c in chunks
        if "content" in c.get("choices", [{}])[0].get("delta", {})
    ]

    assert len(reasoning_chunks) == 2, (
        f"Expected 2 reasoning chunks, got {len(reasoning_chunks)}. "
        f"All deltas: {[c['choices'][0]['delta'] for c in chunks]}"
    )
    assert reasoning_chunks[0]["choices"][0]["delta"]["reasoning_content"] == "Reasoning..."
    assert reasoning_chunks[1]["choices"][0]["delta"]["reasoning_content"] == " more"

    assert len(text_chunks) == 1
    assert text_chunks[0]["choices"][0]["delta"]["content"] == "Answer"


# =============================================================================
# Responses API: ResponseOutputReasoning in output
# =============================================================================


@pytest.mark.asyncio
async def test_responses_api_includes_reasoning_output():
    """Given a ChatMessage with ThinkingPart, When using the responses API,
    Then a ResponseOutputReasoning should be in the output list."""
    msg = _make_assistant_with_thinking(
        thinking_content="Reasoning for responses API.",
        text_content="Response text.",
    )
    request = ResponseRequest(model="test-model", input="test")
    response = await handle_request(request, msg)

    reasoning_outputs = [
        o for o in response.output if isinstance(o, ResponseOutputReasoning)
    ]
    assert len(reasoning_outputs) == 1, (
        f"Expected 1 ResponseOutputReasoning, got {len(reasoning_outputs)}. "
        f"Output types: {[type(o).__name__ for o in response.output]}"
    )
    assert reasoning_outputs[0].content == "Reasoning for responses API."
    assert reasoning_outputs[0].type == "reasoning"


@pytest.mark.asyncio
async def test_responses_api_no_thinking_no_reasoning_output():
    """Given a ChatMessage without ThinkingPart, When using the responses API,
    Then no ResponseOutputReasoning should be in the output list."""
    timestamp = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    model_response = ModelResponse(
        parts=[PydanticTextPart(content="No thinking.")],
        usage=RequestUsage(),
        model_name="test-model",
        timestamp=timestamp,
    )
    msg = ChatMessage(
        content="No thinking.",
        role="assistant",
        message_id="msg-no-think",
        session_id="s1",
        timestamp=timestamp,
        messages=[model_response],
        usage=RequestUsage(),
        model_name="test-model",
        provider_name="test-provider",
        finish_reason="stop",
    )
    request = ResponseRequest(model="test-model", input="test")
    response = await handle_request(request, msg)

    reasoning_outputs = [
        o for o in response.output if isinstance(o, ResponseOutputReasoning)
    ]
    assert len(reasoning_outputs) == 0
