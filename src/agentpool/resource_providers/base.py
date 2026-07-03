"""Base resource provider interface."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Self

from anyenv.signals import Signal

from agentpool.log import get_logger
from agentpool.tools.base import Tool
from agentpool_config.tools import ToolHints


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from types import TracebackType

    from pydantic_ai import ModelRequestPart, RunContext
    from pydantic_ai.tools import ToolDefinition
    from schemez import OpenAIFunctionDefinition

    from agentpool.agents.context import AgentContext
    from agentpool.prompts.instructions import InstructionFunc
    from agentpool.prompts.prompts import BasePrompt
    from agentpool.resource_providers.resource_info import ResourceInfo
    from agentpool.skills.skill import Skill
    from agentpool.tools.base import ToolKind


logger = get_logger(__name__)


ResourceType = Literal["tools", "prompts", "resources", "skills"]
ProviderKind = Literal[
    "base", "mcp", "mcp_run", "tools", "prompts", "skills", "aggregating", "custom"
]


@dataclass(frozen=True, slots=True)
class ResourceChangeEvent:
    """Event emitted when resources change in a provider.

    Attributes:
        provider_name: Name of the provider instance
        provider_kind: Kind/type of the provider (e.g., "mcp", "tools")
        resource_type: Type of resource that changed
        owner: Optional owner of the provider (e.g., agent name)
    """

    provider_name: str
    provider_kind: ProviderKind
    resource_type: ResourceType
    owner: str | None = None


class ResourceProvider(ABC):  # noqa: B024
    """Base class for resource providers.

    Provides tools, prompts, and other resources to agents.
    Default implementations return empty lists - override as needed.

    Class Attributes:
        kind: Short slug identifying the provider type (e.g., "mcp", "tools")

    Change signals (using anyenv.signals.Signal):
        - tools_changed: Emitted when tools change
        - prompts_changed: Emitted when prompts change
        - resources_changed: Emitted when resources change
        - skills_changed: Emitted when skills change

    Example:
        provider.tools_changed.connect(my_handler)
        await provider.tools_changed.emit(provider.create_change_event("tools"))
    """

    kind: ProviderKind = "base"

    # Change signals - emit ResourceChangeEvent when resources change
    tools_changed: Signal[ResourceChangeEvent] = Signal()
    prompts_changed: Signal[ResourceChangeEvent] = Signal()
    resources_changed: Signal[ResourceChangeEvent] = Signal()
    skills_changed: Signal[ResourceChangeEvent] = Signal()

    def __init__(self, name: str, owner: str | None = None) -> None:
        """Initialize the resource provider."""
        self.name = name
        self.owner = owner
        self.log = logger.bind(name=self.name, owner=self.owner)

    def create_change_event(self, resource_type: ResourceType) -> ResourceChangeEvent:
        """Create a ResourceChangeEvent for this provider."""
        return ResourceChangeEvent(
            provider_name=self.name,
            provider_kind=self.kind,
            resource_type=resource_type,
            owner=self.owner,
        )

    async def __aenter__(self) -> Self:
        """Async context entry if required."""
        return self

    async def __aexit__(  # noqa: B027
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context cleanup if required."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"

    def as_capability(self) -> Any:
        """Return a pydantic-ai capability representing this provider's tools.

        Converts AgentPool Tool objects to pydantic-ai Tool instances via
        Tool.to_pydantic_ai() and wraps them in a FunctionToolset, exposed
        through a Toolset capability for lazy evaluation.

        Tools with ``requires_confirmation=True`` are wrapped in an
        ``ApprovalRequiredToolset`` so pydantic-ai defers their execution
        until explicit approval is granted.

        Returns:
            A pydantic-ai AbstractCapability (Toolset) that contributes this
            provider's tools when the agent runs.
        """
        from pydantic_ai.capabilities import Toolset
        from pydantic_ai.toolsets import (
            ApprovalRequiredToolset,
            CombinedToolset,
            FunctionToolset,
        )

        async def _build_toolset(ctx: Any) -> Any:
            try:
                tools = await self.get_tools()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to retrieve tools from provider",
                    provider=self.name,
                    exc_info=True,
                )
                return None
            if not tools:
                return None

            normal_tools = [t for t in tools if not t.requires_confirmation]
            confirm_tools = [t for t in tools if t.requires_confirmation]

            toolsets: list[Any] = []
            if normal_tools:
                pa_tools = [self._wrap_for_pydantic_ai(tool) for tool in normal_tools]
                toolsets.append(FunctionToolset(pa_tools, id=self.name))
            if confirm_tools:
                pa_tools = [self._wrap_for_pydantic_ai(tool) for tool in confirm_tools]
                toolsets.append(ApprovalRequiredToolset(FunctionToolset(pa_tools, id=self.name)))

            if not toolsets:
                return None
            if len(toolsets) == 1:
                return toolsets[0]
            return CombinedToolset(toolsets)

        return Toolset(_build_toolset)

    @staticmethod
    def _wrap_for_pydantic_ai(tool: Tool[Any]) -> Any:
        """Wrap an AgentPool tool so pydantic-ai can schema-generate it.

        .. deprecated::
            Delegate to ``agentpool.tools.tool_wrapping.wrap_tool_for_pydantic_ai``.
            This method is kept for backwards compatibility and will be removed
            when ``ResourceProvider`` is removed.
        """
        from agentpool.tools.tool_wrapping import wrap_tool_for_pydantic_ai

        return wrap_tool_for_pydantic_ai(tool)

    async def get_tools(self) -> Sequence[Tool]:
        """Get available tools. Override to provide tools."""
        return []

    async def get_tool(self, tool_name: str) -> Tool:
        """Get specific tool."""
        tools = await self.get_tools()
        for tool in tools:
            if tool.name == tool_name:
                return tool

        raise ValueError(f"Tool {tool_name!r} not found")

    async def get_prompts(self) -> list[BasePrompt]:
        """Get available prompts. Override to provide prompts."""
        return []

    async def get_resources(self) -> list[ResourceInfo]:
        """Get available resources. Override to provide resources."""
        return []

    async def get_instructions(self) -> list[InstructionFunc]:
        """Get available instruction functions. Override to provide instructions."""
        return []

    async def get_skill_instructions(
        self, skill_name: str, arguments: dict[str, str] | None = None
    ) -> str:
        """Get full instructions for a specific skill.

        Args:
            skill_name: Name of the skill to get instructions for
            arguments: Optional arguments for prompt-based skills

        Returns:
            The full skill instructions for execution

        Raises:
            KeyError: If skill not found
        """
        raise KeyError(f"Skill {skill_name!r} not found")

    async def get_request_parts(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> list[ModelRequestPart]:
        """Get a prompt formatted with arguments.

        Args:
            name: Name of the prompt to format
            arguments: Optional arguments for prompt formatting

        Returns:
            Single chat message with merged content

        Raises:
            KeyError: If prompt not found
            ValueError: If formatting fails
        """
        prompts = await self.get_prompts()
        prompt = next((p for p in prompts if p.name == name), None)
        if not prompt:
            raise KeyError(f"Prompt {name!r} not found")

        messages = await prompt.format(arguments or {})
        if not messages:
            raise ValueError(f"Prompt {name!r} produced no messages")

        return [p for prompt_msg in messages for p in prompt_msg.to_pydantic_parts()]

    def create_tool(
        self,
        fn: Callable[..., Any],
        read_only: bool | None = None,
        destructive: bool | None = None,
        idempotent: bool | None = None,
        open_world: bool | None = None,
        requires_confirmation: bool = False,
        metadata: dict[str, Any] | None = None,
        category: ToolKind | None = None,
        name_override: str | None = None,
        description_override: str | None = None,
        schema_override: OpenAIFunctionDefinition | None = None,
        prepare: Callable[
            [RunContext[AgentContext], ToolDefinition], Awaitable[ToolDefinition | None]
        ]
        | None = None,
    ) -> Tool:
        """Create a tool from a function.

        Args:
            fn: Function to create a tool from
            read_only: Whether the tool is read-only
            destructive: Whether the tool is destructive
            idempotent: Whether the tool is idempotent
            open_world: Whether the tool is open-world
            requires_confirmation: Whether the tool requires confirmation
            metadata: Metadata for the tool
            category: Category of the tool
            name_override: Override the name of the tool
            description_override: Override the description of the tool
            schema_override: Override the schema of the tool
            prepare: Optional prepare function to modify tool definition before execution

        Returns:
            Tool created from the function
        """
        return Tool.from_callable(
            fn=fn,
            category=category,
            source=self.name,
            requires_confirmation=requires_confirmation,
            metadata=metadata,
            name_override=name_override,
            description_override=description_override,
            schema_override=schema_override,
            prepare=prepare,
            hints=ToolHints(
                read_only=read_only,
                destructive=destructive,
                idempotent=idempotent,
                open_world=open_world,
            ),
        )

    # Skill-related methods - subclasses should override these

    async def get_skills(self) -> list[Skill]:
        """Get all available skills from this provider.

        Returns:
            List of Skill objects
        """
        return []

    async def get_skill(self, name: str) -> Any:
        """Get a specific skill by name.

        Args:
            name: Name of the skill

        Returns:
            The Skill object

        Raises:
            SkillNotFoundError: If skill not found
        """
        from agentpool.skills.exceptions import SkillNotFoundError

        raise SkillNotFoundError(name)

    async def get_references(self, skill_name: str) -> list[str | dict[str, Any]]:
        """Get list of available reference files for a skill.

        Args:
            skill_name: Name of the skill

        Returns:
            List of reference file paths
        """
        return []

    async def read_reference(self, skill_name: str, ref_path: str) -> tuple[bytes, str]:
        """Read a reference file for a skill.

        Args:
            skill_name: Name of the skill
            ref_path: Path to the reference file (relative to references/)

        Returns:
            Tuple of (content bytes, MIME type)

        Raises:
            SkillNotFoundError: If skill not found
            ReferenceNotFoundError: If reference file not found
        """
        from agentpool.skills.exceptions import SkillNotFoundError

        raise SkillNotFoundError(skill_name)
