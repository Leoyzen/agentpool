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
from agentpool.tasks.exceptions import RunAbortedError
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
    SessionStatus,
    SessionStatusEvent,
    SessionUpdatedEvent,
    StepStartPart,
    SubtaskPartInput,
    TextPartInput,
    TimeCreated,
    TimeCreatedUpdated,
    Tokens,
    UserMessage,
)
from agentpool_server.opencode_server.routes.session_routes import get_or_load_session
from agentpool_server.opencode_server.state import QueuedAsyncPrompt
from agentpool_server.opencode_server.stream_adapter import OpenCodeStreamAdapter


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


logger = get_logger(__name__)


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

        _servers_started = False
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
            _root_uri = f"file://{state.working_dir}"
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
        # Use the session agent's name if available, fallback to template agent name
        session_agent = state._session_agents.get(session_id)
        node_name = session_agent.name if session_agent else state.agent.name
        await storage.log_session(
            session_id=session_id,
            node_name=node_name,
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
    # Fast path for subagent/child sessions already in memory:
    # Skip get_or_load_session (which acquires agent_lock) because the
    # parent agent holds agent_lock while streaming, so the lock would
    # block until the parent finishes — making child messages invisible
    # during subagent execution.
    cached_session = state.sessions.get(session_id)
    if (
        cached_session is not None
        and cached_session.parent_id is not None
        and session_id in state.messages
    ):
        messages = state.messages[session_id]
        return messages[-limit:] if limit else messages

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
    *,
    mark_busy: bool = True,
    mark_idle: bool = True,
) -> MessageWithParts:
    """Actual agent processing logic (called within lock).

    Args:
        session_id: Session receiving the message.
        request: Request payload containing the user's parts and agent/model choice.
        state: Shared OpenCode server state.
        user_msg_id: ID of already-created user message
        user_msg_with_parts: The user message with parts (already broadcast)
        mark_busy: Whether to emit a busy transition before processing.
        mark_idle: Whether to emit an idle transition when processing completes.
    """
    # --- Register active message task so abort_session can cancel it ---
    current_task = asyncio.current_task()
    if current_task is not None:
        state.register_active_message_task(session_id, current_task)

    # --- Clear revert marker (mirrors opencode-native's revert.cleanup()) ---
    # When a user does /undo then sends a new message, the session.revert
    # marker must be cleared so the frontend stops filtering messages with
    # ``message.id >= revert.messageID``.  Without this, ALL new messages
    # are hidden because their ascending IDs are always >= revert.messageID.
    session = state.sessions.get(session_id)
    if session is not None and session.revert is not None:
        updated_session = session.model_copy(update={"revert": None})
        state.sessions[session_id] = updated_session
        # Discard any stored reverted messages for this session
        state.reverted_messages.pop(session_id, None)
        # Broadcast so the frontend removes the revert filter immediately
        await state.broadcast_event(SessionUpdatedEvent.create(updated_session))

    # --- Mark session busy ---
    if mark_busy:
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
    # Per-session agent: each session has its own agent instance,
    # so no global agent_lock is needed. Same-session serialization
    # is handled by get_session_lock() in _process_message().
    agent = await state.get_or_create_agent(session_id)
    # Delegate agent resolution (for subagent requests).
    # Only resolve a delegate when the request names a *different* agent
    # from the default session agent.  A request.agent value of "default"
    # (or any name that matches the session agent) means "use my session
    # agent" — no delegation needed.
    #
    # NOTE: Subagents from state.pool.all_agents are shared singleton
    # instances.  Mutating session_id/_input_provider on them is safe ONLY
    # because same-session serialization (via get_session_lock) prevents
    # concurrent access.  Per-session subagent instances are NOT feasible
    # due to MCP subprocess overhead.  If OpenCode ever supports direct
    # multi-agent selection, this must be redesigned via AgentPool's
    # delegation/team mechanism instead.
    if request.agent and state.pool is not None:
        all_agents = state.pool.all_agents
        # Only delegate to a different agent from the pool — if the request
        # names the same agent as the session's default, the per-session
        # instance is already the right one.
        if request.agent in all_agents and all_agents[request.agent] is not agent:
            if state._agent_config is not None and request.agent == state._agent_config.name:
                pass  # Use per-session agent, don't replace with pool singleton
            else:
                agent = all_agents[request.agent]
    # Ensure agent is bound to this session
    agent._input_provider = state.ensure_input_provider(session_id)

    try:
        request_variant = request.model.variant if request.model else None
        if request_variant:
            # set_mode raises ValueError (or its subclasses UnknownModeError/
            # UnknownCategoryError) for invalid/unsupported modes — safe to ignore.
            try:
                await agent.set_mode(request_variant, category_id="thought_level")
            except ValueError:
                logger.debug("Variant mode not applicable", variant=request_variant)

        # Handle model selection if requested — no save/restore needed
        # because each session has its own agent instance.
        if request.model and request.model.model_id and request.model.provider_id:
            provider_id = request.model.provider_id
            model_id = request.model.model_id

            # Strategy: First try to use model_id as a variant name
            # OpenCode TUI sends variant names as model_id (e.g., "ack-dev", "qwen35")
            # The provider_id is the first part of the identifier (e.g., "openai-chat")
            requested_model = model_id  # Try variant name first

            logger.info("Model selection requested", provider=provider_id, model_id=model_id)

            try:
                available_models = await agent.get_available_models()
                is_valid = False

                # Check 1: Is model_id a variant name in manifest?
                if state.pool and model_id in state.pool.manifest.model_variants:
                    is_valid = True
                    logger.info("Model found as manifest variant", model_id=model_id)
                # Check 2: Is it in tokonomics models?
                elif available_models:
                    valid_ids = [m.id_override if m.id_override else m.id for m in available_models]
                    # Try both "provider:model" format and just model_id
                    full_id = f"{provider_id}:{model_id}"
                    if full_id in valid_ids:
                        is_valid = True
                        requested_model = full_id
                        logger.info("Model found in available models", model_id=full_id)
                    elif model_id in valid_ids:
                        is_valid = True
                        logger.info("Model found in available models", model_id=model_id)

                if is_valid:
                    logger.info(
                        "Switching model for session",
                        requested_model=requested_model,
                    )
                    await agent.set_model(requested_model)
                    logger.info("Switched to requested model", model=requested_model)
                else:
                    logger.warning(
                        "Requested model is not valid",
                        model_id=model_id,
                        provider_id=provider_id,
                    )
                    if state.pool:
                        logger.warning(
                            "Available manifest variants",
                            variants=list(state.pool.manifest.model_variants.keys()),
                        )
            except Exception as e:  # noqa: BLE001
                # Broad catch: agents differ on how they signal
                # unsupported/invalid model switching.
                # Keep behavior stable for OpenCode (see PR #10 review iterations).
                logger.warning("Failed to switch model", error=str(e))

        iterator = agent.run_stream(*user_prompt, session_id=session_id)
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
    except (asyncio.CancelledError, TimeoutError, RunAbortedError) as exc:
        # User cancelled the request (e.g., pressed ESC), or an external
        # timeout (e.g. anyio.fail_after in a tool call) propagated as
        # TimeoutError instead of CancelledError on Python 3.12+, or the
        # agent aborted the run (e.g. question_for_user raised RunAbortedError
        # when the user cancelled the questionnaire).
        # All three cases require the same cleanup: finalize the assistant
        # message with an aborted state so the TUI doesn't get stuck.
        if isinstance(exc, asyncio.CancelledError):
            reason = "Request cancelled by user"
        elif isinstance(exc, RunAbortedError):
            reason = str(exc) or "Run aborted by agent"
        else:
            reason = "Request timed out"
        logger.info(reason, session_id=session_id)

        # Finalize the assistant message with aborted state.
        # This mirrors upstream OpenCode's cleanup() in processor.ts:518
        # and prompt.ts:637-638, 853-854. Without setting time.completed
        # and error, the TUI's `pending` memo permanently finds this
        # stale assistant message, causing all subsequent user messages
        # to display as "QUEUED".
        response_time = now_ms()
        aborted_error = MessageAbortedError(data=MessageAbortedErrorData(message=reason))
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
        # This is safe because the agent is a per-session instance — concurrent
        # sessions each have their own agent, so there is no history contamination
        # between sessions.
        chat_msg = opencode_to_chat_message(assistant_msg_with_parts, session_id=session_id)
        agent.conversation.add_chat_messages([chat_msg], extend_last=True)
    finally:
        # --- Unregister active message task ---
        state.unregister_active_message_task(session_id)
        # --- Mark session idle ---
        # The async prompt worker owns session idling while it drains queued work.
        if mark_idle:
            await state.mark_session_idle(session_id)
            await _ensure_async_prompt_worker(session_id, state, mark_busy=True)
        # --- Update session timestamp ---
        if response_time is not None:
            session = state.sessions[session_id]
            state.sessions[session_id] = session.model_copy(
                update={
                    "time": TimeCreatedUpdated(created=session.time.created, updated=response_time)
                }
            )
    return assistant_msg_with_parts


