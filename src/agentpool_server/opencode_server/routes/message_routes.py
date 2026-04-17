"""Message routes."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import contextlib
from typing import TYPE_CHECKING, Any, assert_never

from fastapi import APIRouter, HTTPException, Query, status
from pydantic_ai import UserContent

from agentpool.common_types import PathReference
from agentpool.log import get_logger
from agentpool.utils import identifiers as identifier
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.converters import (
    extract_user_prompt_from_parts,
    opencode_to_chat_message,
)
from agentpool_server.opencode_server.dependencies import StateDep
from agentpool_server.opencode_server.models import (
    AgentPartInput,
    AssistantMessage,
    FilePartInput,
    MessageAbortedError,
    MessageAbortedErrorData,
    MessagePath,
    MessageRequest,
    MessageTime,
    MessageUpdatedEvent,
    MessageWithParts,
    Part,
    PartRemovedEvent,
    PartUpdatedEvent,
    SessionErrorEvent,
    SessionIdleEvent,
    SessionStatus,
    SessionStatusEvent,
    StepStartPart,
    SubtaskPartInput,
    TextPartInput,
    TimeCreated,
    TimeCreatedUpdated,
    Tokens,
    UserMessage,
)
from agentpool_server.opencode_server.routes.session_routes import get_or_load_session
from agentpool_server.opencode_server.stream_adapter import OpenCodeStreamAdapter


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


logger = get_logger(__name__)

# Maximum time (in seconds) to wait for a single agent stream response.
# If the model API hangs without producing any events, this timeout ensures
# the session transitions from "busy" to "idle" instead of getting stuck.
STREAM_TIMEOUT_SECONDS = 600  # 10 minutes


def _warmup_lsp_for_files(state: ServerState, file_paths: list[str]) -> None:
    """Warm up LSP servers for the given file paths.

    This starts LSP servers asynchronously based on file extensions.
    Like OpenCode's LSP.touchFile(), this triggers server startup without waiting.

    Args:
        state: Server state with LSP manager
        file_paths: List of file paths that were accessed
    """
    logger.info("_warmup_lsp_for_files called with", file_paths=file_paths)
    lsp_manager = state.lsp_manager

    async def warmup_files() -> None:
        """Start LSP servers for each file path."""
        logger.info("warmup_files task started")

        servers_started = False
        for path in file_paths:
            # Find appropriate server for this file
            server_info = lsp_manager.get_server_for_file(path)
            if server_info is None:
                continue
            server_id = server_info.id
            if lsp_manager.is_running(server_id):
                logger.info("Server with same id already running", server_id=server_id)
                continue

            # Start server for workspace root
            root_uri = f"file://{state.working_dir}"
            logger.info("Starting server...", server_id=server_id)

    async def warmup() -> None:
        """Run warmup and handle exceptions."""
        try:
            await warmup_files()
        except Exception:
            logger.exception("LSP warmup failed")

    # Fire and forget - don't block message processing
    asyncio.create_task(warmup())


async def _maybe_generate_title(
    state: StateDep,
    session_id: str,
    user_prompt: Sequence[UserContent | PathReference],
) -> None:
    """Generate title for session if this is the first user message.

    Checks if the session only has system/initialization messages (no user messages yet).
    If so, triggers title generation via the storage manager.

    Args:
        state: Server state containing storage manager
        session_id: The session ID to check
        user_prompt: The user's prompt to use for title generation
    """
    # Check if this is the first user message by looking at existing messages
    existing_messages = state.messages.get(session_id, [])

    # Count user messages (not assistant, not system)
    user_message_count = sum(
        1 for msg in existing_messages if hasattr(msg.info, "role") and msg.info.role == "user"
    )

    # Only generate title on first user message
    if user_message_count != 1:
        return

    # Check if storage manager has title generation configured
    storage = state.pool.storage if state.pool else None
    if storage is None:
        return

    # Check if title is already set (not default)
    session = state.sessions.get(session_id)
    if session and session.title and session.title != "New Session":
        return

    try:
        # Convert user_prompt to string for title generation
        # Extract text content from the sequence
        prompt_text_parts: list[str] = []
        for item in user_prompt:
            if isinstance(item, str):
                prompt_text_parts.append(item)
            else:
                # Try to get text attribute, fallback to string representation
                text = getattr(item, "text", None)
                if text:
                    prompt_text_parts.append(str(text))
        prompt_text = " ".join(prompt_text_parts) if prompt_text_parts else ""

        # Trigger title generation via log_session with initial_prompt
        await storage.log_session(
            session_id=session_id,
            node_name=state.agent.name,
            initial_prompt=prompt_text,
            on_title_generated=lambda title: _update_session_title(state, session_id, title),
        )
    except Exception:
        logger.exception("Failed to generate title", session_id=session_id)


def _update_session_title(state: StateDep, session_id: str, title: str) -> None:
    """Update session title in state and storage.

    Args:
        state: Server state
        session_id: The session ID to update
        title: The new title
    """
    import asyncio

    # Update in-memory session
    session = state.sessions.get(session_id)
    if session:
        session.title = title

    # Update in storage (fire and forget)
    async def _update() -> None:
        try:
            await state.pool.storage.update_session_title(session_id, title)
        except Exception:
            logger.exception("Failed to update session title", session_id=session_id)

    # Schedule the async update
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_update())
    except RuntimeError:
        # No event loop running, ignore
        pass


async def persist_message_to_storage(
    state: ServerState,
    msg: MessageWithParts,
    session_id: str,
) -> None:
    """Persist an OpenCode message to storage.

    Converts the OpenCode MessageWithParts to ChatMessage and saves it.

    Args:
        state: Server state with pool reference
        msg: OpenCode message to persist
        session_id: Session/conversation ID
    """
    chat_msg = opencode_to_chat_message(msg, session_id=session_id)
    with contextlib.suppress(Exception):
        await state.storage.log_message(chat_msg)


router = APIRouter(prefix="/session/{session_id}", tags=["message"])


@router.get("/message")
async def list_messages(
    session_id: str,
    state: StateDep,
    limit: int | None = Query(default=None),
) -> list[MessageWithParts]:
    """List messages in a session."""
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = state.messages.get(session_id, [])
    return messages[-limit:] if limit else messages


async def _process_message(
    session_id: str,
    request: MessageRequest,
    state: StateDep,
) -> MessageWithParts:
    """Internal helper to process a message request.

    This does the actual work of creating messages, running the agent,
    and broadcasting events. Used by both sync and async endpoints.

    Per-session locking ensures messages to the same session are processed
    sequentially, preventing race conditions and event interleaving.

    User message is created BEFORE acquiring the lock so that the UI can
    immediately show the message with "QUEUED" status while waiting.
    """
    # --- Create user message BEFORE lock (so UI shows queued status) ---
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    user_msg_id = identifier.ascending("message", request.message_id)
    user_message = UserMessage(
        id=user_msg_id,
        session_id=session_id,
        time=TimeCreated.now(),
        agent=request.agent or "default",
        model=request.model,
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
    await persist_message_to_storage(state, user_msg_with_parts, session_id)
    await state.broadcast_event(MessageUpdatedEvent.create(user_message))

    # Acquire per-session lock to ensure sequential processing
    lock = state.get_session_lock(session_id)
    async with lock:
        return await _process_message_locked(
            session_id, request, state, user_msg_id, user_msg_with_parts
        )


async def _process_message_locked(  # noqa: PLR0915
    session_id: str,
    request: MessageRequest,
    state: StateDep,
    user_msg_id: str,
    user_msg_with_parts: MessageWithParts,
) -> MessageWithParts:
    """Actual agent processing logic (called within lock).

    Args:
        user_msg_id: ID of already-created user message
        user_msg_with_parts: The user message with parts (already broadcast)
    """
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

    # --- Trigger title generation on first message (fire-and-forget) ---
    # Title generation is non-blocking: the title arrives asynchronously via
    # the ``metadata_generated`` signal / ``SessionUpdatedEvent`` SSE event.
    # This prevents slow title-model responses from delaying the agent reply.
    state.create_background_task(
        _maybe_generate_title(state, session_id, user_prompt),
        name=f"title_gen_{session_id}",
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
    part_id = identifier.ascending("part")
    step_start = StepStartPart(id=part_id, message_id=assistant_msg_id, session_id=session_id)
    assistant_msg_with_parts.parts.append(step_start)
    await state.broadcast_event(PartUpdatedEvent.create(step_start))
    # --- Resolve agent and variant ---
    agent = state.agent
    if request.agent and state.agent.agent_pool is not None:
        agent = state.agent.agent_pool.all_agents.get(request.agent, state.agent)
    request_variant = request.model.variant if request.model else None
    if request_variant:
        # set_mode raises ValueError (or its subclasses UnknownModeError/
        # UnknownCategoryError) for invalid/unsupported modes — safe to ignore.
        try:
            await agent.set_mode(request_variant, category_id="thought_level")
        except ValueError:
            logger.debug("Variant mode not applicable", variant=request_variant)

    # Handle model selection if requested
    original_model: str | None = None
    if request.model and request.model.model_id and request.model.provider_id:
        provider_id = request.model.provider_id
        model_id = request.model.model_id

        # Strategy: First try to use model_id as a variant name
        # OpenCode TUI sends variant names as model_id (e.g., "ack-dev", "qwen35")
        # The provider_id is the first part of the identifier (e.g., "openai-chat")
        requested_model = model_id  # Try variant name first

        logger.info(f"Model selection requested: provider={provider_id}, model_id={model_id}")

        try:
            available_models = await agent.get_available_models()
            is_valid = False

            # Check 1: Is model_id a variant name in manifest?
            if state.pool and model_id in state.pool.manifest.model_variants:
                is_valid = True
                logger.info(f"Model {model_id} found as variant name in manifest")
            # Check 2: Is it in tokonomics models?
            elif available_models:
                valid_ids = [m.id_override if m.id_override else m.id for m in available_models]
                # Try both "provider:model" format and just model_id
                full_id = f"{provider_id}:{model_id}"
                if full_id in valid_ids:
                    is_valid = True
                    requested_model = full_id
                    logger.info(f"Model {full_id} found in tokonomics models")
                elif model_id in valid_ids:
                    is_valid = True
                    logger.info(f"Model {model_id} found in tokonomics models")

            if is_valid:
                # Store original model to restore later
                original_model = agent.model_name
                logger.info(f"Switching model from {original_model} to {requested_model}")
                await agent.set_model(requested_model)
                logger.info("Switched to requested model", model=requested_model)
            else:
                logger.warning(f"Model {model_id} (provider: {provider_id}) is not valid")
                if state.pool:
                    logger.warning(
                        f"Available model_variants: {list(state.pool.manifest.model_variants.keys())}"
                    )
        except Exception as e:  # noqa: BLE001
            # Broad catch: agents differ on how they signal unsupported/invalid model switching.
            # Keep behavior stable for OpenCode (see PR #10 review iterations).
            logger.warning(f"Failed to switch model: {e}")

    # --- Stream via adapter ---
    adapter = OpenCodeStreamAdapter(
        state=state,
        session_id=session_id,
        assistant_msg_id=assistant_msg_id,
        assistant_msg=assistant_msg_with_parts,
        working_dir=state.working_dir,
        on_file_paths=lambda paths: _warmup_lsp_for_files(state, paths),
    )

    response_time: int | None = None
    cancelled = False
    try:
        iterator = agent.run_stream(*user_prompt, session_id=session_id)
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
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
        await persist_message_to_storage(state, assistant_msg_with_parts, session_id)
    except asyncio.CancelledError:
        # User cancelled the request (e.g., pressed ESC)
        logger.info("Request cancelled by user", session_id=session_id)
        cancelled = True

        # Finalize the assistant message with aborted state.
        # This mirrors upstream OpenCode's cleanup() in processor.ts:518
        # and prompt.ts:637-638, 853-854. Without setting time.completed
        # and error, the TUI's `pending` memo permanently finds this
        # stale assistant message, causing all subsequent user messages
        # to display as "QUEUED".
        response_time = now_ms()
        aborted_error = MessageAbortedError(
            data=MessageAbortedErrorData(message="Request cancelled by user")
        )
        msg_time = MessageTime(created=now, completed=response_time)
        update = {"time": msg_time, "error": aborted_error}
        updated_assistant = assistant_msg.model_copy(update=update)
        assistant_msg_with_parts.info = updated_assistant
        await state.broadcast_event(MessageUpdatedEvent.create(updated_assistant))
        await persist_message_to_storage(state, assistant_msg_with_parts, session_id)

        # Add the aborted assistant message to the agent's in-memory conversation.
        # Without this, the agent's conversation.chat_messages only has the user
        # message (added by _run_stream_once at base_agent.py:784) but not the
        # assistant response. On the next message, get_or_load_session() skips
        # reloading because agent.session_id matches, so the LLM receives
        # incomplete history — it doesn't know it already (partially) responded.
        #
        # NOTE: This mutates the shared agent's conversation. In the current
        # single-session server architecture this is safe, but if multi-session
        # support is added, the agent instance should be per-session to avoid
        # history contamination between concurrent sessions.
        chat_msg = opencode_to_chat_message(assistant_msg_with_parts, session_id=session_id)
        agent.conversation.add_chat_messages([chat_msg], extend_last=True)
    finally:
        # Restore original model if we changed it
        if original_model is not None:
            with contextlib.suppress(Exception):
                await agent.set_model(original_model)
                logger.info("Restored original model", model=original_model)

        # --- Mark session idle ---
        # Always set session to idle, even if processing failed or was cancelled
        status = SessionStatus(type="idle")
        state.session_status[session_id] = status
        await state.broadcast_event(SessionStatusEvent.create(session_id, status))
        await state.broadcast_event(SessionIdleEvent.create(session_id))
        # --- Update session timestamp ---
        if response_time is not None:
            session = state.sessions[session_id]
            state.sessions[session_id] = session.model_copy(
                update={
                    "time": TimeCreatedUpdated(created=session.time.created, updated=response_time)
                }
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
    Messages to the same session are processed sequentially using per-session locks
    to prevent race conditions and event interleaving.

    For async processing, use POST /session/{id}/prompt_async instead.
    """
    return await _process_message(session_id, request, state)


