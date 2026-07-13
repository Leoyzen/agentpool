"""SkillManagerCap — unified skill management as a pydantic-ai capability.

Replaces the deleted per-skill ``SkillCapability`` class and
:class:`~agentpool.capabilities.skill_activation.SkillActivationCapability`
with a single capability that:

- Holds local skills as ``dict[str, Skill]`` (no per-skill capability wrappers).
- Queries child :class:`~agentpool.capabilities.mcp_server_cap.McpServerCap`
  instances for remote skills and commands.
- Provides metadata-only instructions by default (``<available-skills>`` XML).
- Supports optional ``matcher_fn`` for dynamic per-turn skill injection.
- Supports ``always_active`` flag for skills that bypass the matcher.
- Aggregates ``SkillResource`` and ``CommandResource`` from local + remote.
- Imports per-skill Python tools via :class:`~agentpool.skills.skill_tool_manager.SkillToolManager`.
- Creates per-skill :class:`~agentpool.capabilities.mcp_server_cap.McpServerCap` instances.
- Applies ``allowed_tools`` filtering via ``get_wrapper_toolset()``.
- Inherits change stream merging and lifecycle from
  :class:`~agentpool.capabilities.combined_toolset.CombinedToolsetCapability`.
"""

from __future__ import annotations

import html
import inspect
from typing import TYPE_CHECKING, Any

from pydantic_ai.tools import AgentDepsT, RunContext, ToolDefinition
from pydantic_ai.toolsets import (
    AbstractToolset,
    CombinedToolset,
    FilteredToolset,
    FunctionToolset,
    PrefixedToolset,
)

from agentpool.capabilities.combined_toolset import CombinedToolsetCapability
from agentpool.capabilities.memory import _inject_into_system_prompt
from agentpool.capabilities.resource_protocols import (
    ChangeObservable,
    CommandEntry,
    CommandResource,
    SkillEntry,
    SkillResource,
)
from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import TracebackType

    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.messages import ModelRequestContext

    from agentpool.capabilities.mcp_server_cap import McpServerCap
    from agentpool.skills.skill import Skill
    from agentpool.skills.skill_tool_manager import SkillToolManager
    from agentpool.tools.base import Tool


logger = get_logger(__name__)


