"""Message routes."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, assert_never

from fastapi import APIRouter, HTTPException, Query, status

from agentpool.agents.events import RunErrorEvent, RunFailedEvent
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
from agentpool_server.opencode_server.session_pool_integration import (
    append_message_to_session,
    get_messages_for_session,
    set_session_status,
)
from agentpool_server.opencode_server.stream_adapter import OpenCodeStreamAdapter


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai import UserContent

    from agentpool.common_types import PathReference
    from agentpool_server.opencode_server.state import ServerState


logger = get_logger(__name__)


def _session_disables_title_generation(state: ServerState, session_id: str) -> bool:
    """Return whether SessionPool metadata disables title generation."""
    session_pool = state.pool.session_pool if state.pool else None
    if session_pool is None:
        return False

    session_state = session_pool.sessions.get_session(session_id)
    metadata = getattr(session_state, "metadata", None)
    return isinstance(metadata, dict) and metadata.get("generate_title") is False


def _resolve_message_agent_name(
    state: ServerState,
    session_id: str,
    requested_agent: str | None,
) -> str:
    """Resolve the agent for a message, inheriting the session binding by default."""
    if requested_agent and requested_agent != "default":
        if requested_agent not in state.pool.manifest.agents:
            raise HTTPException(status_code=400, detail=f"Unknown agent: {requested_agent}")
        return requested_agent

    session_pool = state.pool.session_pool
    if session_pool is not None:
        session_state = session_pool.sessions.get_session(session_id)
        if session_state is not None and isinstance(session_state.agent_name, str):
            return session_state.agent_name

    return state.agent.name or "default"


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
    state.create_background_task(warmup(), name="warmup_lsp")


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
    if _session_disables_title_generation(state, session_id):
        return

    # Check if this is the first user message by looking at existing messages
    existing_messages = await get_messages_for_session(state, session_id)

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
        node_name = state.agent.name
        await storage.log_session(
            session_id=session_id,
            node_name=node_name,
            initial_prompt=prompt_text,
        )
    except Exception:
        logger.exception("Failed to generate title", session_id=session_id)


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

    messages = await get_messages_for_session(state, session_id)
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

    agent_name = _resolve_message_agent_name(state, session_id, request.agent)
    user_msg_id = identifier.ascending("message", request.message_id)
    user_message = UserMessage(
        id=user_msg_id,
        session_id=session_id,
        time=TimeCreated.now(),
        agent=agent_name,
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
    await append_message_to_session(state, session_id, user_msg_with_parts)
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
        await set_session_status(state, session_id, busy)
        await state.broadcast_event(SessionStatusEvent.create(session_id, busy))
    agent_name = _resolve_message_agent_name(state, session_id, request.agent)
    # --- Extract user prompt ---
    user_prompt = await extract_user_prompt_from_parts(
        request.parts,
        fs=state.fs,
        agent=state.agent,
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
    # D14: Generate the canonical assistant_msg_id. This is passed to
    # receive_request(message_id=...) so it flows through the event pipeline
    # and the consumer loop reuses it instead of generating its own.
    assistant_msg_id = identifier.ascending("message")
    now = now_ms()
    assistant_msg = AssistantMessage(
        id=assistant_msg_id,
        session_id=session_id,
        parent_id=user_msg_id,
        model_id=request.model.model_id if request.model else "default",
        provider_id=request.model.provider_id if request.model else "agentpool",
        mode=agent_name,
        agent=agent_name,
        path=MessagePath(cwd=state.working_dir, root=state.working_dir),
        time=MessageTime(created=now),
    )
    assistant_msg_with_parts = MessageWithParts(info=assistant_msg, parts=[])
    await append_message_to_session(state, session_id, assistant_msg_with_parts)
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

    # The stream adapter will be fed events directly from the EventBus
    # subscriber loop below so that its mutable context (text, tokens,
    # step-finish tracking) is updated before finalize() is called.

    response_time: int | None = None
    # Per-session agent: each session has its own agent instance,
    # so no global agent_lock is needed. Same-session serialization
    # is handled by get_session_lock() in _process_message().
    # Delegate agent resolution (for subagent requests).
    # Only resolve a delegate when the request names a *different* agent
    # from the default session agent.  A request.agent value of "default"
    # (or any name that matches the session agent) means "use my session
    # agent" — no delegation needed.
    #
    # Delegate agent resolution (for subagent requests).
    # Uses SessionPool's get_or_create_session_agent to create per-session
    # agent instances.  Each delegate agent name gets a unique sub-session
    # ID derived from the main session ID, ensuring per-agent isolation.
    if state.pool is not None and agent_name in state.pool.manifest.agents:
        # Only delegate to a different agent from the pool — if the request
        # names the same agent as the session's default, the per-session
        # instance is already the right one.
        current_agent_name = getattr(state.agent, "name", None)
        if agent_name != current_agent_name:
            session_pool = state.pool.session_pool
            if session_pool is not None:
                await session_pool.sessions.get_or_create_session_agent(
                    f"{session_id}-agent-{agent_name}", agent_name
                )
    # Get input provider for this session — stored on SessionState, NOT on agent.
    # SessionController passes input_provider to the agent via kwargs at run time.
    input_provider = state.ensure_input_provider(session_id)

    # --- SessionPool integration ---
    integration = state.session_pool_integration
    session_pool = state.pool.session_pool
    if session_pool is None:
        msg = "SessionPool not available"
        raise RuntimeError(msg)

    # Ensure session exists in SessionPool before routing
    if integration is not None:
        sp_state = await integration.create_session(
            session_id,
            agent_name=agent_name,
        )
    else:
        sp_state, _was_created = await session_pool.sessions.get_or_create_session(
            session_id,
            agent_name=agent_name,
        )
    sp_state.input_provider = input_provider

    # Obtain per-session agent for model switching so each session
    # gets its own isolated model configuration.
    session_agent = await session_pool.sessions.get_or_create_session_agent(
        session_id,
        agent_name=agent_name,
        input_provider=input_provider,
    )

    try:
        request_variant = request.model.variant if request.model else None
        if request_variant:
            # set_mode raises ValueError (or its subclasses UnknownModeError/
            # UnknownCategoryError) for invalid/unsupported modes — safe to ignore.
            try:
                await session_agent.set_mode(request_variant, category_id="thought_level")
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
                available_models = await session_agent.get_available_models()
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
                    await session_agent.set_model(requested_model)
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

        # Route through SessionPool instead of calling agent.run_stream() directly.
        # Events will be delivered via the EventBus subscription below.
        #
        # Architecture note (auto-subscribe-subagent-events change):
        # When SessionPool is enabled, the protocol layer auto-subscribes
        # to the EventBus with scope="session". This means child session
        # events are automatically received and forwarded to the frontend
        # via SubAgentEvent without any manual subscription in message_routes.
        # The _consume_events loop below only handles the parent session's
        # direct agent events; child events flow through the EventBus
        # independently via _consume_child_events.
        # D13: Map delivery mode from request to priority.
        # "steer" → "asap" (inject into active turn), "queue" → "when_idle".
        delivery_priority = "asap" if request.delivery == "steer" else "when_idle"
        if integration is not None:
            message_id = await integration.route_message(
                session_id=session_id,
                content=user_prompt if isinstance(user_prompt, str) else list(user_prompt),
                priority=delivery_priority,
                input_provider=input_provider,
                agent_name=agent_name,
                message_id=assistant_msg_id,
            )
        else:
            from agentpool.lifecycle.types import DeliveryMode

            delivery_mode = (
                DeliveryMode.STEER if delivery_priority == "asap" else DeliveryMode.QUEUE
            )
            message_id = await session_pool.send_message(
                session_id=session_id,
                content=user_prompt if isinstance(user_prompt, str) else list(user_prompt),
                mode=delivery_mode,
                input_provider=input_provider,
                message_id=assistant_msg_id,
            )

        if message_id is not None:
            # Subscribe to EventBus locally so the adapter receives events
            # and accumulates response_text / tokens for finalize().
            # The session-scoped consumer (_event_consumer_loop) already
            # broadcasts SSE events; we only feed the adapter context here.
            event_stream = await session_pool.event_bus.subscribe(session_id)

            # Track whether the run failed via event observation
            run_failed = False

            async def _feed_adapter() -> None:
                from agentpool.orchestrator.core import drain_and_merge

                nonlocal run_failed
                async for event in drain_and_merge(event_stream):
                    if isinstance(event.event, (RunErrorEvent, RunFailedEvent)):
                        run_failed = True
                    async for _ in adapter.convert_event(event.event):
                        pass

            adapter_task = asyncio.create_task(_feed_adapter(), name=f"adapter_feed_{session_id}")

            try:
                await session_pool.wait_for_completion(session_id)
            except TimeoutError:
                # Turn hung — cancel the run to break through __aexit__ hang
                session_pool.sessions.cancel_run_for_session(session_id)
                raise
            except asyncio.CancelledError:
                session_pool.sessions.cancel_run_for_session(session_id)
                raise
            finally:
                adapter_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await adapter_task
                await session_pool.event_bus.unsubscribe(session_id, event_stream)

            # Finalize based on run outcome
            if not run_failed:
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
            else:
                # Run failed — finalize assistant message with aborted state
                response_time = now_ms()
                reason = "Run failed"
                aborted_error = MessageAbortedError(data=MessageAbortedErrorData(message=reason))
                msg_time = MessageTime(created=now, completed=response_time)
                update = {"time": msg_time, "error": aborted_error}
                updated_assistant = assistant_msg.model_copy(update=update)
                assistant_msg_with_parts.info = updated_assistant
                await state.broadcast_event(MessageUpdatedEvent.create(updated_assistant))
                await persist_message_to_storage(state, assistant_msg_with_parts, session_id)

                # Add the aborted assistant message to the SessionPool agent's
                # in-memory conversation so history remains consistent.
                sp_session_pool = (
                    integration.session_pool if integration is not None else session_pool
                )
                sp_session = sp_session_pool.sessions.get_session(session_id)
                if sp_session is not None and sp_session.agent is not None:
                    chat_msg = opencode_to_chat_message(
                        assistant_msg_with_parts, session_id=session_id
                    )
                    sp_session.agent.conversation.add_chat_messages([chat_msg], extend_last=True)
        else:
            # Message was queued for later processing (session busy)
            logger.info(
                "Message queued in SessionPool for later processing",
                session_id=session_id,
            )
    except asyncio.CancelledError:
        response_time = now_ms()
        reason = "Request cancelled by user"
        aborted_error = MessageAbortedError(data=MessageAbortedErrorData(message=reason))
        msg_time = MessageTime(created=now, completed=response_time)
        update = {"time": msg_time, "error": aborted_error}
        updated_assistant = assistant_msg.model_copy(update=update)
        assistant_msg_with_parts.info = updated_assistant
        await state.broadcast_event(MessageUpdatedEvent.create(updated_assistant))
        await persist_message_to_storage(state, assistant_msg_with_parts, session_id)

        # Add the aborted assistant message to the SessionPool agent's
        # in-memory conversation so history remains consistent.
        sp_session_pool = integration.session_pool if integration is not None else session_pool
        sp_session = sp_session_pool.sessions.get_session(session_id)
        if sp_session is not None and sp_session.agent is not None:
            chat_msg = opencode_to_chat_message(assistant_msg_with_parts, session_id=session_id)
            sp_session.agent.conversation.add_chat_messages([chat_msg], extend_last=True)
    except Exception as exc:
        # Any unexpected error during SessionPool routing
        logger.exception("SessionPool routing failed", session_id=session_id, error=str(exc))
        response_time = now_ms()
        reason = f"Error: {exc}"
        aborted_error = MessageAbortedError(data=MessageAbortedErrorData(message=reason))
        msg_time = MessageTime(created=now, completed=response_time)
        update = {"time": msg_time, "error": aborted_error}
        updated_assistant = assistant_msg.model_copy(update=update)
        assistant_msg_with_parts.info = updated_assistant
        await state.broadcast_event(MessageUpdatedEvent.create(updated_assistant))
        await persist_message_to_storage(state, assistant_msg_with_parts, session_id)

        # Add the aborted assistant message to the SessionPool agent's
        # in-memory conversation so history remains consistent.
        sp_session_pool = integration.session_pool if integration is not None else session_pool
        sp_session = sp_session_pool.sessions.get_session(session_id)
        if sp_session is not None and sp_session.agent is not None:
            chat_msg = opencode_to_chat_message(assistant_msg_with_parts, session_id=session_id)
            sp_session.agent.conversation.add_chat_messages([chat_msg], extend_last=True)
    finally:
        # Session-scoped resources (EventBus consumer)
        # are managed by OpenCodeSessionPoolIntegration and are NOT torn
        # down here.  They outlive individual HTTP requests so that auto-
        # resume events are still streamed to the frontend.
        #
        # --- Mark session idle ---
        # The async prompt worker owns session idling while it drains queued work.
        if mark_idle:
            await state.mark_session_idle(session_id)
        # --- Update session timestamp ---
        if response_time is not None:
            session = state.sessions.get(session_id)
            if session is None:
                logger.info(
                    "Session removed before message cleanup completed",
                    session_id=session_id,
                )
            else:
                state.sessions[session_id] = session.model_copy(
                    update={
                        "time": TimeCreatedUpdated(
                            created=session.time.created,
                            updated=response_time,
                        )
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

    Routes the prompt through the SessionPool and returns immediately.
    If the session is busy, the message is queued by the SessionPool and
    processed after the current run completes.

    Client should listen to SSE events to get updates.

    Returns 204 No Content immediately.
    """
    # 1. Create user message immediately (UI shows QUEUED status)
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    agent_name = _resolve_message_agent_name(state, session_id, request.agent)
    user_msg_id = identifier.ascending("message", request.message_id)
    user_message = UserMessage(
        id=user_msg_id,
        session_id=session_id,
        time=TimeCreated.now(),
        agent=agent_name,
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
    await append_message_to_session(state, session_id, user_msg_with_parts)
    await persist_message_to_storage(state, user_msg_with_parts, session_id)
    await state.broadcast_event(MessageUpdatedEvent.create(user_message))

    # 2. Route through SessionPool instead of server-owned queue
    session_pool = state.pool.session_pool
    if session_pool is not None:
        input_provider = state.ensure_input_provider(session_id)

        user_prompt = await extract_user_prompt_from_parts(
            request.parts,
            fs=state.fs,
            agent=state.agent,
        )

        # D13: Map delivery mode from request to priority.
        delivery_priority = "asap" if request.delivery == "steer" else "when_idle"
        # D14: Generate assistant_msg_id and pass to receive_request so the
        # consumer loop reuses it instead of generating an independent one.
        async_assistant_msg_id = identifier.ascending("message")
        # Use integration layer to ensure session creation and event consumer startup
        integration = state.session_pool_integration
        if integration is not None:
            await integration.route_message(
                session_id=session_id,
                content=user_prompt if isinstance(user_prompt, str) else list(user_prompt),
                priority=delivery_priority,
                input_provider=input_provider,
                agent_name=agent_name,
                message_id=async_assistant_msg_id,
                model_id=request.model.model_id if request.model else None,
                provider_id=request.model.provider_id if request.model else None,
            )
        else:
            sp_state, _was_created = await session_pool.sessions.get_or_create_session(
                session_id,
                agent_name=agent_name,
            )
            sp_state.input_provider = input_provider

            from agentpool.lifecycle.types import DeliveryMode

            delivery_mode = (
                DeliveryMode.STEER if delivery_priority == "asap" else DeliveryMode.QUEUE
            )
            await session_pool.send_message(
                session_id=session_id,
                content=user_prompt if isinstance(user_prompt, str) else list(user_prompt),
                mode=delivery_mode,
                input_provider=input_provider,
                message_id=async_assistant_msg_id,
            )


@router.get("/message/{message_id}")
async def get_message(session_id: str, message_id: str, state: StateDep) -> MessageWithParts:
    """Get a specific message."""
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    for msg in await get_messages_for_session(state, session_id):
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
    for msg in await get_messages_for_session(state, session_id):
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
    for msg in await get_messages_for_session(state, session_id):
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