@router.post("/prompt_async", status_code=status.HTTP_204_NO_CONTENT)
async def send_message_async(session_id: str, request: MessageRequest, state: StateDep) -> None:
    """Send a message asynchronously without waiting for response.

    Starts the agent processing in the background and returns immediately.
    If the session is busy, the message is queued using agent.queue() and
    will be processed after the current run completes.

    Client should listen to SSE events to get updates.

    Returns 204 No Content immediately.
    """
    # 1. Create user message immediately (UI shows QUEUED status)
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    user_msg_id = identifier.ascending("message", request.message_id)
    user_message = UserMessage(
        id=user_msg_id,
        session_id=session_id,
        time=TimeCreated.now(),
        agent=request.agent or "default",
        model=request.model,
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
    await persist_message_to_storage(state, user_msg_with_parts, session_id)
    await state.broadcast_event(MessageUpdatedEvent.create(user_message))

    # 2. Extract user prompt for queuing/processing
    user_prompt = await extract_user_prompt_from_parts(
        request.parts,
        fs=state.fs,
        tools=state.agent.tools,
    )

    # 3. Check if session is busy
    current_status = state.session_status.get(session_id)
    is_busy = current_status is not None and current_status.type == "busy"

    if is_busy:
        # Session is busy → queue the prompt using agent.queue_prompt()
        # The agent will automatically process queued prompts after current run
        logger.info("Session busy, queuing prompt via agent.queue_prompt()", session_id=session_id)
        agent = state.agent
        if request.agent and state.agent.agent_pool is not None:
            agent = state.agent.agent_pool.all_agents.get(request.agent, state.agent)
        agent.queue_prompt(user_prompt)
        return

    # 4. Session is idle → start background task to process
    logger.info("Session idle, starting background task", session_id=session_id)
    state.create_background_task(
        _process_message_locked(session_id, request, state, user_msg_id, user_msg_with_parts),
        name=f"process_message_{session_id}",
    )


@router.get("/message/{message_id}")
async def get_message(session_id: str, message_id: str, state: StateDep) -> MessageWithParts:
    """Get a specific message."""
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    for msg in state.messages.get(session_id, []):
        if msg.info.id == message_id:
            return msg

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
