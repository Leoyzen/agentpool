"""Tool management for AgentPool."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Self, assert_never
from urllib.parse import unquote

from agentpool.common_types import MCPServerStatus
from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider
from agentpool.resource_providers.resource_info import ResourceInfo
from agentpool.skills.exceptions import SecurityError, SkillNotFoundError
from agentpool.skills.skill import Skill


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType
    from typing import Literal

    from fastmcp.client.sampling import SamplingHandler
    from mcp.types import ResourceTemplate

    from agentpool.prompts.prompts import MCPClientPrompt
    from agentpool.tools.base import FunctionTool, Tool
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)


class MCPResourceProvider(ResourceProvider):
    """Resource provider for a single MCP server."""

    kind = "mcp"

    def __init__(
        self,
        server: MCPServerConfig | str,
        name: str = "mcp",
        owner: str | None = None,
        source: Literal["pool", "node"] = "node",
        sampling_callback: SamplingHandler[Any, Any] | None = None,
        accessible_roots: list[str] | None = None,
    ) -> None:
        from agentpool.mcp_server import MCPClient
        from agentpool_config.mcp_server import BaseMCPServerConfig

        super().__init__(name, owner=owner)
        self.server = BaseMCPServerConfig.from_string(server) if isinstance(server, str) else server
        self.source = source
        self.exit_stack = AsyncExitStack()

        self._accessible_roots = accessible_roots
        self._sampling_callback = sampling_callback

        self._saved_enabled_states: dict[str, bool] = {}
        self._tools_cache: list[FunctionTool] | None = None
        self._prompts_cache: list[MCPClientPrompt] | None = None
        self._resources_cache: list[ResourceInfo] | None = None
        self._skills_cache: list[Skill] | None = None
        self.client = MCPClient(
            config=self.server,
            sampling_callback=self._sampling_callback,
            accessible_roots=self._accessible_roots,
            tool_change_callback=self._on_tools_changed,
            prompt_change_callback=self._on_prompts_changed,
            resource_change_callback=self._on_resources_changed,
        )

    def __repr__(self) -> str:
        return f"MCPResourceProvider({self.server!r}, source={self.source!r})"

    @property
    def transport_type(self) -> Literal["stdio", "http", "sse", "acp"]:
        """Return the type of connection used by the MCP server."""
        from agentpool_config import (
            AcpMCPServerConfig,
            SSEMCPServerConfig,
            StdioMCPServerConfig,
            StreamableHTTPMCPServerConfig,
        )

        match self.client.config:
            case StdioMCPServerConfig():
                return "stdio"
            case StreamableHTTPMCPServerConfig():
                return "http"
            case SSEMCPServerConfig():
                return "sse"
            case AcpMCPServerConfig():
                return "acp"
            case _ as unreachable:
                assert_never(unreachable)  # ty: ignore[type-assertion-failure]

    async def __aenter__(self) -> Self:
        try:
            await self.exit_stack.enter_async_context(self.client)
        except Exception as e:
            # Clean up in case of error
            await self.__aexit__(type(e), e, e.__traceback__)
            raise RuntimeError("Failed to initialize MCP manager") from e

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            try:
                # Clean up exit stack (which includes MCP clients)
                await self.exit_stack.aclose()
            except RuntimeError as e:
                if "different task" in str(e):
                    # Handle task context mismatch
                    if asyncio.current_task():
                        loop = asyncio.get_running_loop()
                        await loop.create_task(self.exit_stack.aclose())
                else:
                    raise

        except Exception as e:
            msg = "Error during MCP manager cleanup"
            logger.exception(msg, exc_info=e)
            raise RuntimeError(msg) from e

    async def _on_tools_changed(self) -> None:
        """Callback when tools change on the MCP server."""
        logger.info("MCP tool list changed, refreshing provider cache")
        self._saved_enabled_states = {t.name: t.enabled for t in self._tools_cache or []}
        self._tools_cache = None
        # Notify subscribers via signal
        await self.tools_changed.emit(self.create_change_event("tools"))

    async def _on_prompts_changed(self) -> None:
        """Callback when prompts change on the MCP server."""
        logger.info("MCP prompt list changed, refreshing provider cache")
        self._prompts_cache = None
        # Notify subscribers via signal
        await self.prompts_changed.emit(self.create_change_event("prompts"))

    async def _on_resources_changed(self) -> None:
        """Callback when resources change on the MCP server."""
        logger.info("MCP resource list changed, refreshing provider cache")
        self._resources_cache = None
        # Notify subscribers via signal
        await self.resources_changed.emit(self.create_change_event("resources"))

    async def refresh_tools_cache(self) -> None:
        """Refresh the tools cache by fetching from client."""
        all_tools: list[FunctionTool] = []
        try:
            for tool in await self.client.list_tools():
                try:
                    tool_info = self.client.convert_tool(tool)
                    all_tools.append(tool_info)
                except Exception:
                    logger.exception("Failed to create MCP tool", name=tool.name)
                    continue

            # Restore enabled states from saved states
            for tool_info in all_tools:
                if tool_info.name in self._saved_enabled_states:
                    tool_info.enabled = self._saved_enabled_states[tool_info.name]

            self._tools_cache = all_tools
            logger.debug("Refreshed MCP tools cache", num_tools=len(all_tools))
        except Exception:
            logger.exception("Failed to refresh MCP tools cache")
            self._tools_cache = []

    async def get_tools(self) -> Sequence[Tool]:
        """Get cached tools with server name prefix, refreshing if necessary."""
        if self._tools_cache is None:
            await self.refresh_tools_cache()

        tools = self._tools_cache or []
        # Apply server name prefix to all tool names for isolation
        prefix = f"{self.server.client_id}_"
        for tool in tools:
            if not tool.name.startswith(prefix):
                tool.name = prefix + tool.name
        return tools

    async def refresh_prompts_cache(self) -> None:
        """Refresh the prompts cache by fetching from client."""
        from agentpool.prompts.prompts import MCPClientPrompt

        all_prompts: list[MCPClientPrompt] = []
        try:
            for prompt in await self.client.list_prompts():
                try:
                    converted = MCPClientPrompt.from_fastmcp(self.client, prompt)
                    all_prompts.append(converted)
                except Exception:
                    logger.exception("Failed to convert prompt", name=prompt.name)
                    continue

            self._prompts_cache = all_prompts
            logger.debug("Refreshed MCP prompts cache", num_prompts=len(all_prompts))
        except Exception:
            logger.exception("Failed to refresh MCP prompts cache")
            self._prompts_cache = []

    async def get_prompts(self) -> list[MCPClientPrompt]:  # type: ignore
        """Get cached prompts, refreshing if necessary."""
        if self._prompts_cache is None:
            await self.refresh_prompts_cache()

        return self._prompts_cache or []

    async def refresh_resources_cache(self) -> None:
        """Refresh the resources cache by fetching from client."""
        all_resources: list[ResourceInfo] = []
        try:
            for resource in await self.client.list_resources():
                try:
                    converted = await ResourceInfo.from_mcp_resource(
                        resource,
                        client_name=self.name,
                        reader=self.read_resource,
                    )
                    all_resources.append(converted)
                except Exception:
                    logger.exception("Failed to convert resource", name=resource.name)
                    continue

            self._resources_cache = all_resources
            logger.debug("Refreshed MCP resources cache", num_resources=len(all_resources))
        except Exception:
            logger.exception("Failed to refresh MCP resources cache")
            self._resources_cache = []

    async def get_resources(self) -> list[ResourceInfo]:
        """Get cached resources, refreshing if necessary."""
        if self._resources_cache is None:
            await self.refresh_resources_cache()

        return self._resources_cache or []

    async def read_resource(self, uri: str) -> list[str]:
        """Read resource content by URI.

        Args:
            uri: URI of the resource to read

        Returns:
            List of text contents from the resource

        Raises:
            RuntimeError: If resource cannot be read
        """
        from mcp.types import BlobResourceContents, TextResourceContents

        result: list[str] = []
        for content in await self.client.read_resource(uri):
            match content:
                case TextResourceContents(text=text):
                    result.append(text)
                case BlobResourceContents(blob=blob_data):
                    result.append(f"[Binary data: {len(blob_data)} bytes]")
        return result

    async def list_resource_templates(self) -> list[ResourceTemplate]:
        """Get available resource templates from the MCP server.

        Resource templates define URI patterns with placeholders that can be
        expanded into concrete resource URIs. For example:
        - Template: "file:///{path}" with path="config.json"
        - Expands to: "file:///config.json"

        TODO: Decide on integration strategy:
        - Option 1: Templates as separate concept with expand() -> ResourceInfo
        - Option 2: Unified ResourceInfo with is_template flag and read(**kwargs)
        - Option 3: ResourceTemplateInfo class that produces ResourceInfo

        Returns:
            List of ResourceTemplate objects from the server
        """
        try:
            return await self.client.list_resource_templates()
        except Exception:
            logger.exception("Failed to list resource templates")
            return []

    def get_status(self) -> MCPServerStatus:
        """Get connection status for this MCP server.

        Returns:
            Status dict with 'status' key and optionally 'error' key.
            Status can be: 'connected', 'disabled', or 'failed'.
        """
        try:
            if self.client.connected:
                return MCPServerStatus(
                    name=self.name,
                    status="connected",
                    display_name=self.server.display_name,
                    server_type=self.transport_type,
                )
        except Exception as e:  # noqa: BLE001
            return MCPServerStatus(
                name=self.name,
                status="failed",
                display_name=self.server.display_name,
                error=str(e),
                server_type=self.transport_type,
            )
        else:
            return MCPServerStatus(
                name=self.name,
                status="disabled",
                display_name=self.server.display_name,
                server_type=self.transport_type,
            )

    async def get_skills(self) -> list[Skill]:
        """Get all skills from this MCP provider.

        Combines both prompt-based skills (MCP prompts mapped to skills)
        and resource-based skills (FastMCP Skills Provider via skill:// URI).

        Returns:
            List of Skill objects from both sources
        """
        if self._skills_cache is None:
            # Get both prompt-based and resource-based skills
            prompt_skills = await self._get_prompt_skills()
            resource_skills = await self._get_resource_skills()

            # Combine and deduplicate by name (resource skills take precedence)
            skill_map: dict[str, Skill] = {}
            for skill in prompt_skills:
                skill_map[skill.name] = skill
            for skill in resource_skills:
                skill_map[skill.name] = skill

            self._skills_cache = list(skill_map.values())
            logger.debug(
                "Refreshed MCP skills cache",
                num_skills=len(self._skills_cache),
                prompt_skills=len(prompt_skills),
                resource_skills=len(resource_skills),
            )

        return self._skills_cache

    async def _get_prompt_skills(self) -> list[Skill]:
        """Map MCP prompts to skills with argument_schema metadata.

        Returns:
            List of Skill objects created from MCP prompts
        """
        import json

        skills: list[Skill] = []
        try:
            prompts = await self.get_prompts()
            for prompt in prompts:
                try:
                    # Build argument schema from prompt arguments
                    argument_schema: dict[str, Any] = {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    }

                    if hasattr(prompt, "arguments") and prompt.arguments:
                        for arg in prompt.arguments:
                            arg_name = (
                                arg.get("name", "")
                                if isinstance(arg, dict)
                                else getattr(arg, "name", "")
                            )
                            arg_desc = (
                                arg.get("description", "")
                                if isinstance(arg, dict)
                                else getattr(arg, "description", "")
                            )
                            arg_required = (
                                arg.get("required", False)
                                if isinstance(arg, dict)
                                else getattr(arg, "required", False)
                            )

                            if arg_name:
                                argument_schema["properties"][arg_name] = {
                                    "type": "string",
                                    "description": arg_desc or "",
                                }
                                if arg_required:
                                    argument_schema["required"].append(arg_name)

                    skill = Skill(
                        name=prompt.name or "unknown",
                        description=prompt.description or f"MCP prompt: {prompt.name}",
                        skill_path=PurePosixPath(f"mcp://{self.name}/prompts/{prompt.name}"),
                        metadata={
                            "skill_type": "prompt",
                            "provider": self.name,
                            "argument_schema": json.dumps(argument_schema),
                        },
                    )
                    skills.append(skill)
                except Exception:
                    logger.exception(
                        "Failed to convert prompt to skill", name=getattr(prompt, "name", "unknown")
                    )
                    continue
        except Exception:
            logger.exception("Failed to get prompt-based skills")

        return skills

    async def _get_resource_skills(self) -> list[Skill]:
        """Discover skills via skill:// URI scheme (FastMCP Skills Provider).

        Detects resources matching skill://{name}/SKILL.md pattern.

        Returns:
            List of Skill objects from skill:// resources
        """
        skills: list[Skill] = []
        try:
            resources = await self.get_resources()
            for resource in resources:
                try:
                    # Check if this is a skill:// resource
                    uri = resource.uri
                    if not uri.startswith("skill://"):
                        continue

                    # Parse skill://skill-name/SKILL.md pattern
                    # Format: skill://{skill-name}/SKILL.md or skill://{skill-name}/_manifest
                    uri_path = uri[8:]  # Remove "skill://" prefix
                    parts = uri_path.split("/", 1)
                    if not parts:
                        continue

                    skill_name = parts[0]
                    resource_path = parts[1] if len(parts) > 1 else ""

                    # Only process SKILL.md or _manifest resources
                    if resource_path not in ("SKILL.md", "_manifest"):
                        continue

                    # Preserve original skill name from MCP resource URI.
                    # FastMCP uses directory names as-is for skill identifiers,
                    # which may contain underscores. The Skill model's field_validator
                    # normalizes name to kebab-case (replacing _ with -), so we store
                    # the original name in metadata for constructing read_resource URIs
                    # that match what the MCP server recognizes.
                    main_uri = f"skill://{skill_name}/SKILL.md"
                    description = await self._get_skill_description(skill_name, main_uri)

                    # Use PurePosixPath for skill:// URIs since UPath doesn't support
                    # the skill:// protocol. MCP skills are loaded via the provider's
                    # get_skill_instructions method, not via filesystem access.
                    from pathlib import PurePosixPath

                    skill = Skill(
                        name=skill_name,
                        description=description,
                        skill_path=PurePosixPath(f"skill://{self.name}/{skill_name}"),
                        # Instructions are lazy-loaded via get_skill_instructions
                        # to avoid fetching full content during discovery
                        metadata={
                            "skill_type": "resource",
                            "provider": self.name,
                            "original_name": skill_name,
                            "resource_name": resource.name,
                        },
                    )
                    skills.append(skill)
                except Exception:
                    logger.exception(
                        "Failed to convert resource to skill",
                        uri=getattr(resource, "uri", "unknown"),
                    )
                    continue
        except Exception:
            logger.exception("Failed to get resource-based skills")

        return skills

    async def _get_skill_manifest(self, skill_name: str) -> dict[str, Any] | None:
        """Read _manifest resource for a skill.

        Args:
            skill_name: Name of the skill

        Returns:
            Manifest dict if found, None otherwise
        """
        manifest_uri = f"skill://{skill_name}/_manifest"
        try:
            content = await self.read_resource(manifest_uri)
            if content:
                import yamling

                result = yamling.load_yaml(content[0])
                if isinstance(result, dict):
                    return result
        except Exception:  # noqa: BLE001
            logger.debug("Failed to read skill manifest", skill_name=skill_name)
        return None

    async def _get_skill_description(self, skill_name: str, main_uri: str) -> str:
        """Extract skill description from SKILL.md content.

        Args:
            skill_name: Name of the skill
            main_uri: URI to the SKILL.md resource

        Returns:
            Extracted description or default description
        """
        try:
            content = await self.read_resource(main_uri)
            if content:
                # Parse frontmatter if present
                text = content[0]
                if text.startswith("---"):
                    parts = text.split("---", 2)
                    frontmatter_parts = 3  # ---, frontmatter, body
                    if len(parts) >= frontmatter_parts:
                        import yamling

                        metadata = yamling.load_yaml(parts[1])
                        if isinstance(metadata, dict) and "description" in metadata:
                            desc = metadata["description"]
                            if isinstance(desc, str):
                                return desc
                # Return first non-empty line as description
                for line in text.split("\n"):
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        return stripped[:200]
        except Exception:  # noqa: BLE001
            logger.debug("Failed to extract skill description", skill_name=skill_name)
        return f"MCP skill: {skill_name}"

    async def get_skill_instructions(
        self, skill_name: str, arguments: dict[str, str] | None = None
    ) -> str:
        """Get skill instructions for a specific skill.

        Args:
            skill_name: Name of the skill
            arguments: Optional arguments for prompt-based skills

        Returns:
            Skill instructions as string

        Raises:
            SkillNotFoundError: If skill not found
        """
        # First, find the skill to determine its type
        skills = await self.get_skills()
        skill = next((s for s in skills if s.name == skill_name), None)

        if skill is None:
            available = [s.name for s in skills]
            raise SkillNotFoundError(skill_name, available)

        # Handle based on skill type
        skill_type = skill.metadata.get("skill_type")

        if skill_type == "prompt":
            # Find the corresponding prompt
            prompts = await self.get_prompts()
            prompt = next((p for p in prompts if p.name == skill_name), None)
            if prompt is None:
                raise SkillNotFoundError(skill_name)
            args = arguments or {}
            return await self._get_prompt_skill_instructions(prompt, args)

        if skill_type == "resource":
            original_name = skill.metadata.get("original_name", skill_name)
            return await self._get_resource_skill_instructions(original_name)

        # Unknown skill type
        raise SkillNotFoundError(skill_name)

    async def _get_prompt_skill_instructions(
        self, prompt: MCPClientPrompt, arguments: dict[str, str]
    ) -> str:
        """Render prompt-based skill with arguments.

        Args:
            prompt: The MCP prompt to render
            arguments: Arguments for the prompt

        Returns:
            Rendered prompt content as string
        """
        try:
            components = await prompt.get_components(arguments)
            # Combine all components into a single instruction string
            parts = [
                str(component.content) for component in components if hasattr(component, "content")
            ]
            return "\n\n".join(parts)
        except Exception as e:
            # If arguments are missing, return a template
            if hasattr(prompt, "arguments") and prompt.arguments:
                return self._format_prompt_skill_template(prompt, arguments)
            raise SkillNotFoundError(prompt.name or "unknown") from e

    async def _get_resource_skill_instructions(self, skill_name: str) -> str:
        """Read resource-based skill content.

        Args:
            skill_name: Name of the skill (must match the MCP server's
                resource URI, i.e., the original directory name with
                underscores preserved)

        Returns:
            SKILL.md content as string
        """
        skill_uri = f"skill://{skill_name}/SKILL.md"
        try:
            content = await self.read_resource(skill_uri)
            if content:
                return content[0]
        except Exception as e:
            raise SkillNotFoundError(skill_name) from e
        raise SkillNotFoundError(skill_name)

    def _format_prompt_skill_template(
        self, prompt: MCPClientPrompt, missing_args: dict[str, str]
    ) -> str:
        """Format a template for prompts with required args.

        Args:
            prompt: The MCP prompt
            missing_args: Provided arguments (may be incomplete)

        Returns:
            Formatted template string
        """
        lines: list[str] = []
        lines.append(f"# {prompt.name}")
        lines.append("")
        if prompt.description:
            lines.append(prompt.description)
            lines.append("")

        if hasattr(prompt, "arguments") and prompt.arguments:
            lines.append("## Arguments")
            for arg in prompt.arguments:
                if isinstance(arg, dict):
                    arg_name = arg.get("name", "")
                    arg_desc = arg.get("description", "")
                    arg_required = arg.get("required", False)
                else:
                    arg_name = getattr(arg, "name", "")
                    arg_desc = getattr(arg, "description", "")
                    arg_required = getattr(arg, "required", False)

                provided = missing_args.get(arg_name, "")
                req_marker = " (required)" if arg_required else ""
                lines.append(f"- **{arg_name}**{req_marker}: {arg_desc or 'No description'}")
                if provided:
                    lines.append(f"  - Provided: {provided}")
            lines.append("")

        return "\n".join(lines)

    async def get_references(self, skill_name: str) -> list[dict[str, Any]]:
        """List references for a skill.

        Uses two discovery strategies:
        1. Resource listing: checks listed resources for skill:// URIs with references/
        2. Manifest fallback: reads the skill's _manifest to find reference files
           that aren't listed (e.g., when supporting_files="template")

        Args:
            skill_name: Name of the skill (kebab-case or underscore form).
                The MCP server may list resources using either convention
                depending on the directory name, so both forms are tried.

        Returns:
            List of reference file information
        """
        references: list[dict[str, Any]] = []

        # Strategy 1: Look for resources under skill://skill-name/references/
        # Try both kebab-case (normalized) and underscore (original directory name) forms,
        # since the MCP server uses directory names as-is for skill identifiers.
        try:
            resources = await self.get_resources()
            prefixes = [f"skill://{skill_name}/references/"]
            # Add underscore/kebab alternative
            if "_" in skill_name:
                prefixes.append(f"skill://{skill_name.replace('_', '-')}/references/")
            elif "-" in skill_name:
                prefixes.append(f"skill://{skill_name.replace('-', '_')}/references/")

            for resource in resources:
                for prefix in prefixes:
                    if resource.uri.startswith(prefix):
                        ref_path = resource.uri[len(prefix) :]
                        references.append({
                            "name": resource.name,
                            "path": ref_path,
                            "uri": resource.uri,
                            "description": resource.description,
                        })
                        break  # Avoid duplicates if both prefixes match
        except Exception:
            logger.exception(
                "Failed to get references from resource listing", skill_name=skill_name
            )

        # Strategy 2: Use manifest as fallback when resource listing returns nothing.
        # The _manifest file lists all files in the skill directory, including
        # reference files that aren't exposed as listed resources (e.g., when
        # supporting_files="template" config on the MCP server).
        if not references:
            try:
                # Determine the original_name for manifest lookup.
                # The manifest is stored using the MCP server's directory name (underscores).
                manifest_name = skill_name
                if "-" in skill_name:
                    manifest_name = skill_name.replace("-", "_")

                manifest = await self._get_skill_manifest(manifest_name)
                if manifest and "files" in manifest:
                    for file_info in manifest["files"]:
                        file_path = file_info.get("path", "")
                        if file_path.startswith("references/"):
                            # Use the manifest_name (underscore) for URI construction
                            # since that matches the MCP server's skill identifier
                            references.append({
                                "name": file_path,
                                "path": file_path,
                                "uri": f"skill://{manifest_name}/{file_path}",
                                "description": None,
                            })
            except Exception:
                logger.exception("Failed to get references from manifest", skill_name=skill_name)

        return references

    async def read_reference(self, skill_name: str, ref_path: str) -> tuple[bytes, str]:
        """Read reference content with path traversal protection.

        The skill_name should be the canonical kebab-case name (as stored in
        Skill.name). This method looks up the original_name from its skill cache
        (which preserves the MCP server's directory name with underscores) to
        construct the correct URI for the MCP server.

        Args:
            skill_name: Name of the skill (kebab-case, matches Skill.name)
            ref_path: Path to the reference file (relative to references/)

        Returns:
            Tuple of (content bytes, MIME type)

        Raises:
            SecurityError: If path traversal is detected
            SkillNotFoundError: If reference not found
        """
        # Path traversal protection
        # Decode URL-encoded characters first
        decoded_path = unquote(ref_path)

        # Check for null bytes
        if "\x00" in decoded_path:
            raise SecurityError("Null bytes not allowed in path")

        # Check for path traversal attempts and absolute paths
        # Absolute paths (starting with /) are rejected for defense-in-depth
        if ".." in decoded_path.split("/") or decoded_path.startswith("/"):
            raise SecurityError(f"Path traversal detected: {ref_path}")

        # Look up the original_name from the skill cache.
        # The MCP server uses directory names as-is (may contain underscores),
        # while Skill.name normalizes to kebab-case. We need the original name
        # to construct the correct skill:// URI for the MCP server.
        original_name = skill_name
        skills = await self.get_skills()
        skill = next((s for s in skills if s.name == skill_name), None)
        if skill is not None:
            original_name = skill.metadata.get("original_name", skill_name)

        # Construct the full URI for the MCP server's SkillsProvider
        # Format: skill://{original_name}/references/{path}
        # The MCP server's SkillsProvider expects skill://skill-name/file-path,
        # NOT skill://provider/skill-name/file-path (self.name is NOT part of URI).
        # Avoid double "references/" prefix when decoded_path already contains it
        if decoded_path.startswith("references/"):
            uri = f"skill://{original_name}/{decoded_path}"
        else:
            uri = f"skill://{original_name}/references/{decoded_path}"

        try:
            content = await self.read_resource(uri)
            if content:
                # Return content as bytes with text/markdown MIME type
                return content[0].encode("utf-8"), "text/markdown"
        except Exception as e:
            raise SkillNotFoundError(f"Reference not found: {ref_path}") from e

        raise SkillNotFoundError(f"Reference not found: {ref_path}")

    async def _on_skills_changed(self) -> None:
        """Callback when skills change on the MCP server.

        Derives from prompt and resource changes. Invalidates the skills cache
        and emits the skills_changed signal.
        """
        logger.info("MCP skill list changed, refreshing provider cache")
        self._skills_cache = None
        # Notify subscribers via signal
        await self.skills_changed.emit(self.create_change_event("skills"))


if __name__ == "__main__":
    import anyio

    cfg = "uv run /home/phil65/dev/oss/agentpool/tests/mcp_server/server.py"

    async def main() -> None:
        manager = MCPResourceProvider(cfg)
        async with manager:
            prompts = await manager.get_prompts()
            print(f"Found prompts: {prompts}")

            # Test static prompt (no arguments)
            static_prompt = next(p for p in prompts if p.name == "static_prompt")
            print(f"\n--- Testing static prompt: {static_prompt} ---")
            components = await static_prompt.get_components()
            assert components, "No prompt components found"
            print(f"Found {len(components)} prompt components:")
            for i, component in enumerate(components):
                comp_type = type(component).__name__
                print(f"  {i + 1}. {comp_type}: {component.content}")

            # Test dynamic prompt (with arguments)
            dynamic_prompt = next(p for p in prompts if p.name == "dynamic_prompt")
            print(f"\n--- Testing dynamic prompt: {dynamic_prompt} ---")
            components = await dynamic_prompt.get_components(
                arguments={"some_arg": "Hello, world!"}
            )
            assert components, "No prompt components found"
            print(f"Found {len(components)} prompt components:")
            for i, component in enumerate(components):
                comp_type = type(component).__name__
                print(f"  {i + 1}. {comp_type}: {component.content}")

    anyio.run(main)
