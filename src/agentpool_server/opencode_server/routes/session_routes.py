"""Session routes."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from anyenv.text_sharing.opencode import Message, MessagePart, OpenCodeSharer
from fastapi import APIRouter, HTTPException
from pydantic_ai import FileUrl
from slashed import CommandContext

from agentpool.log import get_logger
from agentpool.repomap import RepoMap, find_src_files
from agentpool.utils import identifiers as identifier
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.command_validation import validate_command
from agentpool_server.opencode_server.converters import (
    chat_message_to_opencode,
    opencode_to_session_data,
    session_data_to_opencode,
)
from agentpool_server.opencode_server.dependencies import StateDep
from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
from agentpool_server.opencode_server.models import (
    AssistantMessage,
    CommandExecutedEvent,
    CommandRequest,
    FileDiff,
    MessagePath,
    MessageTime,
    MessageUpdatedEvent,
    MessageWithParts,
    OpenCodeBaseModel,
    PartDeltaEvent,
    PartUpdatedEvent,
    PermissionAskedProperties,
    PermissionReplyRequest,
    PermissionResolvedEvent,
    Session,
    SessionCreatedEvent,
    SessionCreateRequest,
    SessionDeletedEvent,
    SessionDiffEvent,
    SessionForkRequest,
    SessionIdleEvent,
    SessionInitRequest,
    SessionRevert,
    SessionShare,
    SessionStatus,
    SessionStatusEvent,
    SessionUpdatedEvent,
    SessionUpdateRequest,
    ShellRequest,
    StepFinishPart,
    StepStartPart,
    SummarizeRequest,
    TextPart,
    TimeCreated,
    TimeCreatedUpdated,
    Todo,
    Tokens,
    UserMessage,
)
from agentpool_server.opencode_server.stream_adapter import OpenCodeStreamAdapter
from agentpool_storage.opencode_provider import helpers


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState

logger = get_logger(__name__)


class _CommandOutputCapture:
    """Output writer that captures command output to a string buffer."""

    def __init__(self) -> None:
        self._buffer: list[str] = []

    async def print(self, message: str) -> None:
        """Write a message to the buffer."""
        self._buffer.append(message)

    def __str__(self) -> str:
        """Get the captured output as a single string."""
        return "\n".join(self._buffer)


def _process_skill_template(template: str, arguments: str | None) -> str:
    """Process skill template with placeholder substitution like opencode.

    Args:
        template: The skill instruction template.
        arguments: The command arguments string.

    Returns:
        The processed template with placeholders replaced.

    Supported placeholders:
        - $1, $2, etc.: Positional arguments
        - $ARGUMENTS: All arguments as a single string
    """
    args = arguments.split() if arguments else []

    # Find numbered placeholders $1, $2, etc.
    import re

    placeholder_regex = r"\$(\d+|ARGUMENTS)"
    placeholders = re.findall(placeholder_regex, template)

    # Find the highest numbered placeholder
    last_pos = 0
    for p in placeholders:
        if p.isdigit():
            last_pos = max(last_pos, int(p))

    # Replace placeholders
    def replace_placeholder(match: re.Match[str]) -> str:
        placeholder = match.group(1)
        if placeholder == "ARGUMENTS":
            return arguments or ""
        pos = int(placeholder)
        idx = pos - 1
        if idx >= len(args):
            return ""
        if pos == last_pos and idx < len(args):
            # Last placeholder swallows remaining args
            return " ".join(args[idx:])
        return args[idx] if idx < len(args) else ""

    result = re.sub(placeholder_regex, replace_placeholder, template)

    # If no placeholders and arguments exist, wrap in user_request tag
    if not placeholders and arguments and arguments.strip():
        result = result + "\n\n<user_request>\n\n" + arguments + "\n\n</user_request>"

    return result


def _create_command_context(state: ServerState) -> CommandContext[Any]:
    """Create a CommandContext for executing slash commands.

    Args:
        state: The current server state with agent and working directory info.

    Returns:
        A CommandContext configured with the agent context, output capture, and command store.
    """
    from agentpool.agents.context import AgentContext

    assert state.command_store is not None, "Command store must be initialized"

    agent_ctx = AgentContext(node=state.agent, data=None)
    return CommandContext(
        output=_CommandOutputCapture(),
        data=agent_ctx,
        command_store=state.command_store,
    )


async def _execute_slashed_command(
    state: ServerState,
    session_id: str,
    request: CommandRequest,
) -> MessageWithParts:
    """Execute a slashed command from the CommandStore.

    Args:
        state: The server state containing the command store and agent.
        session_id: The session ID for this command execution.
        request: The command request with command name and arguments.

    Returns:
        MessageWithParts containing the command output.

    Raises:
        HTTPException: 404 if command store not initialized or command not found.
        HTTPException: 500 if command execution fails.
    """
    # Validate command store is available
    if state.command_store is None:
        raise HTTPException(status_code=404, detail="Command store not initialized")

    # Retrieve command from store
    command = state.command_store.get_command(request.command)
    if command is None:
        raise HTTPException(status_code=404, detail=f"Command not found: {request.command}")

    # Check if this is a skill command by looking it up in pool.skill_commands
    skill_cmd = None
    if state.pool.skill_commands:
        skill_cmd = state.pool.skill_commands.get(request.command)

    if skill_cmd:
        return await _execute_skill_command(state, session_id, request)

    # Create assistant message (before execution)
    now = now_ms()
    assistant_msg_id = identifier.ascending("message")
    assistant_message = AssistantMessage(
        id=assistant_msg_id,
        session_id=session_id,
        parent_id="",
        model_id=request.model or "default",
        provider_id="opencode",
        mode="command",
        agent=request.agent or "default",
        path=MessagePath(cwd=state.working_dir, root=state.working_dir),
        time=MessageTime(created=now),
    )

    # Initialize message with parts
    message_with_parts = MessageWithParts(info=assistant_message, parts=[])

    # Store message in state and broadcast
    state.messages[session_id].append(message_with_parts)
    await state.broadcast_event(MessageUpdatedEvent.create(assistant_message))

    try:
        # Mark session as busy
        state.session_status[session_id] = SessionStatus(type="busy")
        await state.broadcast_event(
            SessionStatusEvent.create(session_id, SessionStatus(type="busy"))
        )

        # Add step-start part to indicate command is running
        part_id = identifier.ascending("part")
        step_start = StepStartPart(id=part_id, message_id=assistant_msg_id, session_id=session_id)
        message_with_parts.parts.append(step_start)
        await state.broadcast_event(PartUpdatedEvent.create(step_start))

        # Parse arguments
        args = request.arguments.split() if request.arguments else []

        # Create command context with output capture
        output_capture = _CommandOutputCapture()
        cmd_ctx = CommandContext(
            output=output_capture,
            data=state.agent.get_context(),
            command_store=state.command_store,
        )

        # Execute command
        try:
            await command.execute(cmd_ctx, args, {})
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Command execution failed: {e}") from e

        # Get command output
        output_text = str(output_capture) if output_capture else "Command executed"

        # Create text part with output
        text_part = TextPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg_id,
            session_id=session_id,
            text=output_text,
        )
        message_with_parts.parts.append(text_part)
        await state.broadcast_event(PartUpdatedEvent.create(text_part))

        # Run agent to process the loaded skill context
        try:
            # Create adapter to stream agent events through existing text_part
            adapter = OpenCodeStreamAdapter(
                state=state,
                session_id=session_id,
                assistant_msg_id=assistant_msg_id,
                assistant_msg=message_with_parts,
                working_dir=state.working_dir,
            )
            # Build prompt including user arguments
            user_request = (
                request.arguments if request.arguments else "请使用已加载的 skill context"
            )
            agent_prompt = (
                f"用户执行了命令 '{request.command}' 并说: {user_request}\n\n"
                "请使用已加载的 skill context 来回答用户的请求。"
            )

            # Run agent with prompt to use the skill context
            iterator = state.agent.run_stream(
                agent_prompt,
                session_id=session_id,
            )
            async for oc_event in adapter.process_stream(iterator):
                await state.broadcast_event(oc_event)
            # Append adapter's response to text_part
            if adapter.response_text:
                text_part.text = f"{output_text}\n\n{adapter.response_text}"
                await state.broadcast_event(PartUpdatedEvent.create(text_part))
        except Exception:  # noqa: BLE001
            # Command already executed, ignore agent errors
            pass

        # Add step-finish part to indicate command completed
        step_finish = StepFinishPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg_id,
            session_id=session_id,
        )
        message_with_parts.parts.append(step_finish)
        await state.broadcast_event(PartUpdatedEvent.create(step_finish))

        # Broadcast command.executed event
        await state.broadcast_event(
            CommandExecutedEvent.create(
                name=request.command,
                session_id=session_id,
                arguments=request.arguments or "",
                message_id=assistant_msg_id,
            )
        )
    finally:
        await state.mark_session_idle(session_id)

    return message_with_parts


async def _execute_skill_command(
    state: ServerState,
    session_id: str,
    request: CommandRequest,
) -> MessageWithParts:
    """Execute a skill command from the SkillCommandRegistry.

    This implements opencode-compatible skill handling:
    1. Load skill instructions
    2. Process template with arguments ($1, $2, $ARGUMENTS)
    3. Create USER message with processed content
    4. Run agent with this user message

    Args:
        state: The server state containing the skill commands.
        session_id: The session ID for this command execution.
        request: The command request with command name and arguments.

    Returns:
        MessageWithParts containing the assistant's response.

    Raises:
        HTTPException: 404 if skill command not found.
    """
    skill_name = request.command.removeprefix("skill:")

    # Get skill command from pool
    skill_cmd = None
    if state.pool.skill_commands:
        skill_cmd = state.pool.skill_commands.get(skill_name)

    if not skill_cmd:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_name}")

    # Load skill instructions - use provider for virtual skills
    instructions = ""
    if state.pool.skill_provider is not None:
        try:
            instructions = await state.pool.skill_provider.get_skill_instructions(skill_name)
        except Exception:  # noqa: BLE001
            # Fall back to local load if provider fetch fails
            try:
                instructions = skill_cmd.skill.load_instructions()
            except ValueError:
                instructions = ""
    else:
        try:
            instructions = skill_cmd.skill.load_instructions()
        except ValueError:
            instructions = ""

    # Build RFC-0008 compatible XML format prompt
    args = request.arguments or ""
    user_prompt = f"""<skill-instruction>
{instructions}
</skill-instruction>

