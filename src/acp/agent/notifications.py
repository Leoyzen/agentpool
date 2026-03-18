"""ACP notification helper for clean session update API."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import structlog

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AvailableCommand,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    ContentToolCallContent,
    CurrentModeUpdate,
    FileEditToolCallContent,
    SessionNotification,
    TerminalToolCallContent,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)
from acp.schema.tool_call import ToolCallLocation
from acp.tool_call_reporter import ToolCallReporter


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

    def __init__(self, client: Client, session_id: str) -> None:
        """Initialize notifications helper.

        Args:
            client: ACP client and session_id
            session_id: Session identifier
        """
        self.client = client
        self.id = session_id
        self.log = logger.bind(session_id=session_id)

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
        await self.client.session_update(notification)

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
        config_options: Sequence[SessionConfigOption],
    ) -> None:
        """Send a config option update notification.

        Args:
            config_options: The full set of configuration options with current values.
        """
        update = ConfigOptionUpdate(config_options=config_options)
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
