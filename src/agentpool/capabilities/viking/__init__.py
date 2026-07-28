"""VikingCapability — Viking knowledge graph integration for AgentPool.

Provides 15 tools for interacting with a Viking knowledge graph server,
organized into three categories:

- **Retrieve** (7 tools): search, find, recall, grep, glob, ls, read
- **Write** (6 tools): remember, write, edit, mkdir, add_resource, forget
- **Graph** (2 tools): link, set_tags

The capability also implements the ``SkillResource`` protocol, enabling
remote skill discovery and reading from the Viking server.

Configuration is via ``VikingCapabilityConfig`` in
``agentpool_config.capabilities``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AgentToolset, FunctionToolset


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import TracebackType
    from typing import Self

    from pydantic_ai import RunContext

    from agentpool.capabilities.change_event import ChangeEvent
    from agentpool.capabilities.resource_protocols import SkillEntry


@dataclass
class VikingCapability(AbstractCapability[Any]):
    """Capability for interacting with a Viking knowledge graph.

    Provides 15 tools across retrieve, write, and graph categories.
    The SDK client is lazily initialized in ``__aenter__`` and shared
    across per-run copies via ``for_run()``.

    Attributes:
        mode: Tool exposure mode — ``"retrieve"`` (7 tools), ``"write"``
            (6 tools), ``"graph"`` (2 tools), or ``"all"`` (15 tools).
        url: Viking server URL. If ``None``, SDK resolves from
            ``OPENVIKING_URL`` env var or ``~/.openviking/ovcli.conf``.
        api_key: Viking API key. If ``None``, SDK resolves from env vars.
        account: Viking account ID. If ``None``, SDK resolves from env vars.
        user: Viking user ID. If ``None``, SDK resolves from env vars.
        timeout: Request timeout in seconds. If ``None``, SDK uses 60s.
        skills_uri: Override for skills URI.
        resources_uri: Override for resources URI.
        multimodal_bridge: Enable multimodal bridge (not yet implemented).
        uploads_uri: Override for uploads URI.
        public_download_base_url: Base URL for public download links.
    """

    mode: Literal["retrieve", "write", "graph", "all"] = "all"
    url: str | None = None
    api_key: str | None = None
    account: str | None = None
    user: str | None = None
    timeout: float | None = None
    skills_uri: str | None = None
    resources_uri: str | None = None
    multimodal_bridge: bool = False
    uploads_uri: str | None = None
    public_download_base_url: str | None = None
    _client: Any = field(default=None, repr=False)
    _owns_client: bool = field(default=True, repr=False)

    @property
    def has_wrap_node_run(self) -> bool:
        """Return ``False`` — Viking does not wrap node execution."""
        return False

    def _get_client(self) -> Any:
        """Return the SDK client, raising if not initialized.

        Returns:
            The ``AsyncHTTPClient`` instance.

        Raises:
            RuntimeError: If the client has not been initialized
                (``__aenter__`` was not called).
        """
        if self._client is None:
            msg = (
                "VikingCapability client not initialized. "
                "Use 'async with VikingCapability(...) as cap:' "
                "or ensure the capability is entered via the pool lifecycle."
            )
            raise RuntimeError(msg)
        return self._client

    def _resolve_skills_uri(self) -> str:
        """Return the skills URI, using override or default convention.

        Returns:
            The skills URI string (e.g. ``viking://user/alice/skills/``).
        """
        if self.skills_uri is not None:
            return self.skills_uri
        return f"viking://user/{self.user or 'default'}/skills/"

    async def __aenter__(self) -> Self:
        """Initialize the Viking SDK client.

        Lazily imports ``AsyncHTTPClient`` from ``openviking_sdk`` and
        creates a client with the configured fields. If the client is
        already set (e.g. a ``for_run`` copy sharing the parent's client),
        this is a no-op.

        Returns:
            ``self`` with the client initialized.
        """
        if self._client is not None:
            return self

        from openviking_sdk import AsyncHTTPClient

        self._client = AsyncHTTPClient(
            url=self.url,
            api_key=self.api_key,
            account=self.account,
            user=self.user,
            timeout=self.timeout,
        )
        await self._client.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the SDK client if this instance owns it.

        Sets ``_client`` to ``None`` regardless of ownership to prevent
        use-after-close errors on shared clients.
        """
        if self._owns_client and self._client is not None:
            await self._client.close()
        self._client = None

    async def for_run(self, ctx: RunContext[Any]) -> VikingCapability:
        """Create a per-run copy that shares the parent's client.

        The returned copy has ``_owns_client=False`` so it will not
        close the shared client on ``__aexit__``.

        Args:
            ctx: The pydantic-ai run context (unused but required by
                the ``AbstractCapability`` interface).

        Returns:
            A new ``VikingCapability`` sharing the same client.
        """
        return VikingCapability(
            mode=self.mode,
            url=self.url,
            api_key=self.api_key,
            account=self.account,
            user=self.user,
            timeout=self.timeout,
            skills_uri=self.skills_uri,
            resources_uri=self.resources_uri,
            multimodal_bridge=self.multimodal_bridge,
            uploads_uri=self.uploads_uri,
            public_download_base_url=self.public_download_base_url,
            _client=self._client,
            _owns_client=False,
        )

    def get_instructions(self) -> str | None:
        """Return the Viking workflow instructions.

        Returns:
            The instruction string from ``instructions.py``.
        """
        from agentpool.capabilities.viking.instructions import _VIKING_INSTRUCTIONS

        return _VIKING_INSTRUCTIONS

    def get_toolset(self) -> AgentToolset[Any] | None:
        """Build a ``FunctionToolset`` from tools filtered by ``self.mode``.

        Returns ``None`` if no tools are available for the current mode.

        Returns:
            A ``FunctionToolset`` with the mode-appropriate tools, or
            ``None`` if the tool list is empty.
        """
        from agentpool.capabilities.viking.tools import build_tools

        tool_fns = build_tools(self)
        if not tool_fns:
            return None
        return FunctionToolset(tool_fns, id="viking")

    def on_change(self) -> AsyncIterator[ChangeEvent] | None:
        """Return ``None`` — Viking tools never change at runtime."""
        return None

    # ---- SkillResource Protocol ----

    async def list_skills(self) -> list[SkillEntry]:
        """List available skills from the Viking server.

        Calls ``client.ls(skills_uri)`` and filters for ``.md`` files.

        Returns:
            A list of ``SkillEntry`` descriptors with ``source="remote"``.
            Returns an empty list on error.
        """
        try:
            client = self._get_client()
            uri = self._resolve_skills_uri()
            entries = await client.ls(uri)
            if not isinstance(entries, list):
                return []

            from agentpool.capabilities.resource_protocols import SkillEntry

            skills: list[SkillEntry] = []
            for entry in entries:
                name: str
                if isinstance(entry, dict):
                    name = entry.get("name", entry.get("uri", ""))
                else:
                    name = str(entry)
                if name.endswith(".md"):
                    skill_name = name[:-3]  # strip .md
                    skills.append(
                        SkillEntry(
                            name=skill_name,
                            uri=f"{uri}{name}",
                            source="remote",
                            skill_path=None,
                        )
                    )
            return skills
        except Exception:
            return []

    async def read_skill(self, name: str) -> str | None:
        """Read a skill's content from the Viking server.

        Args:
            name: Skill name (without ``.md`` extension).

        Returns:
            Skill content as a string, or ``None`` if not found or on error.
        """
        try:
            client = self._get_client()
            uri = self._resolve_skills_uri()
            content = await client.read(f"{uri}{name}.md")
            return str(content) if content else None
        except Exception:
            return None

    async def skill_exists(self, name: str) -> bool:
        """Check if a skill exists on the Viking server.

        Args:
            name: Skill name (without ``.md`` extension).

        Returns:
            ``True`` if the skill exists, ``False`` otherwise or on error.
        """
        try:
            client = self._get_client()
            uri = self._resolve_skills_uri()
            entries = await client.ls(uri)
            if not isinstance(entries, list):
                return False
            target = f"{name}.md"
            for entry in entries:
                entry_name: str
                if isinstance(entry, dict):
                    entry_name = entry.get("name", entry.get("uri", ""))
                else:
                    entry_name = str(entry)
                if entry_name == target:
                    return True
            return False
        except Exception:
            return False
