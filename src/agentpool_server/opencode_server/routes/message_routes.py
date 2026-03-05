"""Message routes."""

from __future__ import annotations

import contextlib
from typing import Any, assert_never

from fastapi import APIRouter, HTTPException, Query, status

from agentpool.log import get_logger
from agentpool.utils import identifiers as identifier
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.converters import extract_user_prompt_from_parts
from agentpool_server.opencode_server.dependencies import StateDep
from agentpool_server.opencode_server.models import (
    AgentPartInput,
    AssistantMessage,
    FilePartInput,
    MessagePath,
    MessageRemovedEvent,
    MessageRequest,
    MessageTime,
    MessageUpdatedEvent,
    MessageWithParts,
    Part,
    PartRemovedEvent,
    PartUpdatedEvent,
    SessionIdleEvent,
    SessionStatus,
    SessionStatusEvent,
    SubtaskPartInput,
    TextPartInput,
    TimeCreated,
    TimeCreatedUpdated,
    Tokens,
    UserMessage,
)
from agentpool_server.opencode_server.stream_adapter import OpenCodeStreamAdapter


logger = get_logger(__name__)


router = APIRouter(prefix="/session/{session_id}", tags=["message"])


@router.get("/message")
async def list_messages(
    session_id: str,
    state: StateDep,
    limit: int | None = Query(default=None),
) -> list[MessageWithParts]:
    """List messages in a session."""
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = state.messages.get(session_id, [])
    return messages[-limit:] if limit else messages


async def _process_message(  # noqa: PLR0915
    session_id: str,
    request: MessageRequest,
    state: StateDep,
) -> MessageWithParts:
    """Internal helper to process a message request.

    This does the actual work of creating messages, running the agent,
    and broadcasting events. Used by both sync and async endpoints.
    """
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    # --- Create user message ---
    user_msg_id = identifier.ascending("message", request.message_id)
    user_message = UserMessage(
        id=user_msg_id,
        session_id=session_id,
        time=TimeCreated.now(),
        agent=request.agent or "default",
        model=request.model,
        variant=request.variant,
    )

    user_msg_with_parts = MessageWithParts(info=user_message)
    for part in request.parts:
        match part:
            case TextPartInput(text=text):
                created: Part = user_msg_with_parts.add_text_part(text)
            case FilePartInput(mime=mime, url=url, filename=filename, source=source):
                created = user_msg_with_parts.add_file_part(
                    mime,
                    url,
                    filename=filename,
                    source=source,
                )
            case AgentPartInput(name=name, source=source):
                created = user_msg_with_parts.add_agent_part(name, source=source)
            case SubtaskPartInput(
                prompt=subtask_prompt, description=desc, agent=subtask_agent, model=subtask_model
            ):
                created = user_msg_with_parts.add_subtask_part(
                    subtask_prompt,
                    desc,
                    subtask_agent,
                    model=subtask_model,
                )
            case _ as unreachable:
                assert_never(unreachable)
        await state.broadcast_event(PartUpdatedEvent.create(created))
    state.messages[session_id].append(user_msg_with_parts)
    await state.persist_message_to_storage(user_msg_with_parts, session_id)
    await state.broadcast_event(MessageUpdatedEvent.create(user_message))
    # --- Mark session busy ---
    busy = SessionStatus(type="busy")
    state.session_status[session_id] = busy
    await state.broadcast_event(SessionStatusEvent.create(session_id, busy))
    # --- Extract user prompt ---
    user_prompt = await extract_user_prompt_from_parts(
        request.parts,
        fs=state.fs,
        tools=state.agent.tools,
    )
    # --- Create assistant message ---
    assistant_msg_id = identifier.ascending("message")
    now = now_ms()
    assistant_msg = AssistantMessage(
        id=assistant_msg_id,
        session_id=session_id,
        parent_id=user_msg_id,
        model_id=request.model.model_id if request.model else "default",
        provider_id=request.model.provider_id if request.model else "agentpool",
        mode=request.agent or "default",
        agent=request.agent or "default",
        path=MessagePath(cwd=state.working_dir, root=state.working_dir),
        time=MessageTime(created=now),
    )
    assistant_msg_with_parts = MessageWithParts(info=assistant_msg, parts=[])
    state.messages[session_id].append(assistant_msg_with_parts)
    await state.broadcast_event(MessageUpdatedEvent.create(assistant_msg))
    # Step-start part
    step_start = assistant_msg_with_parts.add_step_start_part()
    await state.broadcast_event(PartUpdatedEvent.create(step_start))
    # --- Resolve agent and variant ---
    agent = state.agent
    if request.agent and state.agent.agent_pool is not None:
        agent = state.agent.agent_pool.all_agents.get(request.agent, state.agent)
    if request.variant:
        with contextlib.suppress(Exception):
            await agent.set_mode(request.variant, category_id="thought_level")

    # --- Stream via adapter ---
    adapter = OpenCodeStreamAdapter(
        assistant_msg=assistant_msg_with_parts,
        working_dir=state.working_dir,
        on_file_paths=state._warmup_lsp_for_files,
    )
    iterator = agent.run_stream(user_prompt, session_id=session_id)
    async for oc_event in adapter.process_stream(iterator):
        await state.broadcast_event(oc_event)

    for oc_event in adapter.finalize():
        await state.broadcast_event(oc_event)

    # --- Finalize assistant message ---
    response_time = now_ms()
    preview = adapter.response_text[:100] if adapter.response_text else "EMPTY"
    logger.info("Response text", text_preview=preview)
    tokens = Tokens.from_pydantic_ai(adapter.usage)
    cost = float(adapter.cost_info.total_cost) if adapter.cost_info else 0.0
    msg_time = MessageTime(created=now, completed=response_time)
    update = {"time": msg_time, "tokens": tokens, "cost": cost}
    updated_assistant = assistant_msg.model_copy(update=update)
    assistant_msg_with_parts.info = updated_assistant
    await state.broadcast_event(MessageUpdatedEvent.create(updated_assistant))
    await state.persist_message_to_storage(assistant_msg_with_parts, session_id)
    # --- Mark session idle ---
    status = SessionStatus(type="idle")
    state.session_status[session_id] = status
    await state.broadcast_event(SessionStatusEvent.create(session_id, status))
    await state.broadcast_event(SessionIdleEvent.create(session_id))
    # --- Update session timestamp ---
    session = state.sessions[session_id]
    state.sessions[session_id] = session.model_copy(
        update={"time": TimeCreatedUpdated(created=session.time.created, updated=response_time)}
    )
    return assistant_msg_with_parts


