"""Provider for skills and commands tools."""

from __future__ import annotations

import contextlib
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.capabilities.function_toolset import FunctionToolsetCapability
from agentpool.capabilities.resource_protocols import SkillResource
from agentpool.skills.skill import Skill
from agentpool.skills.skill_tool_manager import SkillToolManager
from agentpool.skills.uri_resolver import ResolvedSkillURI


if TYPE_CHECKING:
    from agentpool.skills.uri_resolver import SkillURIResolver


SKILL_USAGE_GUIDANCE = """
## Skill URI Format

Skills can be loaded using either a bare skill name or a skill:// URI:

### Bare Skill Name (Backward Compatible)
- `python-expert` - Load skill by name (searches all providers)

### URI Format
- `skill://skill-name` - Load skill by flat URI
- `skill://skill-name/references/file.md` - Load with reference file

### Argument Substitution
When providing arguments, the following substitutions are made:
- `$1`, `$2`, ... - Replaced with the Nth argument
- `$@` - Replaced with all arguments
- `$ARGUMENTS` - Replaced with all arguments

Example: `load_skill(ctx, "skill://python-expert", "arg1 arg2")`
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

    # For virtual paths (PurePosixPath like skill:// URIs), use the provider.
    # Use exact type check (not isinstance) to avoid catching UPath subclasses.
    # UPath is a subclass of PurePosixPath; isinstance would match filesystem skills too,
    # routing them through the provider which hardcodes references/ prefix.
    if type(skill.skill_path) is PurePosixPath and pool is not None:
        if pool.skill_provider is not None:
            # Always pass the canonical kebab-case skill.name to the aggregating
            # provider, which matches against Skill.name (always kebab-case).
            # The MCP provider's read_reference() internally looks up
            # original_name from its skill cache for URI construction.
            try:
                content_bytes, _ = await pool.skill_provider.read_reference(
                    skill.name, reference_path
                )
            except Exception as e:
                raise ReferenceNotFoundError(f"Reference not found: {reference_path}") from e
            else:
                content = content_bytes.decode("utf-8")
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


def _node_name_for_scope(ctx: AgentContext, node_name: str | None = None) -> str | None:
    if node_name is not None:
        return node_name
    node = getattr(ctx, "node", None)
    return getattr(node, "name", None)


def _is_skill_visible_to_node(ctx: AgentContext, skill: Skill, node_name: str | None) -> bool:
    checker = getattr(ctx.pool, "is_skill_visible_to_node", None)
    if not callable(checker):
        return True
    return bool(checker(skill, node_name))


def _visible_model_skills(
    ctx: AgentContext,
    skills: list[Skill],
    node_name: str | None,
) -> list[Skill]:
    return [
        skill
        for skill in skills
        if not getattr(skill, "disable_model_invocation", False)
        and _is_skill_visible_to_node(ctx, skill, node_name)
    ]


async def _load_visible_bare_skill(
    ctx: AgentContext,
    skill_name: str,
    node_name: str | None,
) -> tuple[Skill, str] | None:
    if ctx.pool is None:
        return None
    local_skills = _visible_model_skills(ctx, ctx.pool.skills.list_skills(), node_name)
    local_skill = next((skill for skill in local_skills if skill.name == skill_name), None)
    if local_skill is not None:
        return local_skill, ctx.pool.skills.get_skill_instructions(skill_name)

    skill_provider = getattr(ctx.pool, "skill_provider", None)
    if skill_provider is None:
        return None

    # Use capabilities property (CombinedToolsetCapability) instead of old providers
    capabilities = getattr(skill_provider, "capabilities", None)
    if capabilities is None:
        return None
    for provider in capabilities:
        # Use SkillResource protocol check instead of getattr duck-typing.
        if not isinstance(provider, SkillResource):
            continue
        try:
            provider_entries = await provider.list_skills()
        except Exception:  # noqa: BLE001
            continue
        # Map SkillEntry objects to Skill instances and apply visibility
        # filtering before selecting the matching skill (defense-in-depth).
        provider_skills = [
            Skill(
                name=entry.name,
                description=entry.description,
                skill_path=PurePosixPath(entry.uri),
                instructions="",
            )
            for entry in provider_entries
        ]
        visible_skills = _visible_model_skills(ctx, provider_skills, node_name)
        matching_skill = next(
            (s for s in visible_skills if s.name == skill_name),
            None,
        )
        if matching_skill is not None:
            try:
                instructions = await provider.read_skill(matching_skill.name)
            except Exception:  # noqa: BLE001
                instructions = None
            if instructions is None:
                instructions = ""
            matching_skill.instructions = instructions
            return matching_skill, instructions

    return None


async def load_skill_for_node(
    ctx: AgentContext,
    skill_name: str,
    node_name: str,
    arguments: str | None = None,
) -> str:
    """Load a skill using a target node's package-level skill scope."""
    return await _load_skill(ctx, skill_name, arguments, node_name=node_name)


