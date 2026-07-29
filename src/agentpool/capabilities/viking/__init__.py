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

import asyncio
import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AgentToolset, FunctionToolset


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType
    from typing import Self

    from pydantic_ai import RunContext
    from pydantic_ai.messages import BinaryContent
    from pydantic_ai.models import ModelRequestContext

    from agentpool.capabilities.change_event import ChangeEvent
    from agentpool.capabilities.resource_protocols import (
        BlobResourceContent,
        ResourceEntry,
        SkillEntry,
        TextResourceContent,
    )
    from agentpool_config.model_capabilities import ModelCapabilities


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
    sessions_uri: str | None = None
    """Override for sessions URI. Default: ``viking://user/{user}/sessions/``.
    When set, ``list_resources()`` includes files from this URI tree in
    addition to ``resources_uri``."""
    multimodal_bridge: bool = False
    """Enable multimodal bridge — auto-upload binary content to Viking
    before sending to the model."""
    uploads_uri: str | None = None
    public_download_base_url: str | None = None
    enable_link: bool = False
    """Enable the ``viking_link`` tool. Requires backend support for
    the graph link API. Disabled by default since not all Viking
    deployments support linking."""
    enable_memory: bool = False
    """Enable ``viking_remember`` and ``viking_recall`` tools. Requires
    backend support for session-based memory. Disabled by default
    since not all Viking deployments support memory sessions."""
    resource_file_extensions: tuple[str, ...] = (
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".html",
    )
    """File extensions to include in ``list_resources()``. Files with
    extensions not in this set are skipped. Set to an empty tuple to
    include all files regardless of extension."""
    resource_read_level: Literal["abstract", "overview", "read"] = "overview"
    """Default content level for ``read_resource()`` (ResourceAccess Protocol).
    ``"abstract"`` (L0, ~100 tokens), ``"overview"`` (L1, ~2k tokens, default),
    or ``"read"`` (L2, full content). When ``read_resource()`` is called (e.g.
    via @ mention in OpenCode), this controls how much content is returned.
    Falls back to L2 if the requested level is unavailable."""
    model_capabilities: ModelCapabilities | None = None
    """Resolved model capabilities for multimodal bridge. Set by the
    agent factory after capability construction."""
    _client: Any = field(default=None, repr=False)
    _owns_client: bool = field(default=True, repr=False)

    @property
    def has_wrap_node_run(self) -> bool:
        """Return ``False`` — Viking does not wrap node execution."""
        return False

    async def _ensure_client(self) -> Any:
        """Return the SDK client, lazily initializing if needed.

        Follows the same pattern as ``McpServerCap._ensure_client()``:
        if the client is already set (e.g. from ``__aenter__`` or a
        ``for_run`` copy), return it directly. Otherwise, lazily import
        and initialize the SDK client.

        Returns:
            The ``AsyncHTTPClient`` instance.
        """
        if self._client is not None:
            return self._client

        from openviking_sdk import AsyncHTTPClient

        self._client = AsyncHTTPClient(
            url=self.url,
            api_key=self.api_key,
            account=self.account,
            user=self.user,
            timeout=self.timeout,
        )
        await self._client.initialize()
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
            enable_link=self.enable_link,
            enable_memory=self.enable_memory,
            resource_file_extensions=self.resource_file_extensions,
            resource_read_level=self.resource_read_level,
            model_capabilities=self.model_capabilities,
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

    async def get_tools(self) -> Sequence[Any]:
        """Return tools as ``Tool`` objects for listing endpoints.

        This is required by ``_get_all_tools()`` in ``base_agent.py``,
        which uses the ``_ToolProviding`` Protocol (``get_tools()``).
        Without this, Viking tools won't appear in the OpenCode
        ``/experimental/tool`` endpoint.

        Returns:
            A list of ``FunctionTool`` objects wrapping the tool closures.
        """
        from agentpool.capabilities.viking.tools import build_tools
        from agentpool.tools.base import FunctionTool

        tool_fns = build_tools(self)
        return [FunctionTool.from_callable(fn) for fn in tool_fns]

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
            client = await self._ensure_client()
            uri = self._resolve_skills_uri()
            entries = await client.ls(uri)
            if not isinstance(entries, list):
                return []

            from agentpool.capabilities.resource_protocols import SkillEntry

            skills: list[SkillEntry] = []
            for entry in entries:
                name: str
                if isinstance(entry, dict):
                    name = str(entry.get("name") or entry.get("uri") or "")
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
            client = await self._ensure_client()
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
            client = await self._ensure_client()
            uri = self._resolve_skills_uri()
            entries = await client.ls(uri)
            if not isinstance(entries, list):
                return False
            target = f"{name}.md"
            for entry in entries:
                entry_name: str
                if isinstance(entry, dict):
                    entry_name = str(entry.get("name") or entry.get("uri") or "")
                else:
                    entry_name = str(entry)
                if entry_name == target:
                    return True
            return False
        except Exception:
            return False

    # ---- ResourceAccess Protocol (Phase 5) ----

    def _resolve_resources_uri(self) -> str:
        """Return the resources URI, using override or default convention.

        Returns:
            The resources URI string (e.g. ``viking://resources/``).
        """
        if self.resources_uri is not None:
            return self.resources_uri
        return "viking://resources/"

    def _resolve_sessions_uri(self) -> str:
        """Return the sessions URI, using override or default convention.

        Returns:
            The sessions URI string (e.g.
            ``viking://user/{user}/sessions/``).
        """
        if self.sessions_uri is not None:
            return self.sessions_uri
        return f"viking://user/{self.user or 'default'}/sessions/"

    async def _list_resource_entries_from_uri(
        self, client: Any, uri: str
    ) -> list[ResourceEntry]:
        """Recursively list files under a single Viking URI.

        Performs a per-directory recursive ``client.ls()`` to work around
        Viking's incomplete root-level recursive traversal, then builds
        ``ResourceEntry`` objects for each file (filtering by configured
        extensions and inferring MIME types).

        Args:
            client: The Viking SDK client.
            uri: The base URI to list (e.g. ``viking://resources/``).

        Returns:
            A list of ``ResourceEntry`` descriptors for text files.
        """
        from agentpool.capabilities.resource_protocols import ResourceEntry

        top_entries = await client.ls(uri)
        if not isinstance(top_entries, list):
            return []

        all_entries: list[dict[str, Any]] = []
        for entry in top_entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("isDir"):
                sub_uri = str(entry.get("uri") or "")
                if sub_uri:
                    sub_entries = await client.ls(sub_uri, recursive=True, node_limit=5000)
                    if isinstance(sub_entries, list):
                        all_entries.extend(e for e in sub_entries if isinstance(e, dict))
            else:
                all_entries.append(entry)

        resources: list[ResourceEntry] = []
        for entry in all_entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("isDir"):
                continue
            resource_uri = str(entry.get("uri") or "")
            if not resource_uri:
                continue
            name = str(entry.get("name") or resource_uri.rsplit("/", 1)[-1] or resource_uri)
            # Filter by configured extensions; skip files not in the set
            lowered = name.lower()
            if self.resource_file_extensions and not lowered.endswith(
                self.resource_file_extensions
            ):
                continue
            # Infer MIME type from extension
            mime_type = ""
            if lowered.endswith(".md"):
                mime_type = "text/markdown"
            elif lowered.endswith(".txt"):
                mime_type = "text/plain"
            elif lowered.endswith(".json"):
                mime_type = "application/json"
            elif lowered.endswith((".yaml", ".yml")):
                mime_type = "text/yaml"
            elif lowered.endswith(".html"):
                mime_type = "text/html"
            resources.append(
                ResourceEntry(
                    uri=resource_uri,
                    name=name,
                    description=resource_uri.removeprefix(uri),
                    mime_type=mime_type,
                )
            )
        return resources

    async def list_resources(self) -> Sequence[ResourceEntry]:
        """List Viking resources from multiple URI trees.

        Lists files from both ``resources_uri`` (shared resources) and
        ``sessions_uri`` (user session content), merges and deduplicates
        by URI, then enriches descriptions with L0 abstracts.

        Files are what users @ mention — directories can't be read as
        content.

        Returns:
            A sequence of ``ResourceEntry`` descriptors for text files.
            Returns an empty list on error.
        """
        try:
            client = await self._ensure_client()
            uris = [self._resolve_resources_uri(), self._resolve_sessions_uri()]

            # List from each URI tree in parallel
            results = await asyncio.gather(
                *[self._list_resource_entries_from_uri(client, u) for u in uris],
                return_exceptions=True,
            )

            # Merge results, deduplicate by URI
            seen_uris: set[str] = set()
            resources: list[ResourceEntry] = []

            for result in results:
                if not isinstance(result, list):
                    continue
                for entry in result:
                    if entry.uri not in seen_uris:
                        seen_uris.add(entry.uri)
                        resources.append(entry)

            # Enrich with L0 abstracts — batch-call client.abstract() for each
            # resource. If abstracts fail, keep the path-based description.
            if resources:

                async def _safe_abstract(client: Any, r_uri: str) -> str:
                    try:
                        return str(await client.abstract(r_uri) or "")
                    except Exception:
                        return ""

                abstracts = await asyncio.gather(
                    *[_safe_abstract(client, r.uri) for r in resources],
                    return_exceptions=True,
                )
                for i, ab in enumerate(abstracts):
                    if isinstance(ab, str) and ab.strip():
                        resources[i] = replace(resources[i], description=ab.strip())

            return resources
        except Exception:
            return []

    async def read_resource(
        self, uri: str
    ) -> list[TextResourceContent | BlobResourceContent] | None:
        """Read a Viking resource by URI.

        Uses the configured ``resource_read_level`` to determine content
        depth (L0 abstract, L1 overview, or L2 full content). Falls back
        to L2 (``client.read``) if the requested level is unavailable.

        Args:
            uri: The Viking URI of the resource to read.

        Returns:
            A list containing a ``TextResourceContent`` with the resource
            content, or ``None`` if not found or on error.
        """
        try:
            client = await self._ensure_client()
            # Use configured read level (L0/L1/L2), fallback to L2 if unavailable
            content: str | None = None
            if self.resource_read_level == "abstract":
                try:
                    content = await client.abstract(uri)
                except Exception:
                    content = await client.read(uri)  # fallback to L2
            elif self.resource_read_level == "overview":
                try:
                    content = await client.overview(uri)
                except Exception:
                    content = await client.read(uri)  # fallback to L2
            else:
                content = await client.read(uri)

            if not content:
                return None

            from agentpool.capabilities.resource_protocols import (
                TextResourceContent,
            )

            return [
                TextResourceContent(
                    uri=uri,
                    mime_type="text/markdown" if uri.endswith(".md") else None,
                    text=str(content),
                )
            ]
        except Exception:
            return None

    async def resource_exists(self, uri: str) -> bool:
        """Check if a Viking resource exists.

        Args:
            uri: The Viking URI of the resource to check.

        Returns:
            ``True`` if the resource exists, ``False`` otherwise or on error.
        """
        try:
            client = await self._ensure_client()
            parent = uri.rsplit("/", 1)[0] + "/"
            name = uri.rsplit("/", 1)[1]
            entries = await client.ls(parent)
            if not isinstance(entries, list):
                return False
            for entry in entries:
                entry_name = str(entry.get("name") or "") if isinstance(entry, dict) else str(entry)
                if entry_name == name:
                    return True
            return False
        except Exception:
            return False

    # ---- Multimodal Bridge (Phase 6) ----

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        """Intercept binary content before sending to the model.

        Uploads binary content to Viking, then replaces it based on
        model capabilities:
        - Text-only model → text reference with ``viking://`` URI
        - Multimodal + ``public_download_base_url`` → HTTP URL
        - Multimodal + no URL → keep original (but persisted in Viking)

        Args:
            ctx: The pydantic-ai run context.
            request_context: The model request context containing messages.

        Returns:
            The (possibly modified) model request context.
        """
        if not self.multimodal_bridge or self._client is None:
            return request_context

        from dataclasses import replace

        from pydantic_ai.messages import (
            BinaryContent,
            ModelRequest,
            TextPart,
            UserPromptPart,
        )

        new_messages: list[Any] = []
        modified = False
        for msg in request_context.messages:
            if not isinstance(msg, ModelRequest):
                new_messages.append(msg)
                continue

            new_parts: list[Any] = []
            msg_modified = False
            for part in msg.parts:
                if not isinstance(part, UserPromptPart):
                    new_parts.append(part)
                    continue

                content = part.content
                if not isinstance(content, list):
                    new_parts.append(part)
                    continue

                new_content: list[Any] = []
                for item in content:
                    if not isinstance(item, BinaryContent):
                        new_content.append(item)
                        continue

                    viking_uri = await self._upload_binary(item)
                    if viking_uri is None:
                        new_content.append(item)
                        continue

                    supports = self._supports_modality(item.media_type)
                    if not supports:
                        new_content.append(
                            TextPart(
                                content=(
                                    f"[Content stored at {viking_uri}. Use viking_read to access.]"
                                ),
                            )
                        )
                        msg_modified = True
                    elif self.public_download_base_url:
                        http_url = f"{self.public_download_base_url}?uri={viking_uri}"
                        new_content.append(TextPart(content=http_url))
                        msg_modified = True
                    else:
                        new_content.append(item)

                if msg_modified:
                    new_parts.append(replace(part, content=new_content))
                    modified = True
                else:
                    new_parts.append(part)

            if msg_modified:
                new_messages.append(replace(msg, parts=new_parts))
            else:
                new_messages.append(msg)

        if not modified:
            return request_context
        return replace(request_context, messages=new_messages)

    def _supports_modality(self, media_type: str) -> bool:
        """Check if the model supports the given media type.

        Dispatches on ``media_type`` prefix to the appropriate
        ``ModelCapabilities`` field.

        Args:
            media_type: The MIME type of the content (e.g. ``"image/png"``).

        Returns:
            ``True`` if the model supports this modality, ``False`` otherwise.
        """
        caps = self.model_capabilities
        if caps is None:
            return False
        if media_type.startswith("image/"):
            return bool(caps.image_input)
        if media_type.startswith("audio/"):
            return bool(caps.audio_input)
        if media_type.startswith("video/"):
            return bool(caps.video_input)
        if media_type in (
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ):
            return bool(caps.document_input)
        return False

    async def _upload_binary(self, content: BinaryContent) -> str | None:
        """Upload binary content to Viking under ``uploads_uri``.

        Generates a unique URI and uploads via ``client.write()`` with
        base64-encoded content.

        Args:
            content: The ``BinaryContent`` to upload.

        Returns:
            The Viking URI of the uploaded content, or ``None`` on failure.
        """
        try:
            client = await self._ensure_client()
            uploads_uri = self.uploads_uri or (
                f"viking://user/{self.user or 'default'}/memories/uploads/"
            )
            # Viking server only allows .md files; store binary as base64
            # text inside a .md container.
            uri = f"{uploads_uri}{uuid.uuid4().hex[:12]}.md"

            # write() accepts text content; encode binary as base64
            import base64

            b64_data = base64.b64encode(content.data).decode("ascii")
            await client.write(uri, b64_data, mode="create")
            return uri
        except Exception:
            return None


def _guess_extension(media_type: str) -> str:
    """Guess a file extension from a media type.

    Args:
        media_type: The MIME type (e.g. ``"image/png"``).

    Returns:
        A file extension string (e.g. ``"png"``).
    """
    ext_map = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/svg+xml": "svg",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "audio/ogg": "ogg",
        "video/mp4": "mp4",
        "video/webm": "webm",
        "application/pdf": "pdf",
    }
    return ext_map.get(media_type, "bin")
