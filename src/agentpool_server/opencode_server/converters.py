"""Converters between pydantic-ai/AgentPool and OpenCode message formats."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, assert_never, cast

import anyenv
from pydantic_ai import (
    ModelRequest,
    ModelResponse,
    RequestUsage,
    RetryPromptPart,
    TextPart as PydanticTextPart,
    ToolCallPart as PydanticToolCallPart,
    ToolReturnPart as PydanticToolReturnPart,
    UserPromptPart,
)

from agentpool import log
from agentpool.messaging.messages import ChatMessage
from agentpool.sessions.models import SessionData
from agentpool.tools.exceptions import ToolError
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict, to_user_content_or_path_ref
from agentpool.utils.time_utils import datetime_to_ms, ms_to_datetime
from agentpool_server.opencode_server.models import (
    AgentPartInput,
    FilePart,
    FilePartInput,
    MCPStatus,
    MessagePath,
    MessageTime,
    MessageWithParts,
    Session,
    SessionRevert,
    SessionShare,
    SubtaskPartInput,
    TextPart,
    TextPartInput,
    TimeCreated,
    TimeCreatedUpdated,
    TimeStart,
    TimeStartEnd,
    TimeStartEndCompacted,
    TimeStartEndOptional,
    Tokens,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
    ToolStatePending,
    ToolStateRunning,
    UserMessage,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from fsspec.asyn import AsyncFileSystem
    from pydantic_ai import UserContent

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.common_types import MCPConnectionStatus, MCPServerStatus, PathReference
    from agentpool_server.opencode_server.models import ToolState
    from agentpool_server.opencode_server.models.mcp import (
        MCPConnectionStatus as OpenCodeMCPConnectionStatus,
    )
    from agentpool_server.opencode_server.models.message import PartInput
    from agentpool_server.opencode_server.models.parts import ResourceSource


logger = log.get_logger(__name__)

# Parameter name mapping from snake_case to camelCase for OpenCode TUI compatibility
_PARAM_NAME_MAP: dict[str, str] = {
    "path": "filePath",
    "file_path": "filePath",
    "old_string": "oldString",
    "new_string": "newString",
    "replace_all": "replaceAll",
    "line_hint": "lineHint",
}


def to_mcp_status(status: MCPServerStatus) -> MCPStatus:
    return MCPStatus(
        name=status.name,
        display_name=status.display_name or status.name,
        status=to_opencode_mcp_status(status.status),
        error=status.error,
    )


def to_opencode_mcp_status(status: MCPConnectionStatus) -> OpenCodeMCPConnectionStatus:
    mapping: dict[MCPConnectionStatus, OpenCodeMCPConnectionStatus] = {
        "connected": "connected",
        "disconnected": "disconnected",
        "error": "error",
        "pending": "disconnected",
        "failed": "error",
        "needs-auth": "disconnected",
        "disabled": "disconnected",
    }
    return mapping[status]


def _convert_params_for_ui(params: dict[str, Any]) -> dict[str, Any]:
    """Convert parameter names from snake_case to camelCase for OpenCode TUI.

    OpenCode TUI expects camelCase parameter names like 'filePath', 'oldString', etc.
    This converts our snake_case parameters to match those expectations.
    """
    return {_PARAM_NAME_MAP.get(k, k): v for k, v in params.items()}


def _get_input_from_state(state: ToolState, *, convert_params: bool = False) -> dict[str, Any]:
    """Extract input from any tool state type.

    Args:
        state: Tool state to extract input from
        convert_params: If True, convert param names to camelCase for UI display
    """
    return _convert_params_for_ui(state.input) if convert_params else state.input


async def _resolve_mcp_resource(source: ResourceSource, agent: BaseAgent[Any, Any]) -> str | None:
    """Resolve an MCP resource and return its content as text (or None if cant be read)."""
    try:
        resource = await agent.get_resource(source.uri)
    except ToolError:
        logger.warning("MCP resource not found", client_name=source.client_name, uri=source.uri)
        return None
    try:
        contents = await resource.read()
        return "\n".join(contents) if contents else None
    except Exception:
        logger.exception(
            "Failed to read MCP resource", client_name=source.client_name, uri=source.uri
        )
        return None


async def extract_user_prompt_from_parts(
    parts: list[PartInput],
    fs: AsyncFileSystem | None = None,
    agent: BaseAgent[Any, Any] | None = None,
) -> Sequence[UserContent | PathReference]:
    """Extract user prompt from OpenCode message input parts.

    Converts OpenCode input parts to pydantic-ai UserContent or PathReference format:
    - Text parts become strings
    - File parts with file:// URLs become PathReference (deferred resolution)
    - File parts with ResourceSource are resolved via MCP
    - Other file parts become ImageUrl, DocumentUrl, AudioUrl, VideoUrl, or BinaryContent
    - Agent parts inject instructions to delegate to sub-agents
    - Subtask parts inject instructions for spawning subtasks

    Args:
        parts: List of OpenCode message input parts
        fs: Optional async filesystem for PathReference resolution
        agent: Optional agent for resolving MCP resources

    Returns:
        Either a simple string (text-only) or a list of UserContent/PathReference items
    """
    from agentpool_server.opencode_server.models.parts import ResourceSource

    result: list[UserContent | PathReference] = []
    for part in parts:
        match part:
            case TextPartInput(text=text):
                result.append(text)
            case FilePartInput(source=ResourceSource() as resource) if agent is not None:
                content = await _resolve_mcp_resource(resource, agent)
                if content is not None:
                    result.append(content)
            case FilePartInput(mime=mime, url=url, filename=filename):
                file_content = to_user_content_or_path_ref(mime, url, filename, fs=fs)
                result.append(file_content)
            case AgentPartInput(name=agent_name):
                # Agent mention (@agent-name in prompt) - inject instruction to execute task
                # This mirrors OpenCode's server-side behavior: inject a synthetic
                # text instruction telling the LLM to call the task tool
                instruction = (
                    f"Use the above message and context to generate a prompt "
                    f"and call the task tool with agent_or_team='{agent_name}'"
                )
                result.append(instruction)
            case SubtaskPartInput(agent=subtask_agent, prompt=subtask_prompt, description=desc):
                # Subtask - explicit task execution with pre-defined prompt
                # Inject instruction to call task with the provided parameters
                instruction = (
                    f"Call the task tool with:\n"
                    f"  agent_or_team: '{subtask_agent}'\n"
                    f"  prompt: '{subtask_prompt}'\n"
                    f"  description: '{desc}'"
                )
                result.append(instruction)
            case _ as unreachable:
                assert_never(unreachable)

    return result


# =============================================================================
# ChatMessage <-> OpenCode MessageWithParts Converters
# =============================================================================


def chat_message_to_opencode(  # noqa: PLR0915
    msg: ChatMessage[Any],
    session_id: str,
    working_dir: str = "",
    agent_name: str = "default",
    model_id: str = "unknown",
    provider_id: str = "agentpool",
) -> MessageWithParts:
    """Convert a ChatMessage to OpenCode MessageWithParts.

    Args:
        msg: The ChatMessage to convert
        session_id: OpenCode session ID
        working_dir: Working directory for path context
        agent_name: Name of the agent
        model_id: Model identifier
        provider_id: Provider identifier

    Returns:
        OpenCode MessageWithParts with appropriate info and parts
    """
    message_id = msg.message_id
    created_ms = datetime_to_ms(msg.timestamp)
    if msg.role == "user":
        result = MessageWithParts.user(
            message_id=message_id,
            session_id=session_id,
            time=TimeCreated(created=created_ms),
            agent_name=msg.name or agent_name,
        )
        if msg.content and isinstance(msg.content, str):
            ts_opt = TimeStartEndOptional(start=created_ms)
            result.add_text_part(msg.content, time=ts_opt)
        else:
            for model_msg in msg.messages:
                if not isinstance(model_msg, ModelRequest):
                    continue
                for part in model_msg.parts:
                    if not isinstance(part, UserPromptPart):
                        continue
                    content = part.content
                    if isinstance(content, str):
                        text = content
                    else:
                        from agentpool.agents.native_agent.helpers import _summarize_content_block

                        text = " ".join(_summarize_content_block(c) for c in content)
                    if text:
                        ts_opt = TimeStartEndOptional(start=created_ms)
                        result.add_text_part(text, time=ts_opt)
    else:
        # Assistant message
        completed_ms = created_ms
        if msg.response_time:
            completed_ms = created_ms + int(msg.response_time * 1000)

        tokens = Tokens.from_pydantic_ai(msg.usage)
        result = MessageWithParts.assistant(
            message_id=message_id,
            session_id=session_id,
            parent_id="",  # Would need to track parent user message
            model_id=msg.model_name or model_id,
            provider_id=msg.provider_name or provider_id,
            agent_name=msg.name or agent_name,
            path=MessagePath(cwd=working_dir, root=working_dir),
            time=MessageTime(created=created_ms, completed=completed_ms),
            tokens=tokens,
            cost=float(msg.cost_info.total_cost) if msg.cost_info else 0.0,
            finish=msg.finish_reason,
        )

        result.add_step_start_part()
        # Process all model messages to extract parts
        tool_calls: dict[str, ToolPart] = {}
        for model_msg in msg.messages:
            # Handle case where message might be a dict (loaded from storage)
            if isinstance(model_msg, dict):
                # Try to extract text content from dict representation
                model_dict = cast(dict[str, Any], model_msg)
                parts = model_dict.get("parts") or []
                for part_dict in parts:
                    if isinstance(part_dict, dict) and part_dict.get("part_kind") == "text":
                        content = part_dict.get("content") or ""
                        if content:
                            ts_opt = TimeStartEndOptional(start=created_ms, end=completed_ms)
                            result.add_text_part(content, time=ts_opt)
                continue
            for p in model_msg.parts:
                match p:
                    case PydanticTextPart(content=content):
                        ts_opt = TimeStartEndOptional(start=created_ms, end=completed_ms)
                        result.add_text_part(content, time=ts_opt)
                    case PydanticToolCallPart(tool_name=tool_name, tool_call_id=call_id):
                        tool_input = _convert_params_for_ui(safe_args_as_dict(p))
                        ts = TimeStart(start=created_ms)
                        title = f"Running {tool_name}"
                        running_state = ToolStateRunning(time=ts, input=tool_input, title=title)
                        tool_part = result.add_tool_part(tool_name, call_id, state=running_state)
                        tool_calls[call_id] = tool_part
                    case RetryPromptPart(content=retry_content, tool_name=tool_name, timestamp=ts):
                        retry_count = sum(
                            1
                            for m in msg.messages
                            if isinstance(m, ModelRequest)
                            for p in m.parts
                            if isinstance(p, RetryPromptPart)
                        )
                        error_message = p.model_response()
                        is_retryable = True
                        if isinstance(retry_content, list):
                            error_type = "validation_error"
                        elif tool_name:
                            error_type = "tool_error"
                        else:
                            error_type = "retry"

                        result.add_retry_part(
                            attempt=retry_count,
                            message=error_message,
                            created=int(ts.timestamp() * 1000),
                            is_retryable=is_retryable,
                            metadata={"error_type": error_type} if error_type else None,
                        )
                    case PydanticToolReturnPart(
                        tool_call_id=call_id,
                        content=tool_content,
                        tool_name=tool_name,
                        timestamp=tool_ts,
                    ):
                        end_ms = datetime_to_ms(tool_ts)
                        if isinstance(tool_content, str):
                            output = tool_content
                        elif isinstance(tool_content, dict):
                            output = anyenv.dump_json(tool_content, indent=True)
                        else:
                            output = str(tool_content) if tool_content is not None else ""
                        if existing := tool_calls.get(call_id):
                            existing_input = _get_input_from_state(existing.state)
                            if isinstance(tool_content, dict) and "error" in tool_content:
                                existing.state = ToolStateError(
                                    error=str(tool_content.get("error", "Unknown error")),
                                    input=existing_input,
                                    time=TimeStartEnd(start=created_ms, end=end_ms),
                                )
                            else:
                                title = f"Completed {tool_name}"
                                tsc = TimeStartEndCompacted(start=created_ms, end=end_ms)
                                # Extract metadata from tool result if present
                                # (e.g., subagent sessionId)
                                metadata = (
                                    tool_content.get("metadata", {})
                                    if isinstance(tool_content, dict)
                                    else {}
                                )
                                existing.state = ToolStateCompleted(
                                    title=title,
                                    input=existing_input,
                                    output=output,
                                    time=tsc,
                                    metadata=metadata,
                                )
                        else:
                            # Orphan return - create completed tool part
                            state: ToolStateCompleted | ToolStateError
                            if isinstance(tool_content, dict) and "error" in tool_content:
                                err = str(tool_content.get("error", "Unknown error"))
                                ts_end = TimeStartEnd(start=created_ms, end=end_ms)
                                state = ToolStateError(error=err, time=ts_end)
                            else:
                                title = f"Completed {tool_name}"
                                tsc = TimeStartEndCompacted(start=created_ms, end=end_ms)
                                # Extract metadata for orphan returns too
                                metadata = (
                                    tool_content.get("metadata", {})
                                    if isinstance(tool_content, dict)
                                    else {}
                                )
                                state = ToolStateCompleted(
                                    title=title, output=output, time=tsc, metadata=metadata
                                )
                            result.add_tool_part(tool_name, call_id, state=state)
        cost = float(msg.cost_info.total_cost) if msg.cost_info else 0.0
        result.add_step_finish_part(reason=msg.finish_reason or "stop", cost=cost, tokens=tokens)

    return result


def opencode_to_chat_message(  # noqa: PLR0915
    msg: MessageWithParts,
    session_id: str | None = None,
) -> ChatMessage[str]:
    """Convert OpenCode MessageWithParts to ChatMessage.

    Args:
        msg: OpenCode message with parts
        session_id: Optional conversation ID override

    Returns:
        ChatMessage with pydantic-ai model messages
    """
    info = msg.info
    message_id = info.id
    session_id = info.session_id
    # Determine role and extract timing
    if isinstance(info, UserMessage):
        role = "user"
        created_ms = info.time.created
        model_name = info.model.model_id if info.model else None
        provider_name = info.model.provider_id if info.model else None
        usage = RequestUsage()
        finish_reason = None
    else:
        role = "assistant"
        created_ms = info.time.created
        model_name = info.model_id
        provider_name = info.provider_id
        usage = RequestUsage(
            input_tokens=info.tokens.input,
            output_tokens=info.tokens.output,
            cache_read_tokens=info.tokens.cache.read,
            cache_write_tokens=info.tokens.cache.write,
        )
        finish_reason = info.finish

    timestamp = ms_to_datetime(created_ms)
    # Build model messages from parts
    model_messages: list[ModelRequest | ModelResponse] = []
    if role == "user":
        # Collect all parts (text and files/images) into multimodal content list
        from pydantic_ai import BinaryContent, ImageUrl

        content_items: list[str | BinaryContent | ImageUrl] = []
        for part in msg.parts:
            if isinstance(part, TextPart):
                content_items.append(part.text)
            elif isinstance(part, FilePart):
                # Convert file part to appropriate content type
                if part.mime.startswith("image/") and part.url.startswith("data:"):
                    # Data URI image - extract base64 and create BinaryContent
                    # This is the most compatible format for multimodal models
                    content_items.append(BinaryContent.from_data_uri(part.url))
                elif part.mime.startswith("image/"):
                    # Regular image URL (http/https)
                    content_items.append(ImageUrl(url=part.url, media_type=part.mime))
                else:
                    # Other file types - treat as text reference for now
                    content_items.append(f"[File: {part.filename or 'attachment'}]")

        # Create single user prompt with all content items as a list
        # This is the correct format for multimodal prompts in pydantic-ai
        if content_items:
            model_messages.append(ModelRequest(parts=[UserPromptPart(content=content_items)]))
        else:
            model_messages.append(ModelRequest(parts=[UserPromptPart(content="")]))
    else:
        # Assistant message - collect response parts and tool interactions
        response_parts: list[Any] = []
        tool_returns: list[PydanticToolReturnPart] = []
        for part in msg.parts:
            match part:
                case TextPart(text=text, id=part_id):
                    response_parts.append(PydanticTextPart(content=text, id=part_id))
                case ToolPart(tool=tool_name, call_id=call_id, state=state):
                    response_parts.append(
                        PydanticToolCallPart(
                            tool_name=tool_name,
                            tool_call_id=call_id,
                            args=_get_input_from_state(state),
                        )
                    )
                    match state:
                        case ToolStateCompleted(output=output):
                            tool_returns.append(
                                PydanticToolReturnPart(
                                    tool_name=tool_name,
                                    tool_call_id=call_id,
                                    content=output,
                                )
                            )
                        case ToolStateError(error=error):
                            tool_returns.append(
                                PydanticToolReturnPart(
                                    tool_name=tool_name,
                                    tool_call_id=call_id,
                                    content={"error": error},
                                )
                            )
                        case ToolStatePending() | ToolStateRunning():
                            tool_returns.append(
                                PydanticToolReturnPart(
                                    tool_name=tool_name,
                                    tool_call_id=call_id,
                                    content="Tool call was aborted before completion",
                                )
                            )

        if response_parts:
            model_messages.append(
                ModelResponse(
                    parts=response_parts,
                    usage=usage,
                    model_name=model_name,
                    timestamp=timestamp,
                )
            )

        # Add tool returns as a follow-up request if any
        if tool_returns:
            model_messages.append(ModelRequest(parts=tool_returns, instructions=None))
    # Extract content for the ChatMessage
    content = next((p.text for p in msg.parts if isinstance(p, TextPart)), "")
    return ChatMessage(
        content=content,
        role=role,  # type: ignore[arg-type]
        message_id=message_id,
        session_id=session_id or session_id,
        timestamp=timestamp,
        messages=model_messages,
        usage=usage,
        model_name=model_name,
        provider_name=provider_name,
        finish_reason=finish_reason,  # type: ignore[arg-type]
    )


# =============================================================================
# Session Converters
# =============================================================================


def session_data_to_opencode(data: SessionData) -> Session:
    """Convert SessionData to OpenCode Session model.

    Args:
        data: SessionData to convert (title comes from data.title property)
    """
    # Convert datetime to milliseconds timestamp
    created_ms = datetime_to_ms(data.created_at)
    updated_ms = datetime_to_ms(data.last_active)
    # Extract revert/share from metadata if present
    revert = None
    share = None
    if "revert" in data.metadata:
        revert = SessionRevert(**data.metadata["revert"])
    if "share" in data.metadata:
        share = SessionShare(**data.metadata["share"])

    # Recompute project_id from cwd when stored value is missing or "default".
    # Older sessions were persisted with project_id="default" which doesn't
    # match the projectID computed from the cwd.
    project_id = data.project_id
    if not project_id or project_id == "default":
        if data.cwd:
            from agentpool_storage.opencode_provider import helpers

            project_id = helpers.compute_project_id(data.cwd)
        else:
            project_id = "default"

    return Session(
        id=data.session_id,
        project_id=project_id,
        directory=data.cwd or "",
        title=data.title or "New Session",
        version=data.version,
        time=TimeCreatedUpdated(created=created_ms, updated=updated_ms),
        parent_id=data.parent_id,
        revert=revert,
        share=share,
    )


def opencode_to_session_data(
    session: Session,
    *,
    agent_name: str = "default",
    pool_id: str | None = None,
) -> SessionData:
    """Convert OpenCode Session to SessionData for persistence."""
    # Store revert/share in metadata
    metadata: dict[str, Any] = {}
    if session.title:
        metadata["title"] = session.title
    if session.revert:
        metadata["revert"] = session.revert.model_dump()
    if session.share:
        metadata["share"] = session.share.model_dump()
    return SessionData(
        session_id=session.id,
        agent_name=agent_name,
        pool_id=pool_id,
        project_id=session.project_id,
        parent_id=session.parent_id,
        version=session.version,
        cwd=session.directory,
        created_at=ms_to_datetime(session.time.created),
        last_active=ms_to_datetime(session.time.updated),
        metadata=metadata,
    )
