"""Domain-specific Resource Protocols for unified extension access.

Defines Protocol interfaces for skill, MCP, and command resource access.
Each protocol is ``@runtime_checkable`` so capabilities can be queried via
``isinstance(cap, SkillResource)`` etc.

ChangeEvent is imported from ``agentpool.capabilities.change_event`` —
this module does NOT define a duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import PurePosixPath

    from upath import UPath

    from agentpool.capabilities.change_event import ChangeEvent


# ---- Dataclasses ----


@dataclass(frozen=True, slots=True)
class SkillEntry:
    """A skill descriptor returned by ``SkillResource.list_skills()``.

    Attributes:
        name: Skill name (e.g., ``"ponytail"``).
        description: Short human-readable description.
        uri: Canonical URI (e.g., ``"skill://ponytail/SKILL.md"``).
        source: Where the skill comes from — ``"local"`` or ``"remote"``.
        skill_path: Real filesystem path for local skills (``UPath``) or
            ``None`` for virtual/MCP skills.
    """

    name: str
    description: str = ""
    uri: str = ""
    source: str = "local"
    skill_path: UPath | PurePosixPath | None = None


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """A tool descriptor returned by ``McpResource.list_tools()``.

    Attributes:
        name: Tool name as known to the MCP server.
        description: Tool description from the server.
        schema: JSON schema dict for the tool's input parameters.
    """

    name: str
    description: str = ""
    schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Result of an MCP tool call.

    Attributes:
        content: The tool output content as text.
        is_error: Whether the tool returned an error.
    """

    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ResourceEntry:
    """A resource descriptor returned by ``McpResource.list_resources()``.

    Attributes:
        uri: Resource URI (e.g., ``"file:///path/to/resource"``).
        name: Human-readable resource name.
        description: Optional description.
        mime_type: MIME type of the resource content.
    """

    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""


@dataclass(frozen=True, slots=True)
class CommandEntry:
    """A command descriptor returned by ``CommandResource.list_commands()``.

    Attributes:
        name: Command name (e.g., ``"ponytail"``).
        description: Short description of what the command does.
        skill_uri: URI of the skill backing this command, if any.
        source: Where the command comes from — ``"local"`` or ``"remote"``.
    """

    name: str
    description: str = ""
    skill_uri: str = ""
    source: str = "local"


# ---- Protocols ----


@runtime_checkable
class SkillResource(Protocol):
    """Protocol for accessing skill resources."""

    async def list_skills(self) -> Sequence[SkillEntry]:
        """Return all available skills.

        Returns:
            Sequence of ``SkillEntry`` descriptors.
        """
        ...

    async def read_skill(self, name: str) -> str | None:
        """Read skill content by name.

        Args:
            name: Skill name to read.

        Returns:
            Skill content as string, or ``None`` if not found.
        """
        ...

    async def skill_exists(self, name: str) -> bool:
        """Check if a skill exists without reading it.

        Args:
            name: Skill name to check.

        Returns:
            ``True`` if the skill exists, ``False`` otherwise.
        """
        ...


@runtime_checkable
class McpResource(Protocol):
    """Protocol for accessing MCP tools and resources."""

    async def list_tools(self) -> Sequence[ToolEntry]:
        """List available MCP tools.

        Returns:
            Sequence of ``ToolEntry`` descriptors.
        """
        ...

    async def call_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Call an MCP tool.

        Args:
            name: Tool name to call.
            args: Arguments to pass to the tool.

        Returns:
            ``ToolResult`` with the tool output.
        """
        ...

    async def list_resources(self) -> Sequence[ResourceEntry]:
        """List available MCP resources.

        Returns:
            Sequence of ``ResourceEntry`` descriptors.
        """
        ...

    async def read_resource(self, uri: str) -> str | None:
        """Read an MCP resource by URI.

        Args:
            uri: Resource URI to read.

        Returns:
            Resource content as string, or ``None`` if not found.
        """
        ...

    async def resource_exists(self, uri: str) -> bool:
        """Check if an MCP resource exists.

        Args:
            uri: Resource URI to check.

        Returns:
            ``True`` if the resource exists, ``False`` otherwise.
        """
        ...


@runtime_checkable
class CommandResource(Protocol):
    """Protocol for accessing commands (slash commands)."""

    async def list_commands(self) -> Sequence[CommandEntry]:
        """List available commands.

        Returns:
            Sequence of ``CommandEntry`` descriptors.
        """
        ...

    async def get_command(self, name: str) -> CommandEntry | None:
        """Get a specific command by name.

        Args:
            name: Command name to retrieve.

        Returns:
            ``CommandEntry`` if found, ``None`` otherwise.
        """
        ...


@runtime_checkable
class ChangeObservable(Protocol):
    """Protocol for capabilities that emit change notifications."""

    def on_change(self) -> AsyncIterator[ChangeEvent] | None:
        """Return an async iterator of change events, or ``None``.

        Returns:
            An ``AsyncIterator[ChangeEvent]`` if the capability supports
            change notifications, ``None`` otherwise.
        """
        ...