<user-request>
{args}
</user-request>"""

    try:
        # Mark session as busy
        state.session_status[session_id] = SessionStatus(type="busy")
        await state.broadcast_event(
            SessionStatusEvent.create(session_id, SessionStatus(type="busy"))
        )

        # Load session into agent to ensure conversation history is restored
        # This ensures agent sees all previous messages during this run
        await state.agent.load_session(session_id)

        # Create USER message (not assistant!)
        user_msg_id = identifier.ascending("message")
        user_message = UserMessage(
            id=user_msg_id,
            session_id=session_id,
            role="user",
            time=TimeCreated.now(),
            agent=request.agent or "default",
        )
        user_part_id = identifier.ascending("part")
        user_msg_with_parts = MessageWithParts(
            info=user_message,
            parts=[
                TextPart(
                    id=user_part_id, message_id=user_msg_id, session_id=session_id, text=user_prompt
                )
            ],
        )

        # Store and broadcast user message
        state.messages[session_id].append(user_msg_with_parts)
        await state.broadcast_event(PartUpdatedEvent.create(user_msg_with_parts.parts[0]))
        await state.broadcast_event(MessageUpdatedEvent.create(user_message))

        # Create assistant message (for response)
        assistant_msg_id = identifier.ascending("message")
        assistant_message = AssistantMessage(
            id=assistant_msg_id,
            session_id=session_id,
            parent_id=user_msg_id,
            model_id=request.model or "default",
            provider_id="opencode",
            mode="command",
            agent=request.agent or "default",
            path=MessagePath(cwd=state.working_dir, root=state.working_dir),
            time=MessageTime(created=now_ms()),
        )
        message_with_parts = MessageWithParts(info=assistant_message, parts=[])
        state.messages[session_id].append(message_with_parts)
        await state.broadcast_event(MessageUpdatedEvent.create(assistant_message))

        # Add step-start part
        step_start = StepStartPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg_id,
            session_id=session_id,
        )
        message_with_parts.parts.append(step_start)
        await state.broadcast_event(PartUpdatedEvent.create(step_start))

        # Run agent with the user message context
        try:
            adapter = OpenCodeStreamAdapter(
                state=state,
                session_id=session_id,
                assistant_msg_id=assistant_msg_id,
                assistant_msg=message_with_parts,
                working_dir=state.working_dir,
            )

            # Run agent with the user prompt
            iterator = state.agent.run_stream(user_prompt, session_id=session_id)
            async for oc_event in adapter.process_stream(iterator):
                await state.broadcast_event(oc_event)

        except Exception as e:
            error_text = f"Error: {e}"
            text_part = TextPart(
                id=identifier.ascending("part"),
                message_id=assistant_msg_id,
                session_id=session_id,
                text=error_text,
            )
            message_with_parts.parts.append(text_part)
            await state.broadcast_event(PartUpdatedEvent.create(text_part))

        # Add step-finish part
        step_finish = StepFinishPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg_id,
            session_id=session_id,
        )
        message_with_parts.parts.append(step_finish)
        await state.broadcast_event(PartUpdatedEvent.create(step_finish))

        # Broadcast command.executed event
        await state.broadcast_event(
            CommandExecutedEvent.create(
                name=request.command,
                session_id=session_id,
                arguments=request.arguments or "",
                message_id=assistant_msg_id,
            )
        )
    finally:
        await state.mark_session_idle(session_id)

    return message_with_parts


async def get_or_load_session(state: ServerState, session_id: str) -> Session | None:
    """Get session from cache or load via agent.

    Returns None if session not found.
    Uses agent.load_session() which handles loading from the appropriate
    storage (pool storage, Claude storage, ACP server, Codex, etc.).

    Important: This function ensures the agent's conversation history is always
    synchronized with the requested session. Even if the session is cached,
    if the agent currently has a different session loaded, the history will be
    reloaded to prevent cross-session contamination.

    For subagent sessions (child sessions), we prioritize the in-memory version
    because parts are streamed in real-time and may not be immediately persisted
    to storage. This ensures users see the latest message state when viewing
    subagent sessions.
    """
    # Check if session is cached AND agent has the correct session loaded
    agent_has_correct_session = (
        state.agent.session_id == session_id
        and session_id in state.sessions
        and session_id in state.messages
    )

    if agent_has_correct_session:
        # Session cached and agent has correct history - safe to return
        return state.sessions[session_id]

    # For subagent/child sessions: prioritize in-memory messages if available
    # This is critical because subagent parts are streamed in real-time to memory
    # but are only persisted at completion, not after each part update
    # A session is considered a subagent session if it has a parent_id
    cached_session = state.sessions.get(session_id)
    is_subagent_session = cached_session is not None and cached_session.parent_id is not None

    if is_subagent_session and session_id in state.messages:
        # Subagent session exists in memory with messages - return it directly
        # This avoids overwriting real-time streamed parts with stale storage data
        return cached_session

    # Need to load/reload session history into agent
    # This happens when:
    # 1. Session not in cache (new session)
    # 2. Agent has different session loaded (session switch)
    # 3. Session in cache but no messages (e.g., subagent session not yet populated)

    # Check if we have in-memory messages before reloading (for subagent sessions)
    existing_messages = state.messages.get(session_id) if is_subagent_session else None

    data = await state.agent.load_session(session_id)
    if data is None:
        return None

    # Convert SessionData to OpenCode Session
    session = session_data_to_opencode(data)
    # Cache the session
    state.sessions[session_id] = session
    state.ensure_runtime_session_state(session_id)
    # Initialize runtime state
    if session_id not in state.session_status:
        await state.mark_session_idle(session_id)

    # For subagent sessions with existing in-memory messages, preserve them
    # Subagent messages are streamed in real-time and may not be persisted yet
    if is_subagent_session and existing_messages:
        # Keep existing in-memory messages (they're more recent than storage)
        # Only update if memory is empty
        pass  # existing_messages already in state.messages[session_id]
    else:
        # Convert agent's conversation history to OpenCode format
        # This is for regular sessions or sessions not yet in memory
        state.messages[session_id] = [
            chat_message_to_opencode(
                chat_msg,
                session_id=session_id,
                working_dir=state.working_dir,
                agent_name=state.agent.name,
                model_id=chat_msg.model_name or "sonnet",  # Normalized name from Claude storage
                provider_id=chat_msg.provider_name or "claude-code",
            )
            for chat_msg in state.agent.conversation.chat_messages
        ]
    # Create input provider for this session if not exists
    if session_id not in state.input_providers:
        input_provider = OpenCodeInputProvider(state, session_id)
        state.input_providers[session_id] = input_provider
    # Set input provider on agent to ensure correct session routing.
    # NOTE: The agent singleton is shared across sessions. Setting _input_provider
    # and session_id here is safe for single-session use but creates a race if
    # two sessions run concurrently — the later session overwrites the earlier.
    # Per-session locks (session_locks) prevent message-processing races, but a
    # full fix would pass session context through the execution path instead of
    # mutating the shared agent instance.
    state.agent._input_provider = state.input_providers[session_id]
    state.agent.session_id = session_id
    return session


router = APIRouter(prefix="/session", tags=["session"])


@router.get("")
async def list_sessions(
    state: StateDep,
    directory: str | None = None,
    roots: bool | None = None,
    start: int | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> list[Session]:
    """List all sessions from the agent.

    Delegates to agent.list_sessions() which handles fetching sessions
    from the appropriate storage (pool storage, Claude storage, ACP server, etc.).

    Query params:
        directory: Filter sessions by directory (overrides default cwd).
                   The OpenCode SDK auto-injects this param.
        roots: Only return root sessions (no parentID)
        start: Filter sessions updated on or after this timestamp (ms since epoch)
        search: Filter sessions by title (case-insensitive)
        limit: Maximum number of sessions to return
    """
    # Use directory param if provided, otherwise fall back to state.base_path
    # which resolves to agent.env.cwd (from YAML environment config) or working_dir
    effective_cwd = directory or state.base_path
    # Convert to OpenCode Session format and cache
    sessions: list[Session] = []
    for data in await state.agent.list_sessions(cwd=effective_cwd):
        session = session_data_to_opencode(data)
        # Cache in state for later use
        state.sessions[data.session_id] = session
        sessions.append(session)
    # Apply filters
    if roots:
        sessions = [s for s in sessions if s.parent_id is None]
    if start is not None:
        sessions = [s for s in sessions if s.time.updated >= start]
    if search:
        lower_search = search.lower()
        sessions = [s for s in sessions if lower_search in s.title.lower()]
    if limit is not None:
        sessions = sessions[:limit]
    return sessions


@router.post("")
async def create_session(state: StateDep, request: SessionCreateRequest | None = None) -> Session:
    """Create a new session and persist to storage."""
    now = now_ms()
    session_id = identifier.ascending("session")
    base_path = state.base_path
    project_id = helpers.compute_project_id(base_path)
    session = Session(
        id=session_id,
        project_id=project_id,
        directory=base_path,
        title=request.title if request and request.title else "New Session",
        version="1",
        time=TimeCreatedUpdated(created=now, updated=now),
        parent_id=request.parent_id if request else None,
    )

    # Persist to storage
    id_ = state.pool.manifest.config_file_path
    session_data = opencode_to_session_data(session, agent_name=state.agent.name, pool_id=id_)
    if state.pool.sessions.store:
        await state.pool.sessions.store.save(session_data)
    # Cache in memory
    state.sessions[session_id] = session
    state.messages[session_id] = []
    await state.mark_session_idle(session_id)
    state.todos[session_id] = []
    # Create input provider for this session
    input_provider = OpenCodeInputProvider(state, session_id)
    state.input_providers[session_id] = input_provider
    # Set input provider on agent.
    # NOTE: See load_session for the shared-agent race-condition caveat.
    state.agent._input_provider = input_provider
    # Clear agent's conversation for the new session
    # Agent is shared across sessions, so we need to clear its conversation state
    if hasattr(state.agent, "conversation") and state.agent.conversation:
        state.agent.conversation.chat_messages.clear()
    # Update agent's session_id to the new session
    state.agent.session_id = session_id
    await state.broadcast_event(SessionCreatedEvent.create(session))
    return session


@router.get("/status")
async def get_session_status(state: StateDep) -> dict[str, SessionStatus]:
    """Get status for all sessions.

    Returns only non-idle sessions. If all sessions are idle, returns empty dict.
    """
    return {sid: status for sid, status in state.session_status.items() if status.type != "idle"}


@router.get("/{session_id}")
async def get_session(session_id: str, state: StateDep) -> Session:
    """Get session details.

    Loads from storage if not in memory cache.
    """
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/{session_id}/message")
async def get_session_messages(
    session_id: str,
    state: StateDep,
    limit: int | None = None,
) -> list[MessageWithParts]:
    """Get all messages for a session.

    Retrieves all messages in a session, including user prompts and AI responses.
    Loads from storage if session not in memory cache.

    Args:
        session_id: Unique identifier for the session
        limit: Optional maximum number of messages to return

    Returns:
        List of messages with their parts
    """
    # Ensure session is loaded
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = state.messages.get(session_id, [])
    if limit is not None and limit > 0:
        messages = messages[-limit:]
    return messages


@router.get("/{session_id}/children")
async def get_session_children(
    session_id: str,
    state: StateDep,
) -> list[Session]:
    """Get all child sessions for a given session.

    Returns a list of sessions where parent_id matches the provided session_id.
    Queries both memory cache and database for complete results.
    """
    children: list[Session] = []
    seen_ids: set[str] = set()

    # Check all cached sessions first
    for session in state.sessions.values():
        if session.parent_id == session_id:
            children.append(session)
            seen_ids.add(session.id)

    # Query database for child sessions not in memory
    try:
        store = state.pool.sessions.store
        if hasattr(store, "list_sessions"):
            child_ids = await store.list_sessions(parent_id=session_id)
            for child_id in child_ids:
                if child_id not in seen_ids:
                    child_session = await get_or_load_session(state, child_id)
                    if child_session:
                        children.append(child_session)
                        seen_ids.add(child_id)
    except Exception:  # noqa: BLE001
        # Graceful fallback if store doesn't support list_sessions or query fails
        pass

    return children


@router.patch("/{session_id}")
async def update_session(
    session_id: str,
    request: SessionUpdateRequest,
    state: StateDep,
) -> Session:
    """Update session properties and persist changes.

    Supports updating title and archiving via time.archived.
    """
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    updates: dict[str, Any] = {}
    if request.title is not None:
        updates["title"] = request.title
    # Always update the 'updated' timestamp
    updates["time"] = TimeCreatedUpdated(created=session.time.created, updated=now_ms())
    session = session.model_copy(update=updates)
    state.sessions[session_id] = session  # Update cache
    id_ = state.pool.manifest.config_file_path
    session_data = opencode_to_session_data(session, agent_name=state.agent.name, pool_id=id_)
    if state.pool.sessions.store:
        await state.pool.sessions.store.save(session_data)
    await state.broadcast_event(SessionUpdatedEvent.create(session))
    return session


@router.delete("/{session_id}")
async def delete_session(session_id: str, state: StateDep) -> bool:
    """Delete a session from both cache and storage."""
    # Check if session exists (in cache or storage)
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Cancel any pending permissions and clean up input provider
    if input_provider := state.input_providers.pop(session_id, None):
        input_provider.cancel_all_pending()

    # Remove from cache
    state.sessions.pop(session_id, None)
    state.messages.pop(session_id, None)
    state.session_status.pop(session_id, None)
    state.todos.pop(session_id, None)
    # Delete from storage
    if state.pool.sessions.store:
        await state.pool.sessions.store.delete(session_id)
    await state.broadcast_event(SessionDeletedEvent.create(session_id))
    return True


@router.post("/{session_id}/abort")
async def abort_session(session_id: str, state: StateDep) -> bool:
    """Abort a running session by interrupting the agent."""
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Stop any in-flight prompt worker for this session before interrupting the agent.
    await state.cancel_session_background_tasks(session_id)

    # Interrupt the agent to cancel any ongoing stream
    try:
        await state.agent.interrupt()
        # Give a moment for the cancellation to propagate
        await asyncio.sleep(0.1)
    except Exception:  # noqa: BLE001
        pass

    # Update and broadcast session status to notify clients
    state.session_status[session_id] = SessionStatus(type="idle")
    await state.broadcast_event(SessionStatusEvent.create(session_id, SessionStatus(type="idle")))
    await state.broadcast_event(SessionIdleEvent.create(session_id))
    return True


@router.post("/{session_id}/fork")
async def fork_session(  # noqa: D417
    session_id: str,
    state: StateDep,
    request: SessionForkRequest | None = None,
    directory: str | None = None,
) -> Session:
    """Fork a session, optionally at a specific message.

    Creates a new session with:
    - parent_id pointing to the original session
    - Copies all messages (or up to message_id if specified)
    - Independent conversation history from that point forward

    Args:
        session_id: The session to fork from
        request: Optional fork parameters (message_id to fork from)
        directory: Optional directory for the forked session

    Returns:
        The newly created forked session
    """
    # Get the original session
    original_session = await get_or_load_session(state, session_id)
    if original_session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get messages from the original session
    original_messages = state.messages.get(session_id, [])
    # Filter messages if message_id is specified
    messages_to_copy: list[MessageWithParts] = []
    if request and request.message_id:
        # Copy messages up to and including the specified message_id
        for msg in original_messages:
            messages_to_copy.append(msg)
            if msg.info.id == request.message_id:
                break
        else:
            # message_id not found in messages
            detail = f"Message {request.message_id} not found in session"
            raise HTTPException(status_code=404, detail=detail)
    else:
        # Copy all messages
        messages_to_copy = list(original_messages)

    # Create the new forked session
    now = now_ms()
    new_session_id = identifier.ascending("session")
    # Use provided directory or inherit from original session
    fork_directory = directory if directory else original_session.directory
    forked_session = Session(
        id=new_session_id,
        project_id=original_session.project_id,
        directory=fork_directory,
        title=f"{original_session.title} (fork)",
        version="1",
        time=TimeCreatedUpdated(created=now, updated=now),
        parent_id=session_id,  # Link to original session
    )

    # Persist the forked session to storage
    session_data = opencode_to_session_data(
        forked_session,
        agent_name=state.agent.name,
        pool_id=state.pool.manifest.config_file_path,
    )
    if state.pool.sessions.store:
        await state.pool.sessions.store.save(session_data)
    # Cache in memory
    state.sessions[new_session_id] = forked_session
    await state.mark_session_idle(new_session_id)
    state.todos[new_session_id] = []
    # Copy messages to the new session (with updated session_id references)
    copied_messages: list[MessageWithParts] = []
    for msg_with_parts in messages_to_copy:
        # Create new message info with updated session_id
        new_info = msg_with_parts.info.model_copy(update={"session_id": new_session_id})
        # Copy parts with updated session_id
        new_parts = [
            part.model_copy(update={"session_id": new_session_id}) for part in msg_with_parts.parts
        ]
        copied_messages.append(MessageWithParts(info=new_info, parts=new_parts))

    state.messages[new_session_id] = copied_messages
    input_provider = OpenCodeInputProvider(state, new_session_id)
    state.input_providers[new_session_id] = input_provider
    # Broadcast session created event
    await state.broadcast_event(SessionCreatedEvent.create(forked_session))
    return forked_session


@router.post("/{session_id}/init")
async def init_session(  # noqa: D417
    session_id: str,
    state: StateDep,
    request: SessionInitRequest | None = None,
) -> bool:
    """Initialize a session by analyzing the codebase and creating AGENTS.md.

    Generates a repository map, reads README if present, and runs the agent
    with a prompt to create an AGENTS.md file with project-specific context.

    Args:
        session_id: The session to initialize
        request: Optional model/provider override for the init task

    Returns:
        True when the init task has been started (runs async)
    """
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the agent and filesystem
    agent = state.agent
    fs = state.fs
    working_dir = state.working_dir
    try:
        all_files = await find_src_files(fs, working_dir)
        repo_map = RepoMap(fs=fs, root_path=working_dir, max_tokens=4000)
        repomap_content = await repo_map.get_map(all_files) or "No repository map generated."
    except Exception as e:  # noqa: BLE001
        repomap_content = f"Error generating repository map: {e}"

    # Try to read README.md
    readme_content = ""
    for readme_name in ["README.md", "readme.md", "README", "readme.txt"]:
        try:
            readme_path = f"{working_dir}/{readme_name}".replace("//", "/")
            content = await fs._cat_file(readme_path)
            readme_content = content.decode("utf-8") if isinstance(content, bytes) else content
            break
        except Exception:  # noqa: BLE001
            continue

    # Build the init prompt
    prompt_parts = [
        "Please analyze this codebase and create an AGENTS.md file in the project root.",
        "",
        "<repository-structure>",
        repomap_content,
        "</repository-structure>",
    ]
    if readme_content:
        prompt_parts.extend(["", "<readme>", readme_content, "</readme>"])
    prompt_parts.extend([
        "",
        "Include:",
        "1. Build/lint/test commands - especially for running a single test",
        "2. Code style guidelines (imports, formatting, types, naming conventions, error handling)",
        "",
        "The file will be given to AI coding agents working in this repository. "
        "Keep it around 150 lines.",
        "",
        "If there are existing rules (.cursor/rules/, .cursorrules, "
        ".github/copilot-instructions.md), incorporate them.",
    ])

    init_prompt = "\n".join(prompt_parts)

    # Handle model selection if requested
    original_model: str | None = None
    if request and request.model_id and request.provider_id:
        requested_model = f"{request.provider_id}:{request.model_id}"
        try:
            available_models = await agent.get_available_models()
            if available_models:
                valid_ids = [m.id_override if m.id_override else m.id for m in available_models]
                if requested_model in valid_ids:
                    # Store original model to restore later
                    original_model = agent.model_name
                    await agent.set_model(requested_model)
        except Exception:  # noqa: BLE001
            # Agent doesn't support model selection, ignore
            pass

    # Run the agent in the background
    async def run_init() -> None:
        try:
            await agent.run(init_prompt)
        finally:
            # Restore original model if we changed it
            if original_model is not None:
                with contextlib.suppress(Exception):
                    await agent.set_model(original_model)

    state.create_background_task(run_init(), name=f"init_{session_id}")

    return True


@router.get("/{session_id}/todo")
async def get_session_todos(session_id: str, state: StateDep) -> list[Todo]:
    """Get todos for a session.

    Returns todos from the agent pool's TodoTracker.
    """
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get todos from pool's TodoTracker
    tracker = state.pool.todos
    return [
        Todo(id=e.id, content=e.content, status=e.status, priority=e.priority)
        for e in tracker.entries
    ]


@router.get("/{session_id}/diff")
async def get_session_diff(
    session_id: str,
    state: StateDep,
    message_id: str | None = None,
) -> list[FileDiff]:
    """Get file diffs for a session.

    Returns a list of file changes with unified diffs.
    Optionally filter to changes since a specific message.
    """
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    file_ops = state.pool.file_ops
    if not file_ops.changes:
        return []
    # Optionally filter by message_id
    changes = file_ops.get_changes_since(message_id) if message_id else file_ops.changes
    return [FileDiff.from_file_change(change) for change in changes]


@router.post("/{session_id}/shell")
async def run_shell_command(
    session_id: str,
    request: ShellRequest,
    state: StateDep,
) -> MessageWithParts:
    """Run a shell command directly."""
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Validate command for security issues
    validate_command(request.command, state.working_dir)
    now = now_ms()
    # Create assistant message for the shell output
    assistant_msg_id = identifier.ascending("message")
    assistant_message = AssistantMessage(
        id=assistant_msg_id,
        session_id=session_id,
        parent_id="",  # Shell commands don't have a parent user message
        model_id=request.model.model_id if request.model else "shell",
        provider_id=request.model.provider_id if request.model else "local",
        mode="shell",
        agent=request.agent,
        path=MessagePath(cwd=state.working_dir, root=state.working_dir),
        time=MessageTime(created=now),
    )

    # Initialize message with empty parts
    assistant_msg_with_parts = MessageWithParts(info=assistant_message, parts=[])
    state.messages[session_id].append(assistant_msg_with_parts)
    # Broadcast message created
    await state.broadcast_event(MessageUpdatedEvent.create(assistant_message))
    try:
        # Mark session as busy
        state.session_status[session_id] = SessionStatus(type="busy")
        await state.broadcast_event(
            SessionStatusEvent.create(session_id, SessionStatus(type="busy"))
        )
        # Add step-start part
        part_id = identifier.ascending("part")
        step_start = StepStartPart(id=part_id, message_id=assistant_msg_id, session_id=session_id)
        assistant_msg_with_parts.parts.append(step_start)
        await state.broadcast_event(PartUpdatedEvent.create(step_start))
        # Execute the command
        output_text = ""
        success = False
        try:
            result = await state.agent.env.execute_command(request.command)
            success = result.success
            if success:
                output_text = str(result.result) if result.result else ""
            else:
                output_text = f"Error: {result.error}" if result.error else "Command failed"
        except Exception as e:  # noqa: BLE001
            output_text = f"Error executing command: {e}"

        response_time = now_ms()
        # Create text part with output
        text_part = TextPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg_id,
            session_id=session_id,
            text=f"$ {request.command}\n{output_text}",
        )
        assistant_msg_with_parts.parts.append(text_part)
        await state.broadcast_event(PartUpdatedEvent.create(text_part))
        part_id = identifier.ascending("part")
        step_finish = StepFinishPart(id=part_id, message_id=assistant_msg_id, session_id=session_id)
        assistant_msg_with_parts.parts.append(step_finish)
        await state.broadcast_event(PartUpdatedEvent.create(step_finish))
        # Update message with completion time
        time_ = MessageTime(created=now, completed=response_time)
        updated_assistant = assistant_message.model_copy(update={"time": time_})
        assistant_msg_with_parts.info = updated_assistant
        await state.broadcast_event(MessageUpdatedEvent.create(updated_assistant))
    finally:
        await state.mark_session_idle(session_id)
    return assistant_msg_with_parts


@router.get("/{session_id}/permissions")
async def get_pending_permissions(
    session_id: str, state: StateDep
) -> list[PermissionAskedProperties]:
    """Get all pending permission requests for a session.

    Returns a list of pending permissions awaiting user response.
    """
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the input provider for this session
    input_provider = state.input_providers.get(session_id)
    if input_provider is None:
        return []

    return input_provider.get_pending_permissions()


@router.post("/{session_id}/permissions/{permission_id}")
async def respond_to_permission(
    session_id: str,
    permission_id: str,
    body: PermissionReplyRequest,
    state: StateDep,
) -> bool:
    """Respond to a pending permission request.

    The response can be:
    - "once": Allow this tool execution once
    - "always": Always allow this tool (remembered for session)
    - "reject": Reject this tool execution
    """
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the input provider for this session
    input_provider = state.input_providers.get(session_id)
    if input_provider is None:
        raise HTTPException(status_code=404, detail="No input provider for session")

    # Resolve the permission
    resolved = input_provider.resolve_permission(permission_id, body.reply)
    if not resolved:
        raise HTTPException(status_code=404, detail="Permission not found or already resolved")
    event = PermissionResolvedEvent.create(
        session_id=session_id,
        request_id=permission_id,
        reply=body.reply,
    )
    await state.broadcast_event(event)
    return True


# OpenCode-style continuation prompt for summarization
SUMMARIZE_PROMPT = """Provide a detailed prompt for continuing our conversation above. Focus on information that would be helpful for continuing the conversation, including what we did, what we're doing, which files we're working on, and what we're going to do next considering new session will not have access to our conversation."""  # noqa: E501


