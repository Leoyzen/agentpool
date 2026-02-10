from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast
from xml.sax.saxutils import escape

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from agentpool.prompts.instructions import InstructionFunc
    from agentpool.skills.registry import SkillsRegistry


logger = get_logger(__name__)

InjectionMode = Literal["off", "metadata", "full"]


class SkillsInstructionProvider(ResourceProvider):
    """ResourceProvider that injects skills as dynamic XML-formatted instructions.

    This provider implements RFC-0007's get_instructions() to inject skills
    into agent system prompts. It is separate from SkillsTools to maintain
    single responsibility principle.
    """

    kind: Literal["skills"] = "skills"

    def __init__(
        self,
        name: str = "skills_instructions",
        skills_registry: SkillsRegistry | None = None,
        injection_mode: InjectionMode = "metadata",
        max_skills: int | None = None,
        owner: str | None = None,
    ) -> None:
        """Initialize skills instruction provider.

        Args:
            name: Provider name
            skills_registry: Registry containing discovered skills
            injection_mode: "metadata" (names/desc) or "full" (complete instructions)
            max_skills: Maximum skills to include (None = all)
            owner: Optional owner of the provider
        """
        super().__init__(name=name, owner=owner)
        self.registry = skills_registry
        self.injection_mode = injection_mode
        self.max_skills = max_skills

    async def get_instructions(self) -> list[InstructionFunc]:
        """Return skill injection instruction functions (RFC-0007)."""
        return [self._generate_skills_instruction]

    async def _generate_skills_instruction(self, ctx: AgentContext) -> str:
        """Generate XML-formatted skills section.

        This instruction function is called on each agent run.
        """
        if self.registry is None:
            return ""

        # 1. Check for overrides in agent context
        injection_mode = self.injection_mode
        max_skills = self.max_skills

        # Traverse providers to find SkillsTools (usually named "skills")
        # and extract overrides if present.
        node = ctx.node
        if (tools := getattr(node, "tools", None)) and (
            providers := getattr(tools, "providers", None)
        ):
            for provider in providers:
                if getattr(provider, "name", None) == "skills":
                    # Check for overrides on the provider instance
                    if (val := getattr(provider, "injection_mode", None)) is not None:
                        injection_mode = val
                    if (val := getattr(provider, "max_skills", None)) is not None:
                        max_skills = val
                    break

        if injection_mode == "off":
            return ""

        # Apply limit if configured
        skill_items = list(self.registry.items())
        if not skill_items:
            return ""

        if max_skills is not None:
            skill_items = skill_items[:max_skills]

        # Build XML
        return await self._format_skills_xml(skill_items, cast(InjectionMode, injection_mode))

    async def _format_skills_xml(
        self,
        skill_items: list[tuple[str, Any]],
        mode: InjectionMode,
    ) -> str:
        """Format skills using structured XML format."""
        lines = ["<available-skills>"]

        for name, skill in skill_items:
            try:
                if mode == "metadata":
                    content = self._format_skill_metadata(name, skill)
                elif mode == "full":
                    # Load instructions if available
                    instructions = ""
                    if hasattr(skill, "load_instructions"):
                        instructions = skill.load_instructions()
                    elif hasattr(skill, "instructions"):
                        instructions = skill.instructions or ""

                    content = self._format_skill_full(name, skill, instructions)
                else:
                    continue
                lines.append(content)
            except Exception:
                logger.exception("Failed to format skill for injection", skill=name)
                continue

        lines.append("</available-skills>")
        return "\n".join(lines)

    def _format_skill_metadata(self, name: str, skill: Any) -> str:
        """Format skill metadata in XML."""
        desc = escape(str(skill.description)) if hasattr(skill, "description") else ""
        return f'  <skill id="{escape(name)}" name="{escape(name)}" description="{desc}" />'

    def _format_skill_full(self, name: str, skill: Any, instructions: str) -> str:
        """Format full skill content in XML."""
        desc = escape(str(skill.description)) if hasattr(skill, "description") else ""
        path = str(skill.skill_path) if hasattr(skill, "skill_path") else ""

        return f"""  <skill id="{escape(name)}" name="{escape(name)}" description="{desc}">
    <instructions>
      <skill-instruction>
      Base directory for this skill: {path}/
      File references (@path) are relative to this directory.

      {instructions}
      </skill-instruction>

      <user-request>
      $ARGUMENTS
      </user-request>
    </instructions>
  </skill>"""
