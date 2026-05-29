"""Provider for skills and commands tools."""

from __future__ import annotations

import contextlib
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, cast

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.resource_providers import StaticResourceProvider
from agentpool.skills.uri_resolver import ResolvedSkillURI


if TYPE_CHECKING:
    from agentpool.skills.skill import Skill
    from agentpool.skills.uri_resolver import SkillURIResolver


SKILL_USAGE_GUIDANCE = """
## Skill URI Format

Skills can be loaded using either a bare skill name or a skill:// URI:

### Bare Skill Name (Backward Compatible)
- `python-expert` - Load skill by name (searches all providers)

### URI Format
- `skill://provider/skill-name` - Load skill from specific provider
- `skill://provider/skill-name/references/file.md` - Load with reference file

### Argument Substitution
When providing arguments, the following substitutions are made:
- `$1`, `$2`, ... - Replaced with the Nth argument
- `$@` - Replaced with all arguments
- `$ARGUMENTS` - Replaced with all arguments

Example: `load_skill(ctx, "skill://local/python-expert", "arg1 arg2")`
"""

BASE_DESC = f"""Load a Claude Code Skill and return its instructions.

This tool provides access to Claude Code Skills - specialized workflows and techniques
for handling specific types of tasks. When you need to use a skill, call this tool
with the skill name or URI.

{SKILL_USAGE_GUIDANCE}

Available skills:"""


def _substitute_arguments(instructions: str, arguments: str | None) -> str:
    """Substitute argument placeholders in skill instructions.

    Supports:
    - $1, $2, ... - Nth argument
    - $@ - All arguments
    - $ARGUMENTS - All arguments

    Args:
        instructions: The skill instructions to process
        arguments: Space-separated arguments string

    Returns:
        Instructions with placeholders replaced
    """
    if arguments is None:
        return instructions

    args_list = arguments.split() if arguments else []

    # Replace positional arguments $1, $2, etc.
    for i, arg in enumerate(args_list, start=1):
        instructions = instructions.replace(f"${i}", arg)

    # Replace $@ and $ARGUMENTS with all arguments
    all_args = arguments if arguments else ""
    return instructions.replace("$@", all_args).replace("$ARGUMENTS", all_args)


async def _load_reference_content(
    skill: Skill, reference_path: str, pool: Any | None = None
) -> str:
    """Load content from a skill reference file.

    Args:
        skill: The skill instance
        reference_path: Path to the reference file within the skill directory
        pool: Optional AgentPool for accessing MCP provider

    Returns:
        The reference content with a header, or empty string if not found
    """
    from pathlib import PurePosixPath

    from agentpool.skills.exceptions import ReferenceNotFoundError

    # For virtual paths (PurePosixPath like skill:// URIs), use the provider
    if isinstance(skill.skill_path, PurePosixPath) and pool is not None:
        if pool.skill_provider is not None:
            # Always pass the canonical kebab-case skill.name to the aggregating
            # provider, which matches against Skill.name (always kebab-case).
            # The MCP provider's read_reference() internally looks up
            # original_name from its skill cache for URI construction.
            try:
                content_bytes, _ = await pool.skill_provider.read_reference(
                    skill.name, reference_path
                )
                content = content_bytes.decode("utf-8")
            except Exception as e:
                raise ReferenceNotFoundError(f"Reference not found: {reference_path}") from e
            else:
                return f"\n\n## Reference: {reference_path}\n\n{content}"
        raise ReferenceNotFoundError(
            f"Cannot load reference {reference_path}: no skill provider available"
        )

    # For filesystem paths (UPath), load from disk
    # This branch should only be reached for actual filesystem paths (UPath),
    # not virtual paths (PurePosixPath like skill:// URIs)
    if type(skill.skill_path) is PurePosixPath:
        raise ReferenceNotFoundError(
            f"Cannot load reference {reference_path}: virtual paths require a skill provider"
        )

    # After the check above, skill_path is definitely a UPath
    from upathtools import UPath

    skill_path = cast(UPath, skill.skill_path)

    # Validate reference_path to prevent path traversal attacks
    from agentpool.skills.exceptions import SecurityError

    decoded_path = reference_path
    # Check for path traversal attempts and absolute paths
    if ".." in decoded_path.split("/") or decoded_path.startswith("/"):
        raise SecurityError(f"Path traversal detected in reference path: {reference_path}")

    ref_file = skill_path / reference_path
    # Resolve and verify the path is within the skill directory
    try:
        resolved_path = ref_file.resolve()
        resolved_skill_path = skill_path.resolve()
        if not str(resolved_path).startswith(str(resolved_skill_path)):
            raise SecurityError(f"Reference path escapes skill directory: {reference_path}")
    except (OSError, ValueError) as e:
        raise ReferenceNotFoundError(f"Invalid reference path: {reference_path}") from e

    if not ref_file.exists():
        raise ReferenceNotFoundError(str(ref_file))

    content = ref_file.read_text(encoding="utf-8")
    return f"\n\n## Reference: {reference_path}\n\n{content}"