@router.post("/{session_id}/summarize")
async def summarize_session(  # noqa: PLR0915
    session_id: str,
    state: StateDep,
    request: SummarizeRequest | None = None,
) -> MessageWithParts:
    """Summarize the session conversation.

    First runs the compaction pipeline to condense older messages,
    then streams an LLM-generated summary/continuation prompt to the user.
    The summary message is marked with summary=true for UI display.
    """
    from pydantic_ai.messages import (
        PartDeltaEvent as PydanticPartDeltaEvent,
        PartStartEvent,
        TextPart as PydanticTextPart,
        TextPartDelta,
    )

    from agentpool.agents.events import StreamCompleteEvent
    from agentpool.messaging.compaction import compact_conversation, summarizing_context

    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not state.messages.get(session_id):
        raise HTTPException(status_code=400, detail="No messages to summarize")

    # Determine model to use
    model_id = request.model_id if request and request.model_id else "default"
    provider_id = request.provider_id if request and request.provider_id else "agentpool"

    now = now_ms()
    # Create assistant message for the summary (marked with summary=true)
    assistant_msg_id = identifier.ascending("message")
    assistant_message = AssistantMessage(
        id=assistant_msg_id,
        session_id=session_id,
        parent_id="",
        model_id=model_id,
        provider_id=provider_id,
        mode="summarize",
        agent="summarizer",
        path=MessagePath(cwd=state.working_dir, root=state.working_dir),
        time=MessageTime(created=now),
        summary=True,  # Mark as summary message
    )

    assistant_msg_with_parts = MessageWithParts(info=assistant_message, parts=[])
    state.messages[session_id].append(assistant_msg_with_parts)
    # Broadcast message created
    await state.broadcast_event(MessageUpdatedEvent.create(assistant_message))
    try:
        # Mark session as busy
        state.session_status[session_id] = SessionStatus(type="busy")
        await state.broadcast_event(
            SessionStatusEvent.create(session_id, SessionStatus(type="busy"))
        )
        # Add step-start part
        part_id = identifier.ascending("part")
        step_start = StepStartPart(id=part_id, message_id=assistant_msg_id, session_id=session_id)
        assistant_msg_with_parts.parts.append(step_start)
        await state.broadcast_event(PartUpdatedEvent.create(step_start))
        # Step 1: Stream LLM summary generation FIRST (while we have full history)
        # The LLM sees the complete conversation and generates a continuation prompt.
        response_text = ""
        usage = None
        cost = 0.0
        text_part: TextPart | None = None
        try:
            # Stream events from the agent with the summarization prompt
            # This runs with FULL history - the summary is based on complete context
            async for event in state.agent.run_stream(SUMMARIZE_PROMPT):
                match event:
                    # Text streaming start
                    case PartStartEvent(part=PydanticTextPart(content=delta)):
                        response_text = delta
                        text_part = TextPart(
                            id=identifier.ascending("part"),
                            message_id=assistant_msg_id,
                            session_id=session_id,
                            text=delta,
                        )
                        assistant_msg_with_parts.parts.append(text_part)
                        await state.broadcast_event(PartUpdatedEvent.create(text_part))

                    # Text streaming delta
                    case PydanticPartDeltaEvent(delta=TextPartDelta(content_delta=delta)) if delta:
                        response_text += delta
                        if text_part is not None:
                            text_part = TextPart(
                                id=text_part.id,
                                message_id=assistant_msg_id,
                                session_id=session_id,
                                text=response_text,
                            )
                            # Update in parts list
                            for i, p in enumerate(assistant_msg_with_parts.parts):
                                if isinstance(p, TextPart) and p.id == text_part.id:
                                    assistant_msg_with_parts.parts[i] = text_part
                                    break
                            await state.broadcast_event(
                                PartDeltaEvent.create(
                                    session_id=session_id,
                                    message_id=assistant_msg_id,
                                    part_id=text_part.id,
                                    delta=delta,
                                )
                            )

                    # Stream complete - extract token usage
                    case StreamCompleteEvent(message=msg) if msg and msg.usage:
                        usage = msg.usage
                        cost = float(msg.cost_info.total_cost) if msg.cost_info else 0

        except Exception as e:  # noqa: BLE001
            response_text = f"Error generating summary: {e}"

        response_time = now_ms()
        # Create/update text part with final response
        if text_part is None:
            text_part = TextPart(
                id=identifier.ascending("part"),
                message_id=assistant_msg_id,
                session_id=session_id,
                text=response_text,
            )
            assistant_msg_with_parts.parts.append(text_part)
            await state.broadcast_event(PartUpdatedEvent.create(text_part))

        # Step 2: Run compaction pipeline AFTER summary is generated
        # The summary was generated with full context. Now we compact the history.
        # Final state will be: [compacted history] + [summary message]
        # The compacted history becomes the cached prefix for future LLM calls.
        try:
            # Get the compaction pipeline from the agent pool configuration
            pipeline = None
            if state.agent.agent_pool is not None:
                pipeline = state.agent.agent_pool.compaction_pipeline
            if pipeline is None:
                # Fall back to a default summarizing pipeline
                pipeline = summarizing_context()

            # Apply the compaction pipeline (modifies agent.conversation in place)
            await compact_conversation(pipeline, state.agent.conversation)
            # Persist compacted messages to storage, replacing the old ones
            if state.storage is not None:
                compacted_history = state.agent.conversation.get_history()
                await state.storage.replace_conversation_messages(session_id, compacted_history)
            # Update in-memory OpenCode messages list with compacted versions
            # Keep only the summary message we just created
            state.messages[session_id] = [assistant_msg_with_parts]

        except Exception:  # noqa: BLE001
            # Compaction failure is not fatal - we still have the summary
            pass
        tokens = Tokens.from_pydantic_ai(usage) if usage else Tokens()
        # Add step-finish part
        step_finish = StepFinishPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg_id,
            session_id=session_id,
            tokens=tokens,
            cost=cost,
        )
        assistant_msg_with_parts.parts.append(step_finish)
        await state.broadcast_event(PartUpdatedEvent.create(step_finish))
        # Update message with completion time and tokens
        msg_time = MessageTime(created=now, completed=response_time)
        update = {"time": msg_time, "tokens": tokens, "cost": cost}
        updated_assistant = assistant_message.model_copy(update=update)
        assistant_msg_with_parts.info = updated_assistant
        await state.broadcast_event(MessageUpdatedEvent.create(updated_assistant))

        # Broadcast session.diff event after summarization
        file_ops = state.pool.file_ops
        diffs = [FileDiff.from_file_change(change) for change in file_ops.changes]
        await state.broadcast_event(SessionDiffEvent.create(session_id, diffs))
    finally:
        await state.mark_session_idle(session_id)

    return assistant_msg_with_parts