class SkillManagerCap(
    CombinedToolsetCapability[AgentDepsT],
    SkillResource,
    CommandResource,
    ChangeObservable,
):
    """Unified skill management capability.

    Holds local skills directly as ``dict[str, Skill]`` and queries child
    ``McpServerCap`` instances for remote skills/commands. Provides
    metadata-only instructions by default, with optional ``matcher_fn``
    for dynamic per-turn injection.

    Attributes:
        _local_skills: Local skills keyed by name.
        _children: Child ``McpServerCap`` instances for remote access.
        _matcher_fn: Optional callable for skill selection.
        _always_active: Set of skill names that always inject.
    """

    def __init__(
        self,
        local_skills: dict[str, Skill] | None = None,
        children: list[AbstractCapability[AgentDepsT]] | None = None,
        *,
        matcher_fn: Callable[..., list[str]] | None = None,
        always_active: set[str] | None = None,
        registry: Any | None = None,
        name: str | None = None,
        tool_manager: SkillToolManager | None = None,
    ) -> None:
        """Initialize the skill manager capability.

        Args:
            local_skills: Local skills keyed by name. Defaults to empty.
            children: Child ``McpServerCap`` instances for remote skills/commands.
            matcher_fn: Optional async or sync callable that receives the
                conversation context and returns a list of skill names to
                inject. When ``None``, all skills are injected (backward compat).
            always_active: Set of skill names that always have their instructions
                injected, bypassing the matcher.
            registry: Optional ``SkillsRegistry`` reference for hot-reload.
            name: Optional name override.
            tool_manager: Optional ``SkillToolManager`` for importing Python tools
                declared in skill frontmatter. When provided, tools are imported
                eagerly at construction time.
        """
        self._local_skills: dict[str, Skill] = dict(local_skills) if local_skills else {}
        self._children: list[AbstractCapability[AgentDepsT]] = list(children) if children else []
        self._matcher_fn = matcher_fn
        self._always_active: set[str] = set(always_active) if always_active else set()
        self._registry = registry
        self._tool_manager: SkillToolManager | None = tool_manager

        # Per-skill Python tools: {skill_name: [Tool, ...]}
        self._skill_tools: dict[str, list[Tool]] = {}
        # Per-skill McpServerCap children: {skill_name: [McpServerCap, ...]}
        self._skill_mcp_children: dict[str, list[McpServerCap]] = {}

        # Import Python tools eagerly (D2).
        if self._tool_manager is not None:
            self._import_skill_tools()

        # Create per-skill McpServerCap instances (D3).
        self._create_skill_mcp_children()

        # Build the full children list: original children + skill MCP children.
        all_children: list[AbstractCapability[AgentDepsT]] = list(self._children)
        for caps in self._skill_mcp_children.values():
            all_children.extend(caps)  # type: ignore[arg-type]  # McpServerCap[None] vs AgentDepsT

        # Initialize CombinedToolsetCapability with all child capabilities.
        super().__init__(all_children, name=name or "skill-manager")

    # ---- Properties ----

    @property
    def local_skills(self) -> dict[str, Skill]:
        """Return the local skills dict."""
        return self._local_skills

    @property
    def children(self) -> list[AbstractCapability[AgentDepsT]]:
        """Return the child capability list."""
        return list(self._children)

    def add_child(self, child: AbstractCapability[AgentDepsT]) -> None:
        """Add a child capability at runtime.

        Args:
            child: The capability to add.
        """
        self._children.append(child)
        self._capabilities.append(child)

    def add_local_skill(self, skill: Skill) -> None:
        """Add a local skill.

        Args:
            skill: The Skill to add.
        """
        self._local_skills[skill.name] = skill

    # ---- Per-skill tool import (D2) ----

    def _import_skill_tools(self) -> None:
        """Import Python tools from all local skills with ``tools`` frontmatter.

        Iterates ``local_skills``, calls ``tool_manager.import_tools(skill.tools)``
        for skills with non-empty ``tools``, and stores results in
        ``_skill_tools`` keyed by skill name.
        """
        if self._tool_manager is None:
            return
        for name, skill in self._local_skills.items():
            if not skill.tools:
                continue
            try:
                imported = self._tool_manager.import_tools(skill.tools)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to import tools for skill %r", name, exc_info=True)
                continue
            if imported:
                self._skill_tools[name] = imported

    # ---- Per-skill MCP children (D3) ----

    def _create_skill_mcp_children(self) -> None:
        """Create ``McpServerCap`` instances for skills with ``mcp_servers`` frontmatter.

        For each skill with non-empty ``mcp_servers``, converts each
        ``SkillMcpServerConfig`` to a ``MCPServerConfig`` via
        ``to_mcp_server_config()``, creates a ``McpServerCap``, and stores
        it in ``_skill_mcp_children[skill_name]``.
        """
        from agentpool.capabilities.mcp_server_cap import McpServerCap

        for name, skill in self._local_skills.items():
            if not skill.mcp_servers:
                continue
            caps: list[McpServerCap] = []
            for server_name, server_config in skill.mcp_servers.items():
                try:
                    mcp_config = server_config.to_mcp_server_config(f"{name}__{server_name}")
                except (ValueError, TypeError):
                    logger.warning(
                        "Failed to convert MCP server config %r for skill %r",
                        server_name,
                        name,
                        exc_info=True,
                    )
                    continue
                try:
                    cap = McpServerCap(config=mcp_config)
                    caps.append(cap)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Failed to create McpServerCap for skill %r server %r",
                        name,
                        server_name,
                        exc_info=True,
                    )
            if caps:
                self._skill_mcp_children[name] = caps

    # ---- get_toolset() full override (D2) ----

    def get_toolset(self) -> AbstractToolset[AgentDepsT] | None:
        """Return a unified toolset with per-skill prefixed tools.

        Fully overrides :meth:`CombinedToolsetCapability.get_toolset` — does
        NOT call ``super().get_toolset()``. Only skill-specific tools are
        provided; pool-level MCP server tools are handled by
        :meth:`MCPManager.get_capabilities` to avoid duplicate registration.

        1. Creates ``PrefixedToolset("{skill_name}__tool__")`` for each skill
           with imported Python tools.
        2. Creates ``PrefixedToolset("{skill_name}__mcp__")`` for each
           per-skill ``McpServerCap`` child.
        3. Combines all into a ``CombinedToolset``.
        """
        toolsets: list[AbstractToolset[AgentDepsT]] = []

        # 1. Python tools: PrefixedToolset per skill.
        for skill_name, tools in self._skill_tools.items():
            pa_tools: list[Any] = [t.to_pydantic_ai() for t in tools]
            if pa_tools:
                toolsets.append(
                    PrefixedToolset(
                        wrapped=FunctionToolset(pa_tools),
                        prefix=f"{skill_name}__tool__",
                    )
                )

        # 2. Per-skill McpServerCap children: PrefixedToolset per skill.
        for skill_name, child_caps in self._skill_mcp_children.items():
            for child in child_caps:
                child_ts = child.get_toolset()
                if child_ts is not None:
                    toolsets.append(
                        PrefixedToolset(
                            wrapped=child_ts,
                            prefix=f"{skill_name}__mcp__",
                        )
                    )

        if not toolsets:
            return None
        return CombinedToolset(toolsets=toolsets)

    # ---- get_wrapper_toolset() override (D4) ----

    def get_wrapper_toolset(
        self,
        toolset: AbstractToolset[AgentDepsT],
    ) -> AbstractToolset[AgentDepsT] | None:
        """Apply composite ``allowed_tools`` filtering across all skills.

        Builds a per-skill filter map from ``parsed_allowed_tools()``. If
        any skill has non-empty ``allowed_tools``, wraps the entire agent
        toolset in a ``FilteredToolset`` with a composite filter function.

        Filter semantics:
        - Non-skill tools (no ``{skill_name}__`` prefix) always pass.
        - Skill tools are checked against that skill's allowed set after
          stripping the prefix.
        """
        skill_filters: dict[str, set[str]] = {}
        for name, skill in self._local_skills.items():
            allowed = skill.parsed_allowed_tools()
            if allowed is not None:
                skill_filters[name] = set(allowed)

        if not skill_filters:
            return None

        def _filter(
            ctx: RunContext[AgentDepsT],
            tool_def: ToolDefinition,
        ) -> bool:
            tool_name = tool_def.name
            for skill_name, allowed_set in skill_filters.items():
                for category in ("tool", "mcp"):
                    prefix = f"{skill_name}__{category}__"
                    if tool_name.startswith(prefix):
                        bare = tool_name[len(prefix) :]
                        return bare in allowed_set
            return True  # Non-skill tools always pass.

        return FilteredToolset(wrapped=toolset, filter_func=_filter)

    # ---- AbstractCapability: instructions ----

    def get_instructions(self) -> str | None:
        """Return metadata-only ``<available-skills>`` XML block.

        Implements progressive disclosure: metadata at compilation,
        full instructions on demand via ``before_model_request``.

        Returns:
            XML string with skill names and descriptions, or ``None``.
        """
        if not self._local_skills:
            return None
        lines = ["<available-skills>"]
        for name, skill in self._local_skills.items():
            if skill.disable_model_invocation:
                continue
            desc = html.escape(skill.description)
            lines.append(f'<skill name="{html.escape(name)}" description="{desc}" />')
        lines.append("</available-skills>")
        return "\n".join(lines)

    # ---- AbstractCapability: before_model_request ----

    async def before_model_request(
        self,
        ctx: RunContext[AgentDepsT],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        """Inject full instructions for relevant skills.

        When ``matcher_fn`` is set, calls it to select 2-3 relevant skills.
        When ``matcher_fn`` is ``None``, injects all skills (backward compat).
        Skills in ``_always_active`` bypass the matcher.

        Args:
            ctx: The pydantic-ai run context.
            request_context: The model request context with messages.

        Returns:
            The (possibly modified) request context.
        """
        if not self._local_skills:
            return request_context

        messages = request_context.messages

        # Determine which skills to inject.
        if self._matcher_fn is not None:
            sig = inspect.signature(self._matcher_fn)
            if len(sig.parameters) >= 2:  # noqa: PLR2004
                result = self._matcher_fn(messages, list(self._local_skills.keys()))
            else:
                result = self._matcher_fn(messages)
            if inspect.isawaitable(result):
                result = await result
            matched: set[str] = {n for n in result if n in self._local_skills}
        else:
            # Backward compat: inject all skills.
            matched = set(self._local_skills.keys())

        # Always add always_active skills.
        matched |= self._always_active & set(self._local_skills.keys())

        if not matched:
            return request_context

        # Build injection text.
        parts: list[str] = []
        for name in sorted(matched):
            skill = self._local_skills[name]
            try:
                instructions = skill.load_instructions()
            except (ValueError, OSError):
                logger.warning("Failed to load instructions for skill %r", name)
                continue
            if instructions:
                escaped_name = html.escape(name)
                parts.append(
                    f'<skill_content name="{escaped_name}">\n{instructions}\n</skill_content>'
                )

        if not parts:
            return request_context

        injected = "\n\n".join(parts)
        _inject_into_system_prompt(messages, injected)
        return request_context

    @property
    def has_wrap_node_run(self) -> bool:
        """Return False — no node run wrapping needed."""
        return False

    async def for_run(
        self,
        ctx: RunContext[AgentDepsT],
    ) -> SkillManagerCap[AgentDepsT]:
        """Create a per-run copy of this capability.

        Calls ``for_run()`` on each child capability so children are
        also per-run isolated.

        Args:
            ctx: The pydantic-ai run context.

        Returns:
            A new ``SkillManagerCap`` sharing the same skills but with
            per-run copies of children.
        """
        children_for_run = [await child.for_run(ctx) for child in self._children]
        cap = SkillManagerCap(
            local_skills=self._local_skills,
            children=children_for_run,
            matcher_fn=self._matcher_fn,
            always_active=self._always_active,
            registry=self._registry,
            name=self._name,
            tool_manager=self._tool_manager,
        )
        return cap  # noqa: RET504

    # ---- SkillResource ----

    async def list_skills(self) -> Sequence[SkillEntry]:
        """List all available skills (local + remote).

        Returns:
            Sequence of ``SkillEntry`` descriptors.
        """
        entries: list[SkillEntry] = []

        # Local skills.
        for name, skill in self._local_skills.items():
            entries.append(
                SkillEntry(
                    name=name,
                    description=skill.description,
                    uri=f"skill://{name}",
                    source="local",
                    skill_path=skill.skill_path,
                )
            )

        # Remote skills from child McpServerCap instances.
        for child in self._children:
            if isinstance(child, SkillResource):
                try:
                    remote_skills = await child.list_skills()
                    entries.extend(remote_skills)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Failed to list skills from child %r",
                        child.get_serialization_name(),
                        exc_info=True,
                    )

        return entries

    async def read_skill(self, name: str) -> str | None:
        """Read skill content by name.

        Local skills take precedence over remote.

        Args:
            name: Skill name to read.

        Returns:
            Skill content as string, or ``None`` if not found.
        """
        # Local first.
        if name in self._local_skills:
            try:
                return self._local_skills[name].load_instructions()
            except (ValueError, OSError):
                return None

        # Remote.
        for child in self._children:
            if isinstance(child, SkillResource):
                try:
                    content = await child.read_skill(name)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Failed to read skill %r from child %r",
                        name,
                        child.get_serialization_name(),
                        exc_info=True,
                    )
                    continue
                if content is not None:
                    return content

        return None

    async def skill_exists(self, name: str) -> bool:
        """Check if a skill exists (local or remote).

        Args:
            name: Skill name to check.

        Returns:
            ``True`` if the skill exists, ``False`` otherwise.
        """
        # Local.
        if name in self._local_skills:
            return True

        # Remote.
        for child in self._children:
            if isinstance(child, SkillResource):
                try:
                    if await child.skill_exists(name):
                        return True
                except Exception:  # noqa: BLE001
                    continue

        return False

    # ---- CommandResource ----

    async def list_commands(self) -> Sequence[CommandEntry]:
        """List all available commands (local + remote).

        Each local skill becomes a ``CommandEntry``. Remote commands come
        from child ``McpServerCap`` instances implementing ``CommandResource``.

        Returns:
            Sequence of ``CommandEntry`` descriptors.
        """
        entries: list[CommandEntry] = []

        # Local skills as commands.
        for name, skill in self._local_skills.items():
            if not skill.user_invocable:
                continue
            entries.append(
                CommandEntry(
                    name=name,
                    description=skill.description,
                    skill_uri=f"skill://{name}",
                    source="local",
                )
            )

        # Remote commands from child McpServerCap instances.
        for child in self._children:
            if isinstance(child, CommandResource):
                try:
                    remote_commands = await child.list_commands()
                    entries.extend(remote_commands)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Failed to list commands from child %r",
                        child.get_serialization_name(),
                        exc_info=True,
                    )

        return entries

    async def get_command(self, name: str) -> CommandEntry | None:
        """Get a specific command by name.

        Local skills take precedence over remote.

        Args:
            name: Command name to retrieve.

        Returns:
            ``CommandEntry`` if found, ``None`` otherwise.
        """
        # Local first.
        if name in self._local_skills:
            skill = self._local_skills[name]
            if skill.user_invocable:
                return CommandEntry(
                    name=name,
                    description=skill.description,
                    skill_uri=f"skill://{name}",
                    source="local",
                )

        # Remote.
        for child in self._children:
            if isinstance(child, CommandResource):
                try:
                    entry = await child.get_command(name)
                except Exception:  # noqa: BLE001
                    continue
                if entry is not None:
                    return entry

        return None

    # ---- Lifecycle ----

    async def __aenter__(self) -> SkillManagerCap[AgentDepsT]:
        """Enter async context for all children."""
        await super().__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context for all children."""
        await super().__aexit__(exc_type, exc_val, exc_tb)
