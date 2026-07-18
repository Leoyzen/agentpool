"""Message format conversion and OpenCode message handling mixin.

Extracted from session_pool_integration.py as part of the session-debt-cleanup
file split. Contains message conversion utilities and the message bridge mixin
that provides tool-part creation/update methods for subagent sessions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.converters import (
    chat_message_to_opencode,
    opencode_to_chat_message,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageWithParts,
    PartUpdatedEvent,
    TimeCreatedUpdated,
)
from agentpool_server.opencode_server.models.parts import (
    TimeStart,
    TimeStartEnd,
    TimeStartEndCompacted,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
    ToolStateRunning,
)
from agentpool_server.opencode_server.models.session import Session


if TYPE_CHECKING:
    from agentpool.agents.events.events import RunErrorEvent, SpawnSessionStart, StreamCompleteEvent
    from agentpool_server.opencode_server.state import ServerState


logger = get_logger(__name__)


async def get_messages_for_session(
    state: ServerState,
    session_id: str,
) -> list[MessageWithParts]:
    """Get messages for a session from SessionPool or fall back to ServerState.

    For subagent/child sessions (identified by ``parent_id``), the in-memory
    ``state.messages`` cache is consulted first because streaming parts are
    updated in-place on those objects and may be more recent than the
    SessionPool snapshot.

    Args:
        state: The OpenCode server state.
        session_id: The session ID to get messages for.

    Returns:
        List of MessageWithParts for the session.
    """
    messages: list[MessageWithParts] = getattr(state, "messages", {}).get(session_id, []) or []

    # Fast-path: subagent sessions are streamed live into memory, so the
    # in-memory copy is always the most up-to-date.
    cached_session = state.sessions.get(session_id)
    is_subagent = cached_session is not None and cached_session.parent_id is not None
    if is_subagent and messages:
        return messages

    session_pool = getattr(state.pool, "session_pool", None)
    if session_pool is not None:
        try:
            sp_messages = await session_pool.get_messages(session_id)
        except (KeyError, TypeError):
            sp_messages = []
        if sp_messages:
            agent = state.agent
            # Use safe lookup to avoid recreating a phantom session during
            # message retrieval if the session was already closed.
            existing_agent = session_pool.sessions.get_session_agent(session_id)
            if existing_agent is not None:
                agent = existing_agent
            return [
                chat_message_to_opencode(
                    chat_msg,
                    session_id=session_id,
                    working_dir=state.working_dir,
                    agent_name=agent.name,
                    model_id=getattr(chat_msg, "model_name", None) or "sonnet",
                    provider_id=getattr(chat_msg, "provider_name", None) or "claude-code",
                )
                for chat_msg in sp_messages
            ]
    return messages


async def append_message_to_session(
    state: ServerState,
    session_id: str,
    msg: MessageWithParts,
) -> None:
    """Append a message to a session's history.

    Writes to SessionPool when the feature flag is enabled.
    Also writes to the in-memory messages dict when present for
    backward compatibility with tests and legacy code paths.

    Args:
        state: The OpenCode server state.
        session_id: The session ID to append to.
        msg: The OpenCode message to append.
    """
    session_pool = None
    if hasattr(state, "pool") and state.pool is not None:
        session_pool = getattr(state.pool, "session_pool", None)
    if session_pool is not None:
        chat_msg = opencode_to_chat_message(msg, session_id=session_id)
        try:
            await session_pool.append_message(session_id, chat_msg)
        except (KeyError, TypeError):
            logger.warning(
                "Failed to append message to SessionPool",
                session_id=session_id,
                exc_info=True,
            )

    # Always mirror to the in-memory dict when present for backward compatibility
    messages = getattr(state, "messages", None)
    if messages is not None:
        messages.setdefault(session_id, [])
        messages[session_id].append(msg)


async def set_messages_for_session(
    state: ServerState,
    session_id: str,
    messages: list[MessageWithParts],
) -> None:
    """Replace all in-memory messages for a session.

    This is a bulk operation used after compaction/summarization when
    the UI-visible message list should be reset to a specific set.
    SessionPool storage is managed separately via storage.replace_conversation_messages.

    Args:
        state: The OpenCode server state.
        session_id: The session ID to update.
        messages: The new message list.
    """
    in_memory_messages = getattr(state, "messages", None)
    if in_memory_messages is not None:
        in_memory_messages[session_id] = list(messages)


def _session_state_to_opencode(state: Any) -> Session:
    """Convert SessionPool SessionState to OpenCode Session model.

    Args:
        state: SessionState from SessionPool.

    Returns:
        OpenCode Session model.
    """
    import time

    from agentpool_storage.opencode_provider import helpers

    now_mono = time.monotonic()
    now_epoch = time.time()
    created_ms = int((now_epoch - (now_mono - state.created_at)) * 1000)
    updated_ms = int((now_epoch - (now_mono - state.last_active_at)) * 1000)
    directory = state.metadata.get("cwd", "")
    project_id = state.metadata.get("project_id", "")
    if not project_id and directory:
        project_id = helpers.compute_project_id(directory)
    if not project_id:
        project_id = "default"

    return Session(
        id=state.session_id,
        project_id=project_id,
        directory=directory,
        title=state.metadata.get("title", "New Session"),
        version="1",
        time=TimeCreatedUpdated(created=created_ms, updated=updated_ms),
        parent_id=state.parent_session_id,
    )


def _reconstruct_tool_parts_from_checkpoint(
    state: ServerState,
    session_id: str,
    pending_calls: list[Any],
) -> None:
    """Reconstruct running ToolParts from pending deferred calls.

    Creates an assistant message (if one does not exist) and appends
    a ``ToolPart`` with ``ToolStateRunning`` for each pending deferred
    call. This restores the visual tool state in the OpenCode TUI so
    the user sees what tools were in-flight at checkpoint time.

    Args:
        state: The OpenCode server state.
        session_id: The session to reconstruct ToolParts for.
        pending_calls: Unresolved deferred tool calls from the checkpoint.
    """
    if not pending_calls:
        return

    from agentpool.utils import identifiers as identifier
    from agentpool.utils.time_utils import now_ms
    from agentpool_server.opencode_server.models.parts import (
        TimeStart,
        ToolPart,
        ToolStateRunning,
    )

    # Create an assistant message to hold the ToolParts
    assistant_msg_id = identifier.ascending("message")

    # Agent/model propagation: look up the real agent_name from the
    # session state instead of hardcoding "agentpool". Falls back to
    # "agentpool" when the session state is unavailable.
    agent_name = "agentpool"
    try:
        pool = state.pool
        session_pool = pool.session_pool
    except RuntimeError:
        session_pool = None
    if session_pool is not None:
        session_state = session_pool.sessions.get_session(session_id)
        if session_state is not None:
            agent_name = session_state.agent_name

    assistant_msg = MessageWithParts.assistant(
        message_id=assistant_msg_id,
        session_id=session_id,
        time=MessageTime(created=now_ms()),
        agent_name=agent_name,
        model_id="default",
        parent_id=session_id,
        provider_id="agentpool",
        path=MessagePath(cwd=state.working_dir, root=state.working_dir),
    )

    for call in pending_calls:
        ts = TimeStart(start=now_ms())
        running_state = ToolStateRunning(
            time=ts,
            input={
                "description": call.tool_name,
                "tool_call_id": call.tool_call_id,
            },
            metadata={"deferred": True, "deferred_strategy": call.deferred_strategy},
            title=call.tool_name,
        )
        tool_part = ToolPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg_id,
            session_id=session_id,
            tool=call.tool_name,
            call_id=call.tool_call_id,
            state=running_state,
        )
        assistant_msg.parts.append(tool_part)

    # Register in the in-memory message list
    messages = getattr(state, "messages", None)
    if messages is not None:
        messages.setdefault(session_id, [])
        messages[session_id].append(assistant_msg)


class OpenCodeMessageBridgeMixin:
    """Mixin providing message format conversion and tool-part management.

    Provides methods for creating and updating ToolParts that represent
    subagent sessions in the parent session's message history.

    Attributes:
        server_state: The OpenCode server state (provided by main class).
    """

    server_state: ServerState

    async def _create_subagent_tool_part(
        self,
        parent_session_id: str,
        spawn_event: SpawnSessionStart,
    ) -> ToolPart | None:
        """Create a ToolPart in the parent session representing a subagent.

        This replaces the ToolPart creation that previously happened inside
        EventProcessor._process_subagent_event when events were wrapped in
        SubAgentEvent.

        Args:
            parent_session_id: The parent session ID.
            spawn_event: The spawn event containing subagent metadata.

        Returns:
            The created ToolPart, or None if one already exists for this child.
        """
        from agentpool.utils import identifiers as identifier

        # Find the parent session's latest assistant message from in-memory state
        # (not via get_messages_for_session, which may return copies when
        # SessionPool message storage is enabled).
        messages = getattr(self.server_state, "messages", {}).get(parent_session_id, []) or []
        assistant_msg = None
        for msg in reversed(messages):
            if msg.info.role == "assistant":
                assistant_msg = msg
                break

        if assistant_msg is None:
            logger.warning(
                "No assistant message found for parent session %s, skipping ToolPart creation",
                parent_session_id,
            )
            return None

        # Check if ToolPart already exists for this child session
        child_session_id = spawn_event.child_session_id
        for part in assistant_msg.parts:
            if (
                isinstance(part, ToolPart)
                and part.metadata is not None
                and part.metadata.get("sessionId") == child_session_id
            ):
                logger.debug("ToolPart already exists for child session %s", child_session_id)
                return None

        source_name = spawn_event.source_name or "subagent"
        tool_title = source_name
        ts = TimeStart(start=now_ms())
        running_state = ToolStateRunning(
            time=ts,
            input={
                "description": tool_title,
                "subagent_type": tool_title,
                "prompt": spawn_event.metadata.get("prompt", ""),
            },
            metadata={"sessionId": child_session_id, "title": tool_title},
            title=tool_title,
        )
        tool_part = ToolPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg.info.id,
            session_id=parent_session_id,
            tool="task",
            call_id=identifier.ascending("part"),
            state=running_state,
        )
        assistant_msg.parts.append(tool_part)
        await self.server_state.broadcast_event(PartUpdatedEvent.create(tool_part))
        logger.debug(
            "Created ToolPart for child session %s in parent %s",
            child_session_id,
            parent_session_id,
        )
        return tool_part

    async def _update_parent_toolpart(
        self,
        parent_session_id: str,
        child_session_id: str,
        spawn_event: SpawnSessionStart,
        event: StreamCompleteEvent[Any],
    ) -> None:
        """Update parent ToolPart to Completed when child subagent finishes.

        Args:
            parent_session_id: The parent session ID.
            child_session_id: The child session ID.
            spawn_event: The spawn event containing subagent metadata.
            event: The StreamCompleteEvent from the child.
        """
        # Find the parent session's latest assistant message from in-memory state
        messages = getattr(self.server_state, "messages", {}).get(parent_session_id, []) or []
        assistant_msg = None
        for msg in reversed(messages):
            if msg.info.role == "assistant":
                assistant_msg = msg
                break

        if assistant_msg is None:
            return

        # Find the ToolPart for this child session
        tool_part = None
        for part in assistant_msg.parts:
            if (
                isinstance(part, ToolPart)
                and hasattr(part.state, "metadata")
                and isinstance(part.state.metadata, dict)
                and part.state.metadata.get("sessionId") == child_session_id
            ):
                tool_part = part
                break

        if tool_part is None:
            logger.warning(
                "No ToolPart found for child session %s in parent %s",
                child_session_id,
                parent_session_id,
            )
            return

        source_name = spawn_event.source_name or "subagent"
        tool_title = source_name
        complete_msg = event.message
        content = str(complete_msg.content) if complete_msg.content else "(no output)"

        start_time = (
            tool_part.state.time.start
            if isinstance(tool_part.state, ToolStateRunning)
            else now_ms()
        )
        completed_state = ToolStateCompleted(
            input={
                "description": tool_title,
                "subagent_type": tool_title,
                "prompt": spawn_event.metadata.get("prompt", ""),
            },
            output=content,
            title=tool_title,
            metadata={"sessionId": child_session_id, "title": tool_title},
            time=TimeStartEndCompacted(start=start_time, end=now_ms()),
        )
        updated = ToolPart(
            id=tool_part.id,
            message_id=tool_part.message_id,
            session_id=tool_part.session_id,
            tool=tool_part.tool,
            call_id=tool_part.call_id,
            state=completed_state,
        )

        # Replace the old part in the message
        for i, part in enumerate(assistant_msg.parts):
            if part.id == tool_part.id:
                assistant_msg.parts[i] = updated
                break

        await self.server_state.broadcast_event(PartUpdatedEvent.create(updated))
        logger.debug(
            "Updated ToolPart to Completed for child session %s in parent %s",
            child_session_id,
            parent_session_id,
        )

    async def _update_parent_toolpart_error(
        self,
        parent_session_id: str,
        child_session_id: str,
        spawn_event: SpawnSessionStart,
        event: RunErrorEvent,
    ) -> None:
        """Update parent ToolPart to Error when child subagent fails.

        Args:
            parent_session_id: The parent session ID.
            child_session_id: The child session ID.
            spawn_event: The spawn event containing subagent metadata.
            event: The RunErrorEvent from the child.
        """
        # Find the parent session's latest assistant message from in-memory state
        messages = getattr(self.server_state, "messages", {}).get(parent_session_id, []) or []
        assistant_msg = None
        for msg in reversed(messages):
            if msg.info.role == "assistant":
                assistant_msg = msg
                break

        if assistant_msg is None:
            return

        # Find the ToolPart for this child session
        tool_part = None
        for part in assistant_msg.parts:
            if (
                isinstance(part, ToolPart)
                and hasattr(part.state, "metadata")
                and isinstance(part.state.metadata, dict)
                and part.state.metadata.get("sessionId") == child_session_id
            ):
                tool_part = part
                break

        if tool_part is None:
            return

        source_name = spawn_event.source_name or "subagent"
        tool_title = source_name
        error_msg = event.message or "Unknown error"

        start_time = (
            tool_part.state.time.start
            if isinstance(tool_part.state, ToolStateRunning)
            else now_ms()
        )
        error_state = ToolStateError(
            error=error_msg,
            input={
                "description": tool_title,
                "subagent_type": tool_title,
                "prompt": spawn_event.metadata.get("prompt", ""),
            },
            metadata={"sessionId": child_session_id, "title": tool_title},
            time=TimeStartEnd(start=start_time, end=now_ms()),
        )
        updated = ToolPart(
            id=tool_part.id,
            message_id=tool_part.message_id,
            session_id=tool_part.session_id,
            tool=tool_part.tool,
            call_id=tool_part.call_id,
            state=error_state,
        )

        # Replace the old part in the message
        for i, part in enumerate(assistant_msg.parts):
            if part.id == tool_part.id:
                assistant_msg.parts[i] = updated
                break

        await self.server_state.broadcast_event(PartUpdatedEvent.create(updated))
        logger.debug(
            "Updated ToolPart to Error for child session %s in parent %s",
            child_session_id,
            parent_session_id,
        )