@router.post("/{session_id}/share")
async def share_session(
    session_id: str,
    state: StateDep,
    num_messages: int | None = None,
) -> Session:
    """Share session conversation history via OpenCode's sharing service.

    Uses the OpenCode share API to create a shareable link with the full
    conversation including messages and parts.

    Returns the updated session with the share URL.
    """
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = state.messages.get(session_id, [])

    if not messages:
        raise HTTPException(status_code=400, detail="No messages to share")

    # Apply message limit if specified
    if num_messages is not None and num_messages > 0:
        messages = messages[-num_messages:]
    # Convert our messages to OpenCode Message format
    opencode_messages: list[Message] = []
    for msg_with_parts in messages:
        # Extract text parts
        parts = [
            MessagePart(type="text", text=part.text)
            for part in msg_with_parts.parts
            if isinstance(part, TextPart) and part.text
        ]
        if parts:
            opencode_messages.append(Message(role=msg_with_parts.info.role, parts=parts))
    if not opencode_messages:
        raise HTTPException(status_code=400, detail="No content to share")

    # Share via OpenCode API
    async with OpenCodeSharer() as sharer:
        result = await sharer.share_conversation(opencode_messages, title=session.title)
        share_url = result.url
    # Store the share URL in the session
    share_info = SessionShare(url=share_url)
    updated_session = session.model_copy(update={"share": share_info})
    state.sessions[session_id] = updated_session
    # Broadcast session update
    await state.broadcast_event(SessionUpdatedEvent.create(updated_session))
    return updated_session


