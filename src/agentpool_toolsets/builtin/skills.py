"""Skills tools — backward-compatible re-exports.

The standalone ``SkillsTools`` toolset has been consolidated into
:class:`~agentpool.capabilities.skill_manager_cap.SkillManagerCap`
(unify-skill-loading change). The helper functions are now owned by
``skill_manager_cap.py`` and re-exported here for backward compatibility
with tests and external code that imports them directly.

The ``load_skill`` and ``list_skills`` module-level functions are kept as
thin wrappers that delegate to the cap's implementation, using the
``AgentContext`` interface (not ``RunContext``) for backward compat.
"""

from __future__ import annotations

import contextlib
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.capabilities.resource_protocols import SkillResource
from agentpool.capabilities.skill_manager_cap import (
    _is_skill_visible_to_node,
    _load_reference_content,
    _substitute_arguments,
    _visible_model_skills,
)
from agentpool.skills.skill import Skill
from agentpool.skills.skill_tool_manager import SkillToolManager
from agentpool.skills.uri_resolver import ResolvedSkillURI, _name_alternatives


if TYPE_CHECKING:
    from agentpool.skills.uri_resolver import SkillURIResolver


__all__ = [
    "_is_skill_visible_to_node",
    "_load_reference_content",
    "_substitute_arguments",
    "_visible_model_skills",
    "list_skills",
    "load_skill",
    "load_skill_for_node",
]


# ---------------------------------------------------------------------------
# Backward-compatible module-level functions (take AgentContext, not RunContext)
# ---------------------------------------------------------------------------


def _get_skill_cap(ctx: AgentContext) -> Any | None:
    """Get the SkillManagerCap from the pool's skill_capabilities."""
    if ctx.pool is None:
        return None
    caps = ctx.pool.skill_capabilities
    if caps:
        return caps[0]
    return None


async def load_skill(  # noqa: PLR0911, PLR0915
    ctx: AgentContext,
    skill_name: str,
    arguments: str | None = None,
    *,
    node_name: str | None = None,
) -> str:
    """Load a Claude Code Skill and return its instructions.

    Backward-compatible wrapper that delegates to the SkillManagerCap.

    Args:
        ctx: Agent context providing access to pool and skills
        skill_name: Name of the skill to load, or a skill:// URI.
        arguments: Optional space-separated arguments for substitution
        node_name: Optional node name for package-scoped skill visibility.

    Returns:
        The full skill instructions for execution
    """
    if ctx.pool is None:
        return "No agent pool available - skills require pool context"

    cap = _get_skill_cap(ctx)
    if cap is None:
        return "SkillManagerCap not available"

    # Determine if this is a URI or bare skill name
    is_uri = skill_name.startswith("skill://")

    try:
        resolved = ResolvedSkillURI.parse(skill_name)
    except Exception as e:  # noqa: BLE001
        return f"Invalid skill name or URI {skill_name!r}: {e}"

    if is_uri:
        resolver: SkillURIResolver | None = ctx.pool.skill_resolver
        if resolver is None:
            return "Skill URI resolution not available - skill_resolver not configured"

        try:
            skill = await resolver.resolve(skill_name)
        except Exception as e:  # noqa: BLE001
            return f"Failed to resolve skill URI {skill_name!r}: {e}"
        if not _is_skill_visible_to_node(ctx.pool, skill, node_name):
            available = await _available_skill_names(ctx)
            return f"Skill {resolved.skill_name!r} not found. Available skills: {available}"

        ref_path = skill.resolved_reference_path or resolved.reference_path

        if ref_path:
            try:
                instructions = await _load_reference_content(skill, ref_path, pool=ctx.pool)
            except Exception as e:  # noqa: BLE001
                return f"Failed to load reference {ref_path!r}: {e}"
        else:
            instructions = skill.instructions or ""
    else:
        loaded = await _load_visible_bare_skill(ctx, resolved.skill_name, node_name)
        if loaded is None:
            available = await _available_skill_names(ctx)
            return f"Skill {resolved.skill_name!r} not found. Available skills: {available}"
        skill, instructions = loaded

    # Apply argument substitution
    instructions = _substitute_arguments(instructions, arguments)

    # Activate MCP servers and tools
    mcp_lines: list[str] = []
    tool_lines: list[str] = []

    if skill.mcp_servers:
        for server_name, config in skill.mcp_servers.items():
            server_desc = config.command or config.url or "configured"
            mcp_lines.append(f"- `{server_name}`: {server_desc}")

    if skill.tools:
        tool_manager = SkillToolManager()
        for tool_config in skill.tools:
            result = tool_manager.import_tool(tool_config)
            status = "✓" if result is not None else "✗"
            tool_lines.append(f"- `{tool_config.import_path}` ({status})")

    effective_ref_path = skill.resolved_reference_path or (
        resolved.reference_path if is_uri else None
    )
    is_reference_load = is_uri and effective_ref_path is not None

    if is_reference_load:
        header = f"# {skill.name} → Reference: {effective_ref_path}"
        parts: list[str] = [header, instructions, f"Skill URI: {skill.safe_uri}"]
    else:
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

    if mcp_lines:
        parts.append("## Activated MCP Servers\n" + "\n".join(mcp_lines))
    if tool_lines:
        parts.append("## Activated Tools\n" + "\n".join(tool_lines))

    return "\n\n".join(parts)