@router.post("/message")
async def send_message(
    session_id: str,
    request: MessageRequest,
    state: StateDep,
) -> MessageWithParts:
    """Send a message and wait for the agent's response.

    This is the synchronous version - waits for completion before returning.
    For async processing, use POST /session/{id}/prompt_async instead.
    """
    return await _process_message(session_id, request, state)


@router.post("/prompt_async", status_code=status.HTTP_204_NO_CONTENT)
async def send_message_async(session_id: str, request: MessageRequest, state: StateDep) -> None:
    """Send a message asynchronously without waiting for response.

    Starts the agent processing in the background and returns immediately.
    Client should listen to SSE events to get updates.

    Returns 204 No Content immediately.
    """
    # Create background task to process the message
    state.create_background_task(
        _process_message(session_id, request, state),
        name=f"process_message_{session_id}",
    )


@router.get("/message/{message_id}")
async def get_message(session_id: str, message_id: str, state: StateDep) -> MessageWithParts:
    """Get a specific message."""
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    for msg in state.messages.get(session_id, []):
        if msg.info.id == message_id:
            return msg

    raise HTTPException(status_code=404, detail="Message not found")


@router.delete("/message/{message_id}")
async def delete_message(
    session_id: str,
    message_id: str,
    state: StateDep,
) -> bool:
    """Delete a message and all its parts from a session."""
    session = await state.get_or_load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = state.messages.get(session_id, [])
    for i, msg in enumerate(messages):
        if msg.info.id == message_id:
            for part in msg.parts:
                await state.broadcast_event(
                    PartRemovedEvent.create(
                        session_id=session_id,
                        message_id=message_id,
                        part_id=part.id,
                    )
                )
            messages.pop(i)
            await state.broadcast_event(
                MessageRemovedEvent.create(session_id=session_id, message_id=message_id)
            )
            return True
    raise HTTPException(status_code=404, detail="Message not found")


@router.delete("/message/{message_id}/part/{part_id}")
async def delete_part(
    session_id: str,
    message_id: str,
    part_id: str,
    state: StateDep,
) -> bool:
    """Delete a part from a message."""
    for msg in state.messages.get(session_id, []):
        if msg.info.id != message_id:
            continue
        for i, part in enumerate(msg.parts):
            if part.id == part_id:
                msg.parts.pop(i)
                await state.broadcast_event(
                    PartRemovedEvent.create(
                        session_id=session_id,
                        message_id=message_id,
                        part_id=part_id,
                    )
                )
                return True
        raise HTTPException(status_code=404, detail="Part not found")
    raise HTTPException(status_code=404, detail="Message not found")


@router.patch("/message/{message_id}/part/{part_id}")
async def update_part(
    session_id: str,
    message_id: str,
    part_id: str,
    body: dict[str, Any],
    state: StateDep,
) -> Part:
    """Update a part in a message.

    Accepts the full part object and replaces the existing part.
    Returns the updated part.
    """
    for msg in state.messages.get(session_id, []):
        if msg.info.id != message_id:
            continue
        for i, part in enumerate(msg.parts):
            if part.id == part_id:
                # Update the part fields from the body
                updated = part.model_copy(update=body)
                msg.parts[i] = updated
                await state.broadcast_event(PartUpdatedEvent.create(updated))
                return updated
        raise HTTPException(status_code=404, detail="Part not found")
    raise HTTPException(status_code=404, detail="Message not found")