class RevertRequest(OpenCodeBaseModel):
    """Request body for reverting a message."""

    message_id: str
    part_id: str | None = None


@router.post("/{session_id}/revert")
async def revert_session(session_id: str, request: RevertRequest, state: StateDep) -> Session:
    """Revert file changes and messages from a specific message.

    Removes messages from the revert point onwards and restores files to their
    state before the specified message's changes.
    """
    from agentpool_server.opencode_server.models import MessageRemovedEvent, PartRemovedEvent

    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get messages for this session
    messages = state.messages.get(session_id, [])
    if not messages:
        raise HTTPException(status_code=400, detail="No messages to revert")

    # Find the revert message index
    revert_index = None
    for i, msg in enumerate(messages):
        if msg.info.id == request.message_id:
            revert_index = i
            break

    if revert_index is None:
        raise HTTPException(status_code=404, detail=f"Message {request.message_id} not found")

    # Split messages: keep messages before revert point, remove from revert point onwards
    messages_to_keep = messages[:revert_index]
    messages_to_remove = messages[revert_index:]

    if not messages_to_remove:
        raise HTTPException(status_code=400, detail="No messages to revert")

    # Store removed messages for unrevert
    state.reverted_messages[session_id] = messages_to_remove
    # Update message list - keep only messages before revert point
    state.messages[session_id] = messages_to_keep
    # Emit message.removed and part.removed events for all removed messages
    for msg in messages_to_remove:
        # Emit message.removed event
        await state.broadcast_event(MessageRemovedEvent.create(session_id, msg.info.id))

        # Emit part.removed events for all parts
        for part in msg.parts:
            await state.broadcast_event(PartRemovedEvent.create(session_id, msg.info.id, part.id))

    # Also revert file changes if any
    file_ops = state.pool.file_ops
    if file_ops.changes and (
        revert_ops := file_ops.get_revert_operations(since_message_id=request.message_id)
    ):
        for path, content in revert_ops:
            try:
                if content is None:
                    await state.fs._rm_file(path)
                else:
                    content_bytes = content.encode("utf-8")
                    await state.fs._pipe_file(path, content_bytes)
            except Exception as e:
                detail = f"Failed to revert {path}: {e}"
                raise HTTPException(status_code=500, detail=detail) from e
        file_ops.remove_changes_since(request.message_id)

    # Update session with revert info
    session = state.sessions[session_id]
    revert_info = SessionRevert(message_id=request.message_id, part_id=request.part_id)
    updated_session = session.model_copy(update={"revert": revert_info})
    state.sessions[session_id] = updated_session

    # Broadcast session update
    await state.broadcast_event(SessionUpdatedEvent.create(updated_session))

    # Broadcast session.diff event with current file diffs
    file_ops = state.pool.file_ops
    diffs = [FileDiff.from_file_change(change) for change in file_ops.changes]
    await state.broadcast_event(SessionDiffEvent.create(session_id, diffs))

    return updated_session