async def load_skill(
    ctx: AgentContext,
    skill_name: str,
    arguments: str | None = None,
) -> str:
    """Load a Claude Code Skill and return its instructions.

    Args:
        ctx: Agent context providing access to pool and skills
        skill_name: Name of the skill to load, or a skill:// URI.
            Use skill:// URI to load a specific reference file:
            skill://skill-name/references/file.md
        arguments: Optional space-separated arguments for substitution

    Returns:
        The full skill instructions for execution
    """
    return await _load_skill(ctx, skill_name, arguments)


async def _load_skill(  # noqa: PLR0911, PLR0915
    ctx: AgentContext,
    skill_name: str,
    arguments: str | None = None,
    *,
    node_name: str | None = None,
) -> str:
    if ctx.pool is None:
        return "No agent pool available - skills require pool context"

    requested_node_name = _node_name_for_scope(ctx, node_name)

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
        if not _is_skill_visible_to_node(ctx, skill, requested_node_name):
            available = await _available_skill_names(ctx, requested_node_name)
            return f"Skill {resolved.skill_name!r} not found. Available skills: {available}"

        # Check for reference path first
        # When a reference file is explicitly requested via URI, load ONLY the
        # reference content — not the main SKILL.md instructions.
        # Check for fallback reference path from provider-less URI resolution.
        # Priority: _resolved_reference_path first (resolver's fallback correction
        # for provider-less URIs like skill://skill-name/path), then parsed path.
        # The resolver's fallback correctly reconstructs the full reference path
        # when URI parsing misidentifies the skill name as a provider.
        ref_path = getattr(skill, "_resolved_reference_path", None) or resolved.reference_path

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
                    # Try get_skill_instructions on the skill_provider if available
                    get_instructions_fn = getattr(
                        ctx.pool.skill_provider, "get_skill_instructions", None
                    )
                    if get_instructions_fn is not None:
                        instructions = await get_instructions_fn(skill.name)
                    else:
                        instructions = ""
                except Exception as e:  # noqa: BLE001
                    return f"Failed to load skill instructions for {skill.name!r}: {e}"
            else:
                instructions = ""
        else:
            instructions = skill.load_instructions()
    else:
        try:
            loaded = await _load_visible_bare_skill(ctx, resolved.skill_name, requested_node_name)
        except Exception as e:  # noqa: BLE001
            return f"Failed to load skill {resolved.skill_name!r}: {e}"
        if loaded is None:
            available = await _available_skill_names(ctx, requested_node_name)
            return f"Skill {resolved.skill_name!r} not found. Available skills: {available}"
        skill, instructions = loaded

    # Apply argument substitution
    instructions = _substitute_arguments(instructions, arguments)

    # Activate MCP servers and tools declared in the skill
    mcp_lines: list[str] = []
    tool_lines: list[str] = []

    if skill.mcp_servers:
        # MCP server preparation is now handled by SkillManagerCap.
        for server_name, config in skill.mcp_servers.items():
            server_desc = config.command or config.url or "configured"
            mcp_lines.append(f"- `{server_name}`: {server_desc}")

    if skill.tools:
        tool_manager = SkillToolManager()
        for tool_config in skill.tools:
            result = tool_manager.import_tool(tool_config)
            status = "✓" if result is not None else "✗"
            tool_lines.append(f"- `{tool_config.import_path}` ({status})")

    # Determine if this is a reference-only load
    # Priority: _resolved_reference_path first (resolver's fallback correction
    # for provider-less URIs), then parsed path.
    effective_ref_path = getattr(skill, "_resolved_reference_path", None) or (
        resolved.reference_path if is_uri else None
    )
    is_reference_load = is_uri and effective_ref_path is not None

    # Build the response
    if is_reference_load:
        # Reference-only: minimal header indicating source skill and reference file
        header = f"# {skill.name} → Reference: {effective_ref_path}"
        parts = [header]
        parts.append(instructions)
        parts.append(f"Skill URI: {skill.safe_uri}")
    else:
        # Full skill load: include description, metadata, and instructions
        header = f"# {skill.name}\n\n{skill.description}"
        meta_lines: list[str] = []
        if skill.license:
            meta_lines.append(f"License: {skill.license}")
        if skill.compatibility:
            meta_lines.append(f"Compatibility: {skill.compatibility}")
        meta = "\n".join(meta_lines)
        parts = [header]
        if meta:
            parts.append(meta)
        parts.append(instructions)
        parts.append(f"Skill URI: {skill.safe_uri}")

    # Append activated MCP servers section
    if mcp_lines:
        parts.append("## Activated MCP Servers\n" + "\n".join(mcp_lines))

    # Append activated tools section
    if tool_lines:
        parts.append("## Activated Tools\n" + "\n".join(tool_lines))

    return "\n\n".join(parts)