async def _ensure_async_prompt_worker(
    session_id: str,
    state: StateDep,
    *,
    mark_busy: bool,
) -> None:
    """Start the per-session async prompt worker when queued work exists."""
    if not state.has_pending_async_prompts(session_id):
        return
    if state.has_session_background_task(session_id):
        return

    if mark_busy:
        busy = SessionStatus(type="busy")
        state.session_status[session_id] = busy
        await state.broadcast_event(SessionStatusEvent.create(session_id, busy))

    state.create_background_task(
        _run_async_prompt_queue(session_id, state),
        name=f"process_message_{session_id}",
    )


async def _run_async_prompt_queue(session_id: str, state: StateDep) -> None:
    """Drain queued async prompts for a session in FIFO order."""
    lock = state.get_session_lock(session_id)
    try:
        while True:
            async with lock:
                queued_prompt = state.pop_next_async_prompt(session_id)
                if queued_prompt is None:
                    await state.mark_session_idle(session_id)
                    return

                await _process_message_locked(
                    session_id,
                    queued_prompt.request,
                    state,
                    queued_prompt.user_msg_id,
                    queued_prompt.user_msg_with_parts,
                    mark_busy=False,
                    mark_idle=False,
                )

                if state.has_pending_async_prompts(session_id):
                    await state.emit_session_turn_complete(session_id)
                    continue

                await state.mark_session_idle(session_id)
                return
    except asyncio.CancelledError:
        logger.info("Async prompt worker cancelled", session_id=session_id)
        raise
    except Exception:
        logger.exception("Async prompt worker failed", session_id=session_id)
        await state.mark_session_idle(session_id)
        raise


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
    If the session is busy, the message is queued in server state and
    processed after the current run completes.

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

    # 2. Atomically queue work, then start a single per-session worker if needed.
    lock = state.get_session_lock(session_id)
    async with lock:
        state.enqueue_async_prompt(
            session_id,
            QueuedAsyncPrompt(
                request=request,
                user_msg_id=user_msg_id,
                user_msg_with_parts=user_msg_with_parts,
            ),
        )

        current_status = state.session_status.get(session_id)
        mark_busy = current_status is None or current_status.type != "busy"
        if not mark_busy:
            logger.info(
                "Session became busy before async dispatch, keeping prompt in server queue",
                session_id=session_id,
            )
        else:
            logger.info("Session idle, starting background task", session_id=session_id)

        await _ensure_async_prompt_worker(session_id, state, mark_busy=mark_busy)


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