@router.post("/{session_id}/unrevert")
async def unrevert_session(session_id: str, state: StateDep) -> Session:
    """Restore all reverted messages and file changes.

    Re-applies the messages and changes that were previously reverted.
    """
    from agentpool_server.opencode_server.models import MessageUpdatedEvent, PartUpdatedEvent

    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Restore reverted messages
    reverted_messages = state.reverted_messages.get(session_id, [])
    if not reverted_messages:
        raise HTTPException(status_code=400, detail="No reverted messages to restore")

    # Restore messages to conversation
    if session_id not in state.messages:
        state.messages[session_id] = []
    state.messages[session_id].extend(reverted_messages)

    # Emit message.updated and part.updated events for restored messages
    for msg in reverted_messages:
        # Emit message.updated event
        await state.broadcast_event(MessageUpdatedEvent.create(msg.info))
        # Emit part.updated events for all parts
        for part in msg.parts:
            await state.broadcast_event(PartUpdatedEvent.create(part))

    # Clear reverted messages
    state.reverted_messages.pop(session_id, None)

    # Also unrevert file changes if any
    file_ops = state.pool.file_ops
    if file_ops.reverted_changes:
        unrevert_ops = file_ops.get_unrevert_operations()
        for path, content in unrevert_ops:
            try:
                if content is None:
                    await state.fs._rm_file(path)
                else:
                    content_bytes = content.encode("utf-8")
                    await state.fs._pipe_file(path, content_bytes)
            except Exception as e:
                detail = f"Failed to unrevert {path}: {e}"
                raise HTTPException(status_code=500, detail=detail) from e
        file_ops.restore_reverted_changes()

    # Clear revert info from session
    updated_session = session.model_copy(update={"revert": None})
    state.sessions[session_id] = updated_session
    # Broadcast session update
    await state.broadcast_event(SessionUpdatedEvent.create(updated_session))
    return updated_session


