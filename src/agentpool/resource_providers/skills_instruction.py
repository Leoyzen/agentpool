from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast
from xml.sax.saxutils import escape

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider
from agentpool.skills.capability import SkillCapability


if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.capabilities import AbstractCapability

    from agentpool.prompts.instructions import InstructionFunc
    from agentpool.resource_providers.aggregating import AggregatingResourceProvider
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
        skill_provider: AggregatingResourceProvider | None = None,
        injection_mode: InjectionMode = "metadata",
        max_skills: int | None = None,
        owner: str | None = None,
    ) -> None:
        """Initialize skills instruction provider.

        Args:
            name: Provider name
            skills_registry: Registry containing discovered skills
            skill_provider: Aggregating provider combining local + MCP skills
            injection_mode: "metadata" (names/desc) or "full" (complete instructions)
            max_skills: Maximum skills to include (None = all)
            owner: Optional owner of the provider
        """
        super().__init__(name=name, owner=owner)
        self.registry = skills_registry
        self.skill_provider = skill_provider
        self.injection_mode = injection_mode
        self.max_skills = max_skills

    async def get_instructions(self) -> list[InstructionFunc]:
        """Return skill injection instruction functions (RFC-0007)."""
        return [self._generate_skills_instruction]

    async def _generate_skills_instruction(
        self,
        ctx: RunContext[AgentContext[Any]],
    ) -> str:
        """Generate XML-formatted skills section.

        This instruction function is called on each agent run.
        Accepts pydantic-ai RunContext with AgentContext as deps.
        """
        agent_ctx = ctx.deps
        # 1. Check for overrides in agent context
        injection_mode = self.injection_mode
        max_skills = self.max_skills

        # Traverse providers to find SkillsTools (usually named "skills")
        # and extract overrides if present.
        node = agent_ctx.node
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

        # 2. Collect skills from skill_provider (includes local + MCP) or registry.
        # Deduplicate by name with first-wins priority (local skills appear first
        # in the AggregatingResourceProvider's provider order).
        skill_items: list[tuple[str, Any]] = []
        seen: set[str] = set()
        if self.skill_provider is not None:
            skills = await self.skill_provider.get_skills()
            for skill in skills:
                if skill.name not in seen:
                    seen.add(skill.name)
                    skill_items.append((skill.name, skill))
        elif self.registry is not None:
            skill_items = list(self.registry.items())

        if not skill_items:
            return ""

        node_name = getattr(agent_ctx.node, "name", None)
        visibility_checker = getattr(agent_ctx.pool, "is_skill_visible_to_node", None)
        if visibility_checker is not None:
            skill_items = [
                (name, skill) for name, skill in skill_items if visibility_checker(skill, node_name)
            ]
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
                # Skip skills that disable model invocation
                if getattr(skill, "disable_model_invocation", False):
                    continue

                if mode == "metadata":
                    content = self._format_skill_metadata(name, skill)
                elif mode == "full":
                    # Delegate instruction content to SkillCapability
                    cap = SkillCapability(skill)
                    instructions_raw = cap.get_instructions()
                    instructions = instructions_raw if isinstance(instructions_raw, str) else ""

                    content = self._format_skill_full(name, skill, instructions)
                else:
                    continue
                lines.append(content)
            except Exception:
                logger.exception("Failed to format skill for injection", skill=name)
                continue

        lines.append("</available-skills>")
        return "\n".join(lines)

    def as_capability(self) -> AbstractCapability | None:
        """Return a pydantic-ai capability for this provider.

        Returns:
            A pydantic-ai AbstractCapability instance, or None.
        """
        return None

    def _format_skill_metadata(self, name: str, skill: Any) -> str:
        """Format skill metadata in XML."""
        desc = escape(str(skill.description)) if hasattr(skill, "description") else ""

        # Build optional metadata attributes
        attrs: list[str] = []
        if getattr(skill, "user_invocable", True) is False:
            attrs.append('user-invocable="false"')
        if context := getattr(skill, "context", None):
            attrs.append(f'context="{escape(context)}"')
        if agent := getattr(skill, "agent", None):
            attrs.append(f'agent="{escape(agent)}"')

        attr_str = " " + " ".join(attrs) if attrs else ""

        # Get skill URI/path for reference — use safe_uri to avoid leaking
        # absolute filesystem paths for local skills
        skill_uri = ""
        if hasattr(skill, "safe_uri"):
            skill_uri = skill.safe_uri
        elif hasattr(skill, "skill_path"):
            skill_uri = str(skill.skill_path)

        # Build inner content
        lines: list[str] = [
            f"<skill{attr_str}>",
            f"<id>{escape(name)}</id>",
            f"<name>{escape(name)}</name>",
            f"<description>{desc}</description>",
        ]

        # Add skill URI if available (helps agents reference skills correctly)
        if skill_uri:
            lines.append(f"<uri>{escape(skill_uri)}</uri>")

        # Add argument hint if present
        if arg_hint := getattr(skill, "argument_hint", None):
            lines.append(f"<argument-hint>{escape(arg_hint)}</argument-hint>")

        # Add tools if present (SkillCapability manages these at runtime)
        if tools := getattr(skill, "tools", None):
            lines.append("<tools>")
            lines.extend(f"<tool>{escape(tool.import_path)}</tool>" for tool in tools)
            lines.append("</tools>")

        # Add MCP servers if present (SkillCapability manages these at runtime)
        if mcp_servers := getattr(skill, "mcp_servers", None):
            lines.append("<mcp_servers>")
            lines.extend(f"<server>{escape(server_name)}</server>" for server_name in mcp_servers)
            lines.append("</mcp_servers>")

        lines.append("</skill>")
        return "\n".join(lines)

    def _format_skill_full(self, name: str, skill: Any, instructions: str) -> str:
        """Format full skill content in XML with tool/MCP hints."""
        path = (
            skill.safe_uri
            if hasattr(skill, "safe_uri")
            else (str(skill.skill_path) if hasattr(skill, "skill_path") else "")
        )

        # Build tool/MCP hints from SkillCapability-managed resources
        hints_parts: list[str] = []
        if tools := getattr(skill, "tools", None):
            tool_names = [t.import_path for t in tools]
            hints_parts.append(f"Available tools: {', '.join(tool_names)}")
        if mcp_servers := getattr(skill, "mcp_servers", None):
            hints_parts.append(f"MCP servers: {', '.join(mcp_servers.keys())}")

        hints_block = "\n".join(hints_parts) if hints_parts else ""

        # No leading indentation inside instruction text (LLM-sensitive); outer XML only.
        return f"""<skill_content id="{escape(name)}" name="{escape(name)}">
<instructions>
<skill-instruction>
Base directory for this skill: {path}/
File references (@path) are relative to this directory.

{instructions}
{hints_block}
</skill-instruction>
<user-request>
$ARGUMENTS
</user-request>
</instructions>
</skill_content>"""