async def load_skill_for_node(
    ctx: AgentContext,
    skill_name: str,
    node_name: str,
    arguments: str | None = None,
) -> str:
    """Load a skill using a target node's package-level skill scope."""
    return await load_skill(ctx, skill_name, arguments, node_name=node_name)


async def list_skills(ctx: AgentContext) -> str:
    """List all available skills.

    Backward-compatible wrapper that delegates to the SkillManagerCap.

    Returns:
        Formatted list of available skills with descriptions and URI information
    """
    if ctx.pool is None:
        return "No agent pool available - skills require pool context"

    cap = _get_skill_cap(ctx)
    if cap is None:
        return "No skills available"

    # Use the cap's local skills and children
    local_skill_list = list(cap.local_skills.values())
    visible_local = _visible_model_skills(ctx.pool, local_skill_list, None)

    # Remote skills from children
    remote_skills: list[Skill] = []
    for child in cap.children:
        if not isinstance(child, SkillResource):
            continue
        with contextlib.suppress(Exception):
            entries = await child.list_skills()
            remote_skills.extend(
                Skill(
                    name=entry.name,
                    description=entry.description,
                    skill_path=PurePosixPath(entry.uri),
                    instructions="",
                )
                for entry in entries
            )

    visible_remote = _visible_model_skills(ctx.pool, remote_skills, None)

    seen: set[str] = {s.name for s in visible_local}
    all_skills = list(visible_local)
    for skill in visible_remote:
        if skill.name not in seen:
            seen.add(skill.name)
            all_skills.append(skill)

    if not all_skills:
        return "No skills available"

    lines = ["Available skills:", ""]
    for skill in all_skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
        lines.append(f"  - URI: `skill://{skill.name}`")

    lines.append("")
    lines.append("## Usage")
    lines.append("")
    lines.append("Load a skill by name (backward compatible):")
    lines.append("```python")
    lines.append('await load_skill(ctx, "skill-name")')
    lines.append("```")

    return "\n".join(lines)


async def _load_visible_bare_skill(
    ctx: AgentContext,
    skill_name: str,
    node_name: str | None = None,
) -> tuple[Skill, str] | None:
    """Load a bare skill name from the cap's local skills or children.

    Args:
        ctx: Agent context with pool access.
        skill_name: The bare skill name to load.
        node_name: Optional node name for package-scoped visibility.

    Returns:
        Tuple of (Skill, instructions) if found, ``None`` otherwise.
    """
    cap = _get_skill_cap(ctx)
    if cap is None:
        return None

    # Local skills first
    local_skill = cap.local_skills.get(skill_name)
    if local_skill is None:
        for alt_name in _name_alternatives(skill_name):
            local_skill = cap.local_skills.get(alt_name)
            if local_skill is not None:
                break

    if local_skill is not None and _is_skill_visible_to_node(ctx.pool, local_skill, node_name):
        try:
            instructions = local_skill.load_instructions()
        except (ValueError, OSError):
            instructions = ""
        return local_skill, instructions
    # Local skill found but not visible — fall through to check remote.

    # Remote skills from children
    for child in cap.children:
        if not isinstance(child, SkillResource):
            continue
        try:
            provider_entries = await child.list_skills()
        except Exception:  # noqa: BLE001
            continue
        provider_skills = [
            Skill(
                name=entry.name,
                description=entry.description,
                skill_path=PurePosixPath(entry.uri),
                instructions="",
            )
            for entry in provider_entries
        ]
        visible_skills = _visible_model_skills(ctx.pool, provider_skills, node_name)
        matching_skill = next(
            (s for s in visible_skills if s.name == skill_name),
            None,
        )
        if matching_skill is None:
            for alt_name in _name_alternatives(skill_name):
                matching_skill = next(
                    (s for s in visible_skills if s.name == alt_name),
                    None,
                )
                if matching_skill is not None:
                    break
        if matching_skill is not None:
            try:
                instructions = await child.read_skill(matching_skill.name)
            except Exception:  # noqa: BLE001
                instructions = None
            if instructions is None:
                instructions = ""
            matching_skill.instructions = instructions
            return matching_skill, instructions

    return None


async def _available_skill_names(ctx: AgentContext) -> str:
    """Return a comma-separated list of available skill names."""
    cap = _get_skill_cap(ctx)
    if cap is None:
        return ""

    local_skill_list = list(cap.local_skills.values())
    visible_local = _visible_model_skills(ctx.pool, local_skill_list, None)

    remote_skills: list[Skill] = []
    for child in cap.children:
        if not isinstance(child, SkillResource):
            continue
        with contextlib.suppress(Exception):
            entries = await child.list_skills()
            remote_skills.extend(
                Skill(
                    name=entry.name,
                    description=entry.description,
                    skill_path=PurePosixPath(entry.uri),
                    instructions="",
                )
                for entry in entries
            )

    visible_remote = _visible_model_skills(ctx.pool, remote_skills, None)
    all_names = {skill.name for skill in [*visible_local, *visible_remote]}
    return ", ".join(sorted(all_names))