@router.delete("/{session_id}/share")
async def unshare_session(session_id: str, state: StateDep) -> bool:
    """Remove share link from a session.

    Note: This only removes the link from the session metadata.
    The shared content may still exist on the provider's servers.
    """
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.share is None:
        raise HTTPException(status_code=400, detail="Session is not shared")
    # Remove share info from session
    updated_session = session.model_copy(update={"share": None})
    state.sessions[session_id] = updated_session
    # Broadcast session update
    await state.broadcast_event(SessionUpdatedEvent.create(updated_session))
    return True


@router.post("/{session_id}/command")
async def execute_command(  # noqa: PLR0915
    session_id: str,
    request: CommandRequest,
    state: StateDep,
) -> MessageWithParts:
    """Execute a slash command (CommandStore or MCP prompt).

    Commands are resolved in order: first checked against CommandStore (for
    slashed/skill commands), then against MCP prompts. This provides unified
    command execution across both systems.
    """
    session = await get_or_load_session(state, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check CommandStore first (slashed commands take priority)
    if state.command_store and state.command_store.get_command(request.command) is not None:
        # Check for collision with MCP prompts
        prompts = await state.agent.tools.list_prompts()
        if any(p.name == request.command for p in prompts):
            logger.warning(
                "Both slashed command and prompt exist for '%s'. Using slashed command.",
                request.command,
            )
        return await _execute_slashed_command(state, session_id, request)

    # Fallback: check pool.skill_commands directly when CommandStore misses
    # This handles cases where skills were registered after CommandStore init
    # or where the CommandStore sync callback hasn't fired yet
    if state.pool.skill_commands and request.command in state.pool.skill_commands:
        logger.debug(
            "Command '%s' found in skill_commands but not CommandStore, executing as skill",
            request.command,
        )
        return await _execute_skill_command(state, session_id, request)

    # Fall back to MCP prompts (existing code remains unchanged)
    prompts = await state.agent.tools.list_prompts()
    # Find matching prompt by name
    prompt = next((p for p in prompts if p.name == request.command), None)
    if prompt is None:
        detail = f"Command not found: {request.command}"
        raise HTTPException(status_code=404, detail=detail)

    # Parse arguments - OpenCode uses $1, $2 style, MCP uses named arguments
    # For simplicity, we'll pass the raw arguments string to the first argument
    # or parse space-separated args into a dict
    arguments: dict[str, str] = {}
    if request.arguments and prompt.arguments:
        # Split arguments and map to prompt argument names
        arg_values = request.arguments.split()
        for i, arg_def in enumerate(prompt.arguments):
            if i < len(arg_values):
                arguments[arg_def["name"]] = arg_values[i]

    now = now_ms()
    # Create assistant message
    assistant_msg_id = identifier.ascending("message")
    assistant_message = AssistantMessage(
        id=assistant_msg_id,
        session_id=session_id,
        parent_id="",
        model_id=request.model or "default",
        provider_id="mcp",
        mode="command",
        agent=request.agent or "default",
        path=MessagePath(cwd=state.working_dir, root=state.working_dir),
        time=MessageTime(created=now),
    )
    assistant_msg_with_parts = MessageWithParts(info=assistant_message, parts=[])
    state.messages[session_id].append(assistant_msg_with_parts)
    await state.broadcast_event(MessageUpdatedEvent.create(assistant_message))
    try:
        # Mark session as busy
        state.session_status[session_id] = SessionStatus(type="busy")
        await state.broadcast_event(
            SessionStatusEvent.create(session_id, SessionStatus(type="busy"))
        )
        # Add step-start part
        part_id = identifier.ascending("part")
        step_start = StepStartPart(id=part_id, message_id=assistant_msg_id, session_id=session_id)
        assistant_msg_with_parts.parts.append(step_start)
        await state.broadcast_event(PartUpdatedEvent.create(step_start))

        # Get prompt content and execute through the agent
        try:
            prompt_parts = await prompt.get_components(arguments)
            # Extract text content from parts
            prompt_texts = []
            for part in prompt_parts:
                if hasattr(part, "content"):
                    content = part.content
                    if isinstance(content, str):
                        prompt_texts.append(content)
                    elif isinstance(content, list):
                        # Handle Sequence[UserContent]
                        for item in content:
                            if isinstance(item, FileUrl):
                                prompt_texts.append(item.url)
                            elif isinstance(item, str):
                                prompt_texts.append(item)
            prompt_text = "\n".join(prompt_texts)
            # Run the expanded prompt through the agent
            result = await state.agent.run(prompt_text)
            output_text = str(result.data)

        except Exception as e:  # noqa: BLE001
            output_text = f"Error executing command: {e}"

        response_time = now_ms()
        # Create text part with output
        text_part = TextPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg_id,
            session_id=session_id,
            text=output_text,
        )
        assistant_msg_with_parts.parts.append(text_part)
        await state.broadcast_event(PartUpdatedEvent.create(text_part))
        step_finish = StepFinishPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg_id,
            session_id=session_id,
        )
        assistant_msg_with_parts.parts.append(step_finish)
        await state.broadcast_event(PartUpdatedEvent.create(step_finish))
        # Update message with completion time
        time_ = MessageTime(created=now, completed=response_time)
        updated_assistant = assistant_message.model_copy(update={"time": time_})
        assistant_msg_with_parts.info = updated_assistant
        await state.broadcast_event(MessageUpdatedEvent.create(updated_assistant))
    finally:
        await state.mark_session_idle(session_id)

    # Broadcast command.executed event
    await state.broadcast_event(
        CommandExecutedEvent.create(
            name=request.command,
            session_id=session_id,
            arguments=request.arguments or "",
            message_id=assistant_msg_id,
        )
    )

    return assistant_msg_with_parts
