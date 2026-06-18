"""Aggregating resource provider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agentpool.resource_providers.base import ResourceChangeEvent, ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai import ModelRequestPart
    from pydantic_ai.capabilities import AbstractCapability

    from agentpool.prompts.prompts import BasePrompt
    from agentpool.resource_providers.resource_info import ResourceInfo
    from agentpool.skills.skill import Skill
    from agentpool.tools.base import Tool

ToolMode = Literal["codemode"]

_ = ResourceChangeEvent  # Used at runtime in method signatures


class AggregatingResourceProvider(ResourceProvider):
    """Provider that combines resources from multiple providers.

    Automatically forwards change signals from child providers.
    When a child emits tools_changed, this provider re-emits it.
    """

    kind = "aggregating"

    def __init__(
        self,
        providers: list[ResourceProvider],
        name: str = "aggregating",
        tool_mode: ToolMode | None = None,
    ) -> None:
        """Initialize provider with list of providers to aggregate.

        Args:
            providers: Resource providers to aggregate (stores reference to list)
            name: Name for this provider
            tool_mode: Optional tool execution mode ("codemode" wraps all tools)
        """
        super().__init__(name=name)
        self._providers: list[ResourceProvider] = []
        self.tool_mode = tool_mode
        self._codemode_provider: ResourceProvider | None = None
        # Use property setter to set up signal forwarding
        self.providers = providers

    @property
    def providers(self) -> list[ResourceProvider]:
        """Get the list of child providers."""
        return self._providers

    @providers.setter
    def providers(self, value: list[ResourceProvider]) -> None:
        """Set the list of child providers and set up signal forwarding."""
        # Disconnect from old providers
        for provider in self._providers:
            provider.tools_changed.disconnect(self._forward_tools_changed)
            provider.prompts_changed.disconnect(self._forward_prompts_changed)
            provider.resources_changed.disconnect(self._forward_resources_changed)
            provider.skills_changed.disconnect(self._forward_skills_changed)

        self._providers = value

        # Connect to new providers
        for provider in self._providers:
            provider.tools_changed.connect(self._forward_tools_changed)
            provider.prompts_changed.connect(self._forward_prompts_changed)
            provider.resources_changed.connect(self._forward_resources_changed)
            provider.skills_changed.connect(self._forward_skills_changed)

    async def _forward_tools_changed(self, event: ResourceChangeEvent) -> None:
        """Forward tools_changed signal from child provider."""
        await self.tools_changed.emit(event)

    async def _forward_prompts_changed(self, event: ResourceChangeEvent) -> None:
        """Forward prompts_changed signal from child provider."""
        await self.prompts_changed.emit(event)

    async def _forward_resources_changed(self, event: ResourceChangeEvent) -> None:
        """Forward resources_changed signal from child provider."""
        await self.resources_changed.emit(event)

    async def _forward_skills_changed(self, event: ResourceChangeEvent) -> None:
        """Forward skills_changed signal from child provider."""
        await self.skills_changed.emit(event)

    async def get_tools(self) -> Sequence[Tool]:
        """Get tools from all providers.

        If tool_mode="codemode", wraps all tools in a single Python execution tool.
        """
        from agentpool.resource_providers.codemode.provider import CodeModeResourceProvider
        from agentpool.resource_providers.static import StaticResourceProvider

        # Get all tools from child providers
        all_tools = [t for provider in self.providers for t in await provider.get_tools()]

        # If codemode, wrap all tools in a single codemode tool
        if self.tool_mode == "codemode":
            # Always create fresh static provider with current tools
            static = StaticResourceProvider("codemode_static", tools=all_tools)

            if self._codemode_provider is None:
                self._codemode_provider = CodeModeResourceProvider([static])
            else:
                # Update the providers list on existing codemode provider
                # Type narrowing: we know it's CodeModeResourceProvider at this point
                codemode = self._codemode_provider
                assert isinstance(codemode, CodeModeResourceProvider)
                codemode.providers = [static]

            return list(await self._codemode_provider.get_tools())

        return all_tools

    async def get_prompts(self) -> list[BasePrompt]:
        """Get prompts from all providers."""
        return [p for provider in self.providers for p in await provider.get_prompts()]

    async def get_resources(self) -> list[ResourceInfo]:
        """Get resources from all providers."""
        return [r for provider in self.providers for r in await provider.get_resources()]

    async def get_skills(self) -> list[Skill]:
        """Get skills from all providers."""
        return [s for provider in self.providers for s in await provider.get_skills()]

    async def get_skill_instructions(
        self, skill_name: str, arguments: dict[str, str] | None = None
    ) -> str:
        """Get skill instructions from the first provider that has it.

        Args:
            skill_name: Name of the skill
            arguments: Optional arguments for prompt-based skills

        Returns:
            Skill instructions as string

        Raises:
            SkillNotFoundError: If skill not found in any provider
        """
        from agentpool.skills.exceptions import SkillNotFoundError

        last_not_found: SkillNotFoundError | None = None
        for provider in self.providers:
            try:
                # Check if provider has this skill
                skills = await provider.get_skills()
                if any(s.name == skill_name for s in skills):
                    return await provider.get_skill_instructions(skill_name, arguments)
            except SkillNotFoundError as exc:
                last_not_found = exc
                continue

        if last_not_found is not None:
            raise last_not_found
        raise SkillNotFoundError(skill_name)

    async def get_request_parts(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> list[ModelRequestPart]:
        """Try to get prompt from first provider that has it."""
        for provider in self.providers:
            try:
                return await provider.get_request_parts(name, arguments)
            except KeyError:
                continue

        raise KeyError(f"Prompt {name!r} not found in any provider")

    async def get_references(self, skill_name: str) -> list[str]:
        """Get list of available reference files for a skill from all providers.

        Aggregates results from child providers that have the skill.

        Args:
            skill_name: Name of the skill (kebab-case, matches Skill.name)

        Returns:
            List of reference file paths
        """
        references: list[str] = []
        for provider in self.providers:
            try:
                skills = await provider.get_skills()
                if any(s.name == skill_name for s in skills):
                    provider_refs = await provider.get_references(skill_name)
                    # Normalize: providers may return list[str] or list[dict]
                    for ref in provider_refs:
                        if isinstance(ref, dict):
                            path = ref.get("path", ref.get("name", ""))
                            if path:
                                references.append(path)
                        else:
                            references.append(ref)
            except Exception:  # noqa: BLE001
                continue
        return references

    async def read_reference(self, skill_name: str, ref_path: str) -> tuple[bytes, str]:
        """Read reference content from the first provider that has the skill.

        Args:
            skill_name: Name of the skill (kebab-case, matches Skill.name)
            ref_path: Path to the reference file (relative to references/)

        Returns:
            Tuple of (content bytes, MIME type)

        Raises:
            SkillNotFoundError: If skill not found in any provider
        """
        from agentpool.skills.exceptions import SkillNotFoundError

        for provider in self.providers:
            try:
                # Check if provider has this skill
                skills = await provider.get_skills()
                if any(s.name == skill_name for s in skills):
                    return await provider.read_reference(skill_name, ref_path)
            except Exception:  # noqa: BLE001
                continue

        raise SkillNotFoundError(f"Reference {ref_path!r} not found for skill {skill_name!r}")

    def as_capability(self) -> AbstractCapability | None:
        """Return a pydantic-ai capability for this provider.

        Returns:
            A pydantic-ai AbstractCapability instance, or None.
        """
        return None
