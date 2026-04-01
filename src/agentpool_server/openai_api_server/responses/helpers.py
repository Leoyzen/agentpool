"""OpenAI-compatible responses endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from openai.types.responses import (
    Response,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails


if TYPE_CHECKING:
    from openai.types.responses import ResponseOutputItem

    from agentpool.agents.base_agent import BaseAgent
    from agentpool_server.openai_api_server.responses.models import ResponseRequest


async def handle_request(request: ResponseRequest, agent: BaseAgent[Any, Any]) -> Response:
    from fastapi import HTTPException

    match request.input:
        case str():
            content = request.input
        case list():
            last_msg = request.input[-1]
            msg_content = last_msg["content"]
            if isinstance(msg_content, str):
                content = msg_content
            else:
                text_parts = [p["text"] for p in msg_content if p["type"] == "input_text"]
                content = "\n".join(text_parts)
        case _:
            raise HTTPException(400, "Invalid input format")

    message = await agent.run(content)
    text = ResponseOutputText(text=str(message.content), annotations=[], type="output_text")
    output_msg_id = f"msg_{uuid4().hex}"
    output_msg = ResponseOutputMessage(
        id=output_msg_id,
        role="assistant",
        content=[text],
        status="completed",
        type="message",
    )

    calls = [
        ResponseFunctionToolCall(
            type="function_call",
            arguments=str(tc.args),
            call_id=tc.tool_call_id,
            name=tc.tool_name,
        )
        for tc in message.get_tool_calls()
    ]
    output: list[ResponseOutputItem] = [*calls, output_msg]

    usage_info = ResponseUsage(
        input_tokens=message.usage.input_tokens,
        input_tokens_details=InputTokensDetails(cached_tokens=0),
        output_tokens=message.usage.output_tokens,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        total_tokens=message.usage.total_tokens,
    )

    return Response(
        id=f"resp_{uuid4().hex}",
        created_at=int(datetime.now().timestamp()),
        model=request.model,
        object="response",
        output=output,
        parallel_tool_calls=True,
        tool_choice="auto",
        tools=[],
        status="completed",
        instructions=request.instructions,
        max_output_tokens=request.max_output_tokens,
        temperature=request.temperature,
        usage=usage_info,
        metadata=request.metadata,
    )
