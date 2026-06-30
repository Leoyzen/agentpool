"""ACP notification helper for clean session update API."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, assert_never

from pydantic_ai import ModelRequest, ModelResponse, ToolReturnPart, UserPromptPart
import structlog

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AudioContentBlock,
    AvailableCommand,
    AvailableCommandsUpdate,
    BlobResourceContents,
    ConfigOptionUpdate,
    ContentToolCallContent,
    CurrentModeUpdate,
    EmbeddedResourceContentBlock,
    FileEditToolCallContent,
    ImageContentBlock,
    ResourceContentBlock,
    SessionNotification,
    # CurrentModelUpdate,
    TerminalToolCallContent,
    TextContentBlock,
    TextResourceContents,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)
from acp.schema.tool_call import ToolCallLocation
from acp.tool_call_reporter import ToolCallReporter
from acp.utils import generate_tool_title, infer_tool_kind, to_acp_content_blocks
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict


if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from acp import (
        AvailableCommand,
        Client,
        PlanEntry,
        ToolCallContent,
        ToolCallKind,
        ToolCallStatus,
    )
    from acp.schema import Audience, SessionConfigOption, SessionUpdate

    ContentType = Sequence[ToolCallContent | str]

logger = structlog.get_logger(__name__)


class ACPNotifications:
    """Clean API for creating and sending ACP session notifications.

    Provides convenient methods for common notification patterns,
    handling both creation and sending in a single call.
    """

    def __init__(
        self,
        client: Client,
        session_id: str,
        *,
        notification_batch_size: int = 20,
        notification_flush_interval: float = 0.0,
    ) -> None:
        """Initialize notifications helper.

        Args:
            client: ACP client and session_id
            session_id: Session identifier
            notification_batch_size: Maximum number of SessionUpdate objects per
                batch during replay. Default 20.
            notification_flush_interval: Delay between batches in seconds.
                Default 0.0 (no delay). Use > 0 for slow remote clients.
        """
        if notification_batch_size <= 0:
            raise ValueError(
                f"notification_batch_size must be greater than 0, got {notification_batch_size}"
            )
        if notification_flush_interval < 0:
            raise ValueError(
                f"notification_flush_interval must be non-negative,"
                f" got {notification_flush_interval}"
            )
        self.client = client
        self.id = session_id
        self.log = logger.bind(session_id=session_id)
        self._tool_call_inputs: dict[str, dict[str, Any]] = {}
        self.notification_batch_size = notification_batch_size
        self.notification_flush_interval = notification_flush_interval
        self._batch_supported: bool = False

    async def create_tool_reporter(
        self,
        tool_call_id: str,
        title: str,
        *,
        kind: ToolCallKind | None = None,
        status: ToolCallStatus = "pending",
        locations: Sequence[ToolCallLocation] | None = None,
        content: Sequence[ToolCallContent] | None = None,
        raw_input: Any | None = None,
        auto_start: bool = True,
    ) -> ToolCallReporter:
        """Create a stateful tool call reporter.

        The reporter maintains the current state and sends updates when fields change,
        avoiding the need to repeat unchanged fields on every update.

        Args:
            tool_call_id: Unique identifier for this tool call
            title: Human-readable title describing the tool action
            kind: Category of tool being invoked
            status: Initial execution status
            locations: File locations affected by this tool call
            content: Initial content produced by the tool call
            raw_input: Raw input parameters sent to the tool
            auto_start: Whether to send the initial notification immediately

        Returns:
            A ToolCallReporter instance for sending updates

        Example:
            ```python
            reporter = await notifications.create_tool_reporter(
                tool_call_id="abc123",
                title="Reading file",
                kind="read",
            )
            await reporter.update(status="in_progress", message="Opening...")
            await reporter.update(message="Processing...")
            await reporter.complete(message="Done!")
            ```
        """
        reporter = ToolCallReporter(
            notifications=self,
            tool_call_id=tool_call_id,
            title=title,
            kind=kind,
            status=status,
            locations=locations,
            content=content,
            raw_input=raw_input,
        )
        if auto_start:
            await reporter.start()
        return reporter

    async def tool_call_start(
        self,
        tool_call_id: str,
        title: str,
        *,
        kind: ToolCallKind | None = None,
        locations: Sequence[ToolCallLocation] | None = None,
        content: ContentType | None = None,
        raw_input: dict[str, Any] | None = None,
    ) -> None:
        """Send a tool call start notification.

        Args:
            tool_call_id: Tool call identifier
            title: Optional title for the start notification
            kind: Optional tool call kind
            locations: Optional sequence of file/path locations
            content: Optional sequence of content blocks
            raw_input: Optional raw input data
        """
        start = ToolCallStart(
            tool_call_id=tool_call_id,
            status="pending",
            title=title,
            kind=kind,
            locations=locations,
            content=[
                ContentToolCallContent.text(i) if isinstance(i, str) else i for i in content or []
            ],
            raw_input=raw_input,
        )
        await self.send_update(start)

    async def send_update(self, update: SessionUpdate) -> None:
        notification = SessionNotification(session_id=self.id, update=update)
        await self.client.session_update(notification)  # pyright: ignore[reportArgumentType]

    def set_batch_support(self, supported: bool) -> None:
        """Enable or disable batch session update delivery.

        When enabled, ``replay()`` sends updates via ``_batch_session_updates``
        ext_notification instead of individual ``session/update`` notifications.

        Args:
            supported: Whether the client supports ``_batch_session_updates``.
        """
        self._batch_supported = supported

    async def send_batch_update(self, updates: list[SessionUpdate]) -> None:
        """Send a batch of SessionUpdate objects in a single notification.

        When the client supports ``_batch_session_updates``, sends all updates
        in one ext_notification. Otherwise, falls back to sequential
        ``session/update`` notifications.

        Args:
            updates: List of SessionUpdate objects to send.
        """
        if not updates:
            return
        if self._batch_supported:
            await self.client.ext_notification(
                "_batch_session_updates",
                {
                    "session_id": self.id,
                    "updates": [u.model_dump(by_alias=True, exclude_none=True) for u in updates],
                },
            )
        else:
            for update in updates:
                await self.send_update(update)

    async def send_elicitation_complete(
        self,
        elicitation_id: str,
    ) -> None:
        """Send an elicitation complete notification.

        Informs the client that an out-of-band URL-mode elicitation
        has completed. The client MAY use this to retry failed requests.
        """
        from acp.schema import ElicitationCompleteNotification

        notification = ElicitationCompleteNotification(
            session_id=self.id,
            elicitation_id=elicitation_id,
        )
        await self.client.elicitation_complete(notification)

    async def send_ext_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Send an extension notification.

        Uses the ACP ExtNotification mechanism to send arbitrary one-way
        messages for custom functionality (e.g., toast notifications).

        Args:
            method: Extension method name (should be prefixed with underscore).
            params: Optional parameters for the notification.
        """
        await self.client.ext_notification(method, params or {})

    async def tool_call_progress(
        self,
        tool_call_id: str,
        status: ToolCallStatus,
        *,
        title: str | None = None,
        raw_output: Any | None = None,
        kind: ToolCallKind | None = None,
        locations: Sequence[ToolCallLocation] | None = None,
        content: ContentType | None = None,
    ) -> None:
        """Send a generic progress notification.

        Args:
            tool_call_id: Tool call identifier
            status: Progress status
            title: Optional title for the progress update
            raw_output: Optional raw output text
            kind: Optional kind of tool call
            locations: Optional sequence of file/path locations
            content: Optional sequence of content blocks or strings to display
        """
        progress = ToolCallProgress(
            tool_call_id=tool_call_id,
            status=status,
            title=title,
            raw_output=raw_output,
            kind=kind,
            locations=locations,
            content=[
                ContentToolCallContent.text(i) if isinstance(i, str) else i for i in content or []
            ],
        )
        await self.send_update(progress)

    async def tool_call_update(
        self,
        tool_call_id: str,
        *,
        title: str | None = None,
        status: ToolCallStatus | None = None,
        kind: ToolCallKind | None = None,
        locations: Sequence[ToolCallLocation] | None = None,
        content: ContentType | None = None,
        raw_output: Any | None = None,
    ) -> None:
        """Send a tool call update with only the provided fields.

        Unlike tool_call_progress, all fields are optional. Only fields
        that are explicitly provided (not None) will be included in the
        notification, following the ACP spec which states that only
        changed fields need to be sent.

        Args:
            tool_call_id: Tool call identifier (required)
            title: Update the human-readable title
            status: Update execution status
            kind: Update tool kind
            locations: Update file locations
            content: Update content blocks
            raw_output: Update raw output
        """
        update = ToolCallProgress(
            tool_call_id=tool_call_id,
            status=status,
            title=title,
            raw_output=raw_output,
            kind=kind,
            locations=locations,
            content=[
                ContentToolCallContent.text(i) if isinstance(i, str) else i for i in content or []
            ]
            if content
            else None,
        )
        await self.send_update(update)

    async def file_edit_progress(
        self,
        tool_call_id: str,
        path: str,
        old_text: str,
        new_text: str,
        *,
        status: ToolCallStatus = "completed",
        title: str | None = None,
        changed_lines: Sequence[int] | None = None,
    ) -> None:
        """Send a notification with file edit content.

        Args:
            tool_call_id: Tool call identifier
            path: File path being edited
            old_text: Original file content
            new_text: New file content
            status: Progress status (default: 'completed')
            title: Optional title
            changed_lines: List of line numbers where changes occurred (1-based)
        """
        content = FileEditToolCallContent(path=path, old_text=old_text, new_text=new_text)

        # Create locations for changed lines or fallback to file location
        if changed_lines:
            locations = [ToolCallLocation(path=path, line=i) for i in changed_lines]
        else:
            locations = [ToolCallLocation(path=path)]

        await self.tool_call_progress(
            tool_call_id=tool_call_id,
            status=status,
            title=title,
            locations=locations,
            content=[content],
        )

    async def terminal_progress(
        self,
        tool_call_id: str,
        terminal_id: str,
        *,
        status: ToolCallStatus = "completed",
        title: str | None = None,
        raw_output: str | None = None,
    ) -> None:
        """Send a notification with terminal content.

        Args:
            tool_call_id: Tool call identifier
            terminal_id: Terminal identifier
            status: Progress status (default: 'completed')
            title: Optional title
            raw_output: Optional raw output text
        """
        terminal_content = TerminalToolCallContent(terminal_id=terminal_id)
        await self.tool_call_progress(
            tool_call_id=tool_call_id,
            status=status,
            title=title,
            raw_output=raw_output,
            content=[terminal_content],
        )

    async def update_plan(self, entries: Sequence[PlanEntry]) -> None:
        """Send a plan notification."""
        plan = AgentPlanUpdate(entries=entries)
        await self.send_update(plan)

    async def update_commands(self, commands: list[AvailableCommand]) -> None:
        """Send a command update notification."""
        update = AvailableCommandsUpdate(available_commands=commands)
        await self.send_update(update)

    async def send_agent_text(
        self,
        message: str,
        *,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> None:
        """Send a text message notification."""
        update = AgentMessageChunk.text(
            text=message,
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        await self.send_update(update)

    async def send_agent_thought(
        self,
        message: str,
        *,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> None:
        """Send a text message notification."""
        update = AgentThoughtChunk.text(
            text=message,
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        await self.send_update(update)

    async def send_user_message(
        self,
        message: str,
        *,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> None:
        """Send a user message notification."""
        update = UserMessageChunk.text(
            text=message,
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        await self.send_update(update)

    async def send_user_image(
        self,
        data: str | bytes,
        mime_type: str,
        *,
        uri: str | None = None,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> None:
        """Send a user image notification."""
        update = UserMessageChunk.image(
            data=data,
            mime_type=mime_type,
            uri=uri,
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        await self.send_update(update)

    async def send_user_audio(
        self,
        data: str | bytes,
        mime_type: str,
        *,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> None:
        """Send a user audio notification."""
        update = UserMessageChunk.audio(
            data=data,
            mime_type=mime_type,
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        await self.send_update(update)

    async def send_user_resource(
        self,
        uri: str,
        name: str,
        *,
        description: str | None = None,
        mime_type: str | None = None,
        size: int | None = None,
        title: str | None = None,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> None:
        """Send a user resource link notification."""
        update = UserMessageChunk.resource(
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type,
            size=size,
            title=title,
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        await self.send_update(update)

    async def replay(self, messages: Sequence[ModelRequest | ModelResponse]) -> None:
        """Replay a sequence of model messages as notifications.

        Collects all SessionUpdate objects from message conversion first, then
        sends them in batches of ``notification_batch_size``. When the client
        supports ``_batch_session_updates``, uses ext_notification for batch
        delivery; otherwise falls back to sequential ``session/update``.
        """
        # Collect all updates from all messages first
        all_updates: list[SessionUpdate] = []
        for message in messages:
            try:
                match message:
                    case ModelRequest():
                        all_updates.extend(self._collect_request_updates(message))
                    case ModelResponse():
                        all_updates.extend(self._collect_response_updates(message))
                    case _ as unreachable:
                        assert_never(unreachable)
            except Exception as e:
                self.log.exception("Failed to replay message", error=str(e))

        # Send in batches
        for i in range(0, len(all_updates), self.notification_batch_size):
            batch = all_updates[i : i + self.notification_batch_size]
            await self.send_batch_update(batch)
            if self.notification_flush_interval > 0 and i + self.notification_batch_size < len(
                all_updates
            ):
                await asyncio.sleep(self.notification_flush_interval)

    def _collect_request_updates(self, request: ModelRequest) -> list[SessionUpdate]:
        """Convert a ModelRequest to a list of SessionUpdate objects.

        This is a pure conversion — no I/O is performed. The
        ``_tool_call_inputs`` cache is consumed (popped) for ToolReturnPart.

        Args:
            request: The ModelRequest to convert.

        Returns:
            List of SessionUpdate objects in order.
        """
        updates: list[SessionUpdate] = []
        for part in request.parts:
            match part:
                case UserPromptPart(content=content) if isinstance(content, str):
                    updates.append(UserMessageChunk.text(text=content))
                case UserPromptPart(content=content):
                    converted_content = to_acp_content_blocks(content)
                    for block in converted_content:
                        match block:
                            case TextContentBlock(text=text):
                                updates.append(UserMessageChunk.text(text=text))
                            case ImageContentBlock(annotations=annots) as img_block:
                                updates.append(
                                    UserMessageChunk.image(
                                        data=img_block.data,
                                        mime_type=img_block.mime_type,
                                        uri=img_block.uri,
                                        audience=annots.audience if annots else None,
                                        last_modified=annots.last_modified if annots else None,
                                        priority=annots.priority if annots else None,
                                    )
                                )
                            case AudioContentBlock(annotations=annots) as audio_block:
                                updates.append(
                                    UserMessageChunk.audio(
                                        data=audio_block.data,
                                        mime_type=audio_block.mime_type,
                                        audience=annots.audience if annots else None,
                                        last_modified=annots.last_modified if annots else None,
                                        priority=annots.priority if annots else None,
                                    )
                                )
                            case ResourceContentBlock(annotations=annots) as resource_block:
                                updates.append(
                                    UserMessageChunk.resource(
                                        uri=resource_block.uri,
                                        name=resource_block.name,
                                        description=resource_block.description,
                                        mime_type=resource_block.mime_type,
                                        size=resource_block.size,
                                        title=resource_block.title,
                                        audience=annots.audience if annots else None,
                                        last_modified=annots.last_modified if annots else None,
                                        priority=annots.priority if annots else None,
                                    )
                                )
                            case EmbeddedResourceContentBlock(resource=resource):
                                match resource:
                                    case TextResourceContents(text=text):
                                        updates.append(UserMessageChunk.text(text=text))
                                    case BlobResourceContents(blob=blob, mime_type=mime_type):
                                        blob_size = len(blob) * 3 // 4
                                        size_mb = blob_size / (1024 * 1024)
                                        mime = mime_type or "unknown"
                                        msg = f"Embedded resource: {mime} ({size_mb:.2f} MB)"
                                        updates.append(UserMessageChunk.text(text=msg))
                                    case _ as unreachable:
                                        assert_never(unreachable)  # ty: ignore[type-assertion-failure]
                            case _ as unreachable:
                                assert_never(unreachable)

                case ToolReturnPart(
                    content=content, tool_name=tool_name, tool_call_id=tool_call_id
                ):
                    converted = to_acp_content_blocks(content)
                    tool_input = self._tool_call_inputs.get(tool_call_id, {})
                    acp_content = [ContentToolCallContent(content=block) for block in converted]
                    locations = [
                        ToolCallLocation(path=value)
                        for key, value in tool_input.items()
                        if key in {"path", "file_path", "filepath"} and isinstance(value, str)
                    ]
                    title = generate_tool_title(tool_name, tool_input)
                    updates.append(
                        ToolCallProgress(
                            tool_call_id=tool_call_id,
                            status="completed",
                            title=title,
                            locations=locations or None,
                            content=acp_content or None,
                            raw_output=converted,
                        )
                    )
                    self._tool_call_inputs.pop(tool_call_id, None)
                case _:
                    typ = type(part).__name__
                    self.log.debug("Unhandled request part type", part_type=typ)
        return updates

    def _collect_response_updates(self, response: ModelResponse) -> list[SessionUpdate]:
        """Convert a ModelResponse to a list of SessionUpdate objects.

        This is a pure conversion — no I/O is performed. The
        ``_tool_call_inputs`` cache is populated for ToolCallPart entries
        for later use by ToolReturnPart conversion.

        Args:
            response: The ModelResponse to convert.

        Returns:
            List of SessionUpdate objects in order.
        """
        from pydantic_ai import TextPart, ThinkingPart, ToolCallPart

        updates: list[SessionUpdate] = []
        for part in response.parts:
            match part:
                case TextPart(content=content):
                    updates.append(AgentMessageChunk.text(text=content))

                case ThinkingPart(content=content):
                    updates.append(AgentThoughtChunk.text(text=content))

                case ToolCallPart(tool_call_id=tool_call_id, tool_name=tool_name):
                    tool_input = safe_args_as_dict(part)
                    self._tool_call_inputs[tool_call_id] = tool_input
                    title = generate_tool_title(tool_name, tool_input)
                    updates.append(
                        ToolCallStart(
                            tool_call_id=tool_call_id,
                            status="pending",
                            title=title,
                            kind=infer_tool_kind(tool_name),
                            locations=None,
                            content=[],
                            raw_input=tool_input,
                        )
                    )

                case _:
                    typ = type(part).__name__
                    self.log.debug("Unhandled response part type", part_type=typ)
        return updates

    async def _replay_request(self, request: ModelRequest) -> None:
        """Replay a ModelRequest by sending collected updates sequentially.

        Thin wrapper around ``_collect_request_updates`` that sends each
        update via ``send_update()``. Preserves legacy behavior for
        non-batch callers.

        Args:
            request: The ModelRequest to replay.
        """
        updates = self._collect_request_updates(request)
        for update in updates:
            await self.send_update(update)

    async def _replay_response(self, response: ModelResponse) -> None:
        """Replay a ModelResponse by sending collected updates sequentially.

        Thin wrapper around ``_collect_response_updates`` that sends each
        update via ``send_update()``. Preserves legacy behavior for
        non-batch callers.

        Args:
            response: The ModelResponse to replay.
        """
        updates = self._collect_response_updates(response)
        for update in updates:
            await self.send_update(update)

    async def send_agent_image(
        self,
        data: str | bytes,
        mime_type: str,
        *,
        uri: str | None = None,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> None:
        """Send an image message notification."""
        update = AgentMessageChunk.image(
            data=data,
            mime_type=mime_type,
            uri=uri,
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        await self.send_update(update)

    async def update_session_mode(self, mode_id: str) -> None:
        """Send a session mode update notification."""
        update = CurrentModeUpdate(current_mode_id=mode_id)
        await self.send_update(update)

    async def update_config_option(
        self,
        config_id: str,
        value_id: str,
        config_options: Sequence[SessionConfigOption],
    ) -> None:
        """Send a config option update notification for a full config options update."""
        update = ConfigOptionUpdate(
            config_id=config_id,
            value_id=value_id,
            config_options=config_options,
        )
        await self.send_update(update)

    async def send_agent_audio(
        self,
        data: str | bytes,
        mime_type: str,
        *,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> None:
        """Send an audio message notification."""
        update = AgentMessageChunk.audio(
            data=data,
            mime_type=mime_type,
            last_modified=last_modified,
            priority=priority,
            audience=audience,
        )
        await self.send_update(update)

    async def send_agent_resource(
        self,
        name: str,
        uri: str,
        *,
        title: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        size: int | None = None,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ) -> None:
        """Send a resource reference message notification."""
        update = AgentMessageChunk.resource(
            name=name,
            uri=uri,
            title=title,
            description=description,
            mime_type=mime_type,
            size=size,
            audience=audience,
            last_modified=last_modified,
            priority=priority,
        )
        await self.send_update(update)