async def load_skill(  # noqa: PLR0911
    ctx: AgentContext,
    skill_name: str,
    arguments: str | None = None,
) -> str:
    """Load a Claude Code Skill and return its instructions.

    Args:
        ctx: Agent context providing access to pool and skills
        skill_name: Name of the skill to load, or a skill:// URI.
            Use skill:// URI to load a specific reference file:
            skill://provider/skill-name/references/file.md
        arguments: Optional space-separated arguments for substitution

    Returns:
        The full skill instructions for execution
    """
    if ctx.pool is None:
        return "No agent pool available - skills require pool context"

    # Determine if this is a URI or bare skill name
    is_uri = skill_name.startswith("skill://")

    try:
        resolved = ResolvedSkillURI.parse(skill_name)
    except Exception as e:  # noqa: BLE001
        return f"Invalid skill name or URI {skill_name!r}: {e}"

    if is_uri:
        # URI-based loading via skill_resolver
        resolver: SkillURIResolver | None = getattr(ctx.pool, "skill_resolver", None)
        if resolver is None:
            return "Skill URI resolution not available - skill_resolver not configured"

        try:
            skill = await resolver.resolve(skill_name)
        except Exception as e:  # noqa: BLE001
            return f"Failed to resolve skill URI {skill_name!r}: {e}"

        # Check for reference path first
        # When a reference file is explicitly requested via URI, load ONLY the
        # reference content — not the main SKILL.md instructions.
        # Check for fallback reference path from provider-less URI resolution
        ref_path = resolved.reference_path or getattr(skill, "_resolved_reference_path", None)

        if ref_path:
            # Reference-only loading: skip main SKILL.md content
            try:
                ref_content = await _load_reference_content(skill, ref_path, pool=ctx.pool)
                instructions = ref_content
            except Exception as e:  # noqa: BLE001
                return f"Failed to load reference {ref_path!r}: {e}"
        # Full skill loading: get main instructions
        # For virtual paths (PurePosixPath), fetch from provider
        elif isinstance(skill.skill_path, PurePosixPath):
            if ctx.pool.skill_provider is not None:
                try:
                    instructions = await ctx.pool.skill_provider.get_skill_instructions(
                        skill.name
                    )
                except Exception as e:  # noqa: BLE001
                    return f"Failed to load skill instructions for {skill.name!r}: {e}"
            else:
                instructions = ""
        else:
            instructions = skill.load_instructions()
    else:
        # Bare skill name - use skill_resolver to search across all providers
        # This supports dynamic skill discovery from MCP servers with proper priority
        resolver: SkillURIResolver | None = getattr(ctx.pool, "skill_resolver", None)
        if resolver is not None:
            try:
                # Try to resolve via skill_resolver using bare skill name
                # (searches all providers in priority order)
                skill = await resolver.resolve(resolved.skill_name)
                # For virtual paths (PurePosixPath), fetch from provider
                if isinstance(skill.skill_path, PurePosixPath):
                    if ctx.pool.skill_provider is not None:
                        try:
                            instructions = await ctx.pool.skill_provider.get_skill_instructions(
                                skill.name
                            )
                        except Exception as e:  # noqa: BLE001
                            return f"Failed to load skill instructions for {skill.name!r}: {e}"
                    else:
                        instructions = ""
                else:
                    instructions = skill.load_instructions()
            except Exception:
                # Fallback: check local skills directly
                skills = ctx.pool.skills.list_skills()
                visible_skills = [
                    s for s in skills if not getattr(s, "disable_model_invocation", False)
                ]
                found_skill: Skill | None = next(
                    (s for s in visible_skills if s.name == resolved.skill_name), None
                )
                if found_skill is None:
                    available = ", ".join(s.name for s in visible_skills)
                    return f"Skill {resolved.skill_name!r} not found. Available skills: {available}"
                skill = found_skill
                try:
                    instructions = ctx.pool.skills.get_skill_instructions(resolved.skill_name)
                except Exception as e:  # noqa: BLE001
                    return f"Failed to load skill {resolved.skill_name!r}: {e}"
        else:
            # Fallback when no resolver available - check local skills only
            skills = ctx.pool.skills.list_skills()
            visible_skills = [
                s for s in skills if not getattr(s, "disable_model_invocation", False)
            ]
            found_skill = next((s for s in visible_skills if s.name == resolved.skill_name), None)
            if found_skill is None:
                available = ", ".join(s.name for s in visible_skills)
                return f"Skill {resolved.skill_name!r} not found. Available skills: {available}"
            skill = found_skill
            try:
                instructions = ctx.pool.skills.get_skill_instructions(resolved.skill_name)
            except Exception as e:  # noqa: BLE001
                return f"Failed to load skill {resolved.skill_name!r}: {e}"

    # Apply argument substitution
    instructions = _substitute_arguments(instructions, arguments)

    # Determine if this is a reference-only load
    effective_ref_path = (resolved.reference_path if is_uri else None) or getattr(
        skill, "_resolved_reference_path", None
    )
    is_reference_load = is_uri and effective_ref_path is not None

    # Build the response
    if is_reference_load:
        # Reference-only: minimal header indicating source skill and reference file
        header = f"# {skill.name} → Reference: {effective_ref_path}"
        parts = [header]
        parts.append(instructions)
        parts.append(f"Skill directory: {skill.skill_path}")
        if resolved.provider:
            parts.append(
                f"URI: skill://{resolved.provider}/{resolved.skill_name}/{effective_ref_path}"
            )
    else:
        # Full skill load: include description, metadata, and instructions
        header = f"# {skill.name}\n\n{skill.description}"
        meta_lines: list[str] = []
        if skill.license:
            meta_lines.append(f"License: {skill.license}")
        if skill.compatibility:
            meta_lines.append(f"Compatibility: {skill.compatibility}")
        if skill.allowed_tools:
            meta_lines.append(f"Allowed tools: {skill.allowed_tools}")
        meta = "\n".join(meta_lines)
        parts = [header]
        if meta:
            parts.append(meta)
        parts.append(instructions)
        parts.append(f"Skill directory: {skill.skill_path}")

        # Add URI information if loaded via URI
        if is_uri and resolved.provider:
            parts.append(f"URI: skill://{resolved.provider}/{resolved.skill_name}")

    return "\n\n".join(parts)