async def _available_skill_names(ctx: AgentContext, node_name: str | None) -> str:
    if ctx.pool is None:
        return ""
    skills = ctx.pool.skills.list_skills()
    visible_skills = _visible_model_skills(ctx, skills, node_name)

    provider_skills: list[Skill] = []
    if ctx.pool.skill_provider is not None:
        try:
            # Iterate child capabilities and use SkillResource protocol check.
            capabilities = getattr(ctx.pool.skill_provider, "capabilities", [])
            for cap in capabilities:
                if not isinstance(cap, SkillResource):
                    continue
                with contextlib.suppress(Exception):
                    entries = await cap.list_skills()
                    # Map SkillEntry objects to Skill instances for
                    # downstream compatibility with _visible_model_skills.
                    provider_skills.extend(
                        Skill(
                            name=entry.name,
                            description=entry.description,
                            skill_path=PurePosixPath(entry.uri),
                            instructions="",
                        )
                        for entry in entries
                    )
        except Exception:  # noqa: BLE001
            provider_skills = []

    all_skills = {
        skill.name
        for skill in [*visible_skills, *_visible_model_skills(ctx, provider_skills, node_name)]
    }
    return ", ".join(sorted(all_skills))


async def list_skills(ctx: AgentContext) -> str:  # noqa: PLR0915
    """List all available skills.

    Returns:
        Formatted list of available skills with descriptions and URI information
    """
    if ctx.pool is None:
        return "No agent pool available - skills require pool context"

    requested_node_name = _node_name_for_scope(ctx)

    # Get skills from both local registry and MCP provider.
    # Deduplicate by name: local skills take priority (appear first in list).
    skills = ctx.pool.skills.list_skills()
    # Filter out skills that disable model invocation (for model visibility)
    visible_skills = _visible_model_skills(ctx, skills, requested_node_name)

    # Also get skills from skill_provider (MCP-based skills)
    provider_skills: list[Skill] = []
    if ctx.pool.skill_provider is not None:
        with contextlib.suppress(Exception):
            # Iterate child capabilities and use SkillResource protocol check.
            capabilities = getattr(ctx.pool.skill_provider, "capabilities", [])
            for cap in capabilities:
                if not isinstance(cap, SkillResource):
                    continue
                with contextlib.suppress(Exception):
                    entries = await cap.list_skills()
                    provider_skills.extend(
                        Skill(
                            name=entry.name,
                            description=entry.description,
                            skill_path=PurePosixPath(entry.uri),
                            instructions="",
                        )
                        for entry in entries
                    )

    visible_provider_skills = _visible_model_skills(ctx, provider_skills, requested_node_name)
    seen: set[str] = {s.name for s in visible_skills}
    all_skills = list(visible_skills)
    for skill in visible_provider_skills:
        if skill.name not in seen:
            seen.add(skill.name)
            all_skills.append(skill)

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
                if provider and isinstance(provider, SkillResource):
                    try:
                        entries = await provider.list_skills()
                    except Exception:  # noqa: BLE001
                        continue
                    if any(e.name == skill.name for e in entries):
                        lines.append(f"  - URI: `skill://{skill.name}`")
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
        lines.append('await load_skill(ctx, "skill://skill-name")')
        lines.append("```")
        lines.append("")
        lines.append("With arguments for substitution:")
        lines.append("```python")
        lines.append('await load_skill(ctx, "skill://skill-name", "arg1 arg2")')
        lines.append("```")

    return "\n".join(lines)


class SkillsTools(FunctionToolsetCapability):
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
        max_skills: int | None = None,
    ) -> None:
        """Initialize the SkillsTools provider.

        Args:
            name: Provider name for resource identification
            max_skills: Maximum number of skills to inject. Defaults to None (no limit)
        """
        super().__init__(name=name)
        self.max_skills = max_skills
        self._tools = [
            self.create_tool(load_skill, category="read", read_only=True, idempotent=True),
            self.create_tool(list_skills, category="read", read_only=True, idempotent=True),
        ]