async def list_skills(ctx: AgentContext) -> str:
    """List all available skills.

    Returns:
        Formatted list of available skills with descriptions and URI information
    """
    if ctx.pool is None:
        return "No agent pool available - skills require pool context"

    # Get skills from both local registry and MCP provider
    skills = ctx.pool.skills.list_skills()
    # Filter out skills that disable model invocation (for model visibility)
    visible_skills = [s for s in skills if not getattr(s, "disable_model_invocation", False)]

    # Also get skills from skill_provider (MCP-based skills)
    provider_skills: list[Skill] = []
    if ctx.pool.skill_provider is not None:
        with contextlib.suppress(Exception):
            provider_skills = await ctx.pool.skill_provider.get_skills()

    all_skills = visible_skills + provider_skills

    if not all_skills:
        return "No skills available"

    lines = ["Available skills:", ""]

    # Check if skill_resolver is available for URI info
    resolver: SkillURIResolver | None = getattr(ctx.pool, "skill_resolver", None)
    has_resolver = resolver is not None

    for skill in all_skills:
        lines.append(f"- **{skill.name}**: {skill.description}")

        # Add URI information if resolver is available
        if has_resolver and resolver is not None:
            # Try to find which provider this skill belongs to
            for provider_name in resolver.list_providers():
                provider = resolver.get_provider(provider_name)
                if provider:
                    provider_skills = await provider.get_skills()
                    if any(s.name == skill.name for s in provider_skills):
                        lines.append(f"  - URI: `skill://{provider_name}/{skill.name}`")
                        break
            else:
                # Skill found but not in any registered provider
                lines.append("  - URI: Not resolvable via URI")
        else:
            lines.append(f'  - Usage: `load_skill(ctx, "{skill.name}")`')

    # Add usage guidance
    lines.append("")
    lines.append("## Usage")
    lines.append("")
    lines.append("Load a skill by name (backward compatible):")
    lines.append("```python")
    lines.append('await load_skill(ctx, "skill-name")')
    lines.append("```")
    lines.append("")

    if has_resolver:
        lines.append("Or use a skill:// URI:")
        lines.append("```python")
        lines.append('await load_skill(ctx, "skill://provider/skill-name")')
        lines.append("```")
        lines.append("")
        lines.append("With arguments for substitution:")
        lines.append("```python")
        lines.append('await load_skill(ctx, "skill://provider/skill-name", "arg1 arg2")')
        lines.append("```")

    return "\n".join(lines)


class SkillsTools(StaticResourceProvider):
    """Provider for skills and commands tools.

    Provides tools to:
    - Discover and load skills from the pool's skills registry
    - Execute internal commands via the agent's command system

    Skills are discovered from configured directories (e.g., ~/.claude/skills/,
    .claude/skills/).

    Commands provide access to management operations like creating agents,
    managing tools, connecting nodes, etc. Use run_command("/help") to discover
    available commands.
    """

    def __init__(
        self,
        name: str = "skills",
        *,
        injection_mode: Literal["off", "metadata", "full"] | None = None,
        max_skills: int | None = None,
    ) -> None:
        """Initialize the SkillsTools provider.

        Args:
            name: Provider name for resource identification
            injection_mode: Skill injection mode for agent-specific overrides:
                - "off": No skill injection
                - "metadata": Inject skill metadata only
                - "full": Inject full skill instructions
                Defaults to None (use global/default settings)
            max_skills: Maximum number of skills to inject. Defaults to None (no limit)
        """
        super().__init__(name=name)
        self.injection_mode = injection_mode
        self.max_skills = max_skills
        self._tools = [
            self.create_tool(load_skill, category="read", read_only=True, idempotent=True),
            self.create_tool(list_skills, category="read", read_only=True, idempotent=True),
        ]
