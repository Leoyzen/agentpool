"""Builtin tool configuration models for Codex app-server.

Each builtin tool has a dedicated Pydantic model with a ``type`` discriminator
field.  All tool configs are collected in the ``ToolConfig`` discriminated union
so that ``list[ToolConfig]`` can be used as a typed parameter.

A ``BuiltinToolsConfig`` convenience model groups all tools with named fields,
while :func:`tools_to_config_dict` converts an arbitrary ``list[ToolConfig]``
into the ``config`` dict accepted by ``ThreadStartParams``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag


class _ToolConfigBase(BaseModel):
    """Base for individual tool configuration models."""

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# Shell tool
# ---------------------------------------------------------------------------


class ShellToolConfig(_ToolConfigBase):
    """Configuration for the shell / exec tool."""

    type: Literal["shell"] = "shell"

    enabled: bool = True
    """Enable or disable the shell tool entirely."""

    allow_login_shell: bool | None = None
    """Whether the model may request a login shell."""


# ---------------------------------------------------------------------------
# Apply-patch tool
# ---------------------------------------------------------------------------


class ApplyPatchToolConfig(_ToolConfigBase):
    """Configuration for the apply_patch tool."""

    type: Literal["apply_patch"] = "apply_patch"

    enabled: bool = True
    """Enable or disable the apply_patch tool."""

    variant: Literal["freeform", "function"] | None = None
    """Patch format variant."""


# ---------------------------------------------------------------------------
# Web search tool
# ---------------------------------------------------------------------------


class WebSearchLocationConfig(_ToolConfigBase):
    """Location hints for web search results."""

    country: str | None = None
    region: str | None = None
    city: str | None = None
    timezone: str | None = None


class WebSearchToolConfig(_ToolConfigBase):
    """Configuration for the web_search tool."""

    type: Literal["web_search"] = "web_search"

    enabled: bool = True
    """Enable or disable web search."""

    mode: Literal["disabled", "cached", "live"] | None = None
    """Search mode."""

    context_size: Literal["low", "medium", "high"] | None = None
    """Amount of context returned from search results."""

    allowed_domains: list[str] | None = None
    """Restrict search to these domains only."""

    location: WebSearchLocationConfig | None = None
    """Approximate user location for localised results."""

    content_types: Literal["text", "text_and_image"] | None = None
    """Content types to include in search results."""


# ---------------------------------------------------------------------------
# Image generation tool
# ---------------------------------------------------------------------------


class ImageGenerationToolConfig(_ToolConfigBase):
    """Configuration for the image_generation tool."""

    type: Literal["image_generation"] = "image_generation"

    enabled: bool = False
    """Enable or disable image generation (disabled by default)."""


# ---------------------------------------------------------------------------
# View image tool
# ---------------------------------------------------------------------------


class ViewImageToolConfig(_ToolConfigBase):
    """Configuration for the view_image tool."""

    type: Literal["view_image"] = "view_image"

    enabled: bool = True
    """Enable or disable the view_image tool."""


# ---------------------------------------------------------------------------
# Plan tool
# ---------------------------------------------------------------------------


class PlanToolConfig(_ToolConfigBase):
    """Configuration for the update_plan tool."""

    type: Literal["plan"] = "plan"

    enabled: bool = True
    """Enable or disable the plan tool."""


# ---------------------------------------------------------------------------
# JavaScript REPL tool
# ---------------------------------------------------------------------------


class JsReplToolConfig(_ToolConfigBase):
    """Configuration for the js_repl / js_repl_reset tools."""

    type: Literal["js_repl"] = "js_repl"

    enabled: bool = False
    """Enable or disable the JavaScript REPL (disabled by default)."""


# ---------------------------------------------------------------------------
# Collaboration / multi-agent tools
# ---------------------------------------------------------------------------


class CollabToolsConfig(_ToolConfigBase):
    """Configuration for multi-agent collaboration tools.

    Controls ``spawn_agent``, ``send_input``, ``resume_agent``,
    ``wait_agent``, and ``close_agent``.
    """

    type: Literal["collab"] = "collab"

    enabled: bool = True
    """Enable or disable all collaboration tools."""


# ---------------------------------------------------------------------------
# Agent jobs tools
# ---------------------------------------------------------------------------


class AgentJobsToolsConfig(_ToolConfigBase):
    """Configuration for CSV-backed agent job tools.

    Controls ``spawn_agents_on_csv`` and ``report_agent_job_result``.
    """

    type: Literal["agent_jobs"] = "agent_jobs"

    enabled: bool = False
    """Enable or disable agent-jobs tools (disabled by default)."""


# ---------------------------------------------------------------------------
# Request user input tool
# ---------------------------------------------------------------------------


class RequestUserInputToolConfig(_ToolConfigBase):
    """Configuration for the request_user_input tool."""

    type: Literal["request_user_input"] = "request_user_input"

    enabled: bool = True
    """Enable or disable the request_user_input tool."""


# ---------------------------------------------------------------------------
# Request permissions tool
# ---------------------------------------------------------------------------


class RequestPermissionsToolConfig(_ToolConfigBase):
    """Configuration for the request_permissions tool."""

    type: Literal["request_permissions"] = "request_permissions"

    enabled: bool = False
    """Enable or disable the request_permissions tool (disabled by default)."""


# ---------------------------------------------------------------------------
# Artifacts tool
# ---------------------------------------------------------------------------


class ArtifactsToolConfig(_ToolConfigBase):
    """Configuration for the artifacts tool."""

    type: Literal["artifacts"] = "artifacts"

    enabled: bool = False
    """Enable or disable the artifacts tool (disabled by default)."""


# ---------------------------------------------------------------------------
# Grep files tool (experimental)
# ---------------------------------------------------------------------------


class GrepFilesToolConfig(_ToolConfigBase):
    """Configuration for the grep_files tool (experimental)."""

    type: Literal["grep_files"] = "grep_files"

    enabled: bool = False
    """Enable or disable the grep_files tool."""


# ---------------------------------------------------------------------------
# Read file tool (experimental)
# ---------------------------------------------------------------------------


class ReadFileToolConfig(_ToolConfigBase):
    """Configuration for the read_file tool (experimental)."""

    type: Literal["read_file"] = "read_file"

    enabled: bool = False
    """Enable or disable the read_file tool."""


# ---------------------------------------------------------------------------
# List dir tool (experimental)
# ---------------------------------------------------------------------------


class ListDirToolConfig(_ToolConfigBase):
    """Configuration for the list_dir tool (experimental)."""

    type: Literal["list_dir"] = "list_dir"

    enabled: bool = False
    """Enable or disable the list_dir tool."""


# ---------------------------------------------------------------------------
# Code mode tool
# ---------------------------------------------------------------------------


class CodeModeToolConfig(_ToolConfigBase):
    """Configuration for the code-mode tool (experimental)."""

    type: Literal["code_mode"] = "code_mode"

    enabled: bool = False
    """Enable or disable code mode."""

    only: bool = False
    """When True, restrict model-visible tools to code mode entrypoints only."""


# ---------------------------------------------------------------------------
# Tool search / suggest
# ---------------------------------------------------------------------------


class ToolSearchToolConfig(_ToolConfigBase):
    """Configuration for the tool_search tool (requires apps)."""

    type: Literal["tool_search"] = "tool_search"

    enabled: bool = False
    """Enable or disable the tool_search tool."""


class ToolSuggestToolConfig(_ToolConfigBase):
    """Configuration for the tool_suggest tool (requires discoverable tools)."""

    type: Literal["tool_suggest"] = "tool_suggest"

    enabled: bool = False
    """Enable or disable the tool_suggest tool."""


# ---------------------------------------------------------------------------
# MCP resource tools
# ---------------------------------------------------------------------------


class McpResourceToolsConfig(_ToolConfigBase):
    """Configuration for MCP resource browsing tools.

    Controls ``list_mcp_resources``, ``list_mcp_resource_templates``,
    and ``read_mcp_resource``.
    """

    type: Literal["mcp_resources"] = "mcp_resources"

    enabled: bool = True
    """Enable or disable MCP resource tools."""


# ===========================================================================
# Discriminated union
# ===========================================================================

ToolConfig = Annotated[
    Annotated[ShellToolConfig, Tag("shell")]
    | Annotated[ApplyPatchToolConfig, Tag("apply_patch")]
    | Annotated[WebSearchToolConfig, Tag("web_search")]
    | Annotated[ImageGenerationToolConfig, Tag("image_generation")]
    | Annotated[ViewImageToolConfig, Tag("view_image")]
    | Annotated[PlanToolConfig, Tag("plan")]
    | Annotated[JsReplToolConfig, Tag("js_repl")]
    | Annotated[CollabToolsConfig, Tag("collab")]
    | Annotated[AgentJobsToolsConfig, Tag("agent_jobs")]
    | Annotated[RequestUserInputToolConfig, Tag("request_user_input")]
    | Annotated[RequestPermissionsToolConfig, Tag("request_permissions")]
    | Annotated[ArtifactsToolConfig, Tag("artifacts")]
    | Annotated[GrepFilesToolConfig, Tag("grep_files")]
    | Annotated[ReadFileToolConfig, Tag("read_file")]
    | Annotated[ListDirToolConfig, Tag("list_dir")]
    | Annotated[CodeModeToolConfig, Tag("code_mode")]
    | Annotated[ToolSearchToolConfig, Tag("tool_search")]
    | Annotated[ToolSuggestToolConfig, Tag("tool_suggest")]
    | Annotated[McpResourceToolsConfig, Tag("mcp_resources")],
    Discriminator("type"),
]
"""Discriminated union of all builtin tool configurations."""


# ===========================================================================
# Conversion helpers
# ===========================================================================


def tools_to_config_dict(tools: list[ToolConfig]) -> dict[str, Any]:  # noqa: PLR0915
    """Convert a list of tool configs into the ``config`` dict for ``ThreadStartParams``.

    Each tool config contributes its settings to the appropriate config keys.
    Only non-default values are emitted so that server defaults are preserved.

    Example::

        config = tools_to_config_dict([
            WebSearchToolConfig(mode="live", context_size="high"),
            JsReplToolConfig(enabled=True),
            CollabToolsConfig(enabled=False),
        ])
    """
    features: dict[str, bool] = {}
    config: dict[str, Any] = {}
    tools_section: dict[str, Any] = {}
    experimental_tools: list[str] = []

    for tool in tools:
        match tool:
            case ShellToolConfig(enabled=enabled, allow_login_shell=allow_login):
                if not enabled:
                    features["shell_tool"] = False
                if allow_login is not None:
                    config["allow_login_shell"] = allow_login

            case ApplyPatchToolConfig(enabled=enabled, variant=variant):
                if not enabled:
                    config["include_apply_patch_tool"] = False
                elif variant is not None:
                    config["include_apply_patch_tool"] = True

            case WebSearchToolConfig(
                mode=mode,
                context_size=context_size,
                allowed_domains=allowed_domains,
                location=location,
                content_types=_content_types,
            ):
                if mode is not None:
                    config["web_search"] = mode
                ws_config: dict[str, Any] = {}
                if context_size is not None:
                    ws_config["context_size"] = context_size
                if allowed_domains is not None:
                    ws_config["allowed_domains"] = allowed_domains
                if location is not None:
                    loc = location.model_dump(exclude_none=True)
                    if loc:
                        ws_config["location"] = loc
                if ws_config:
                    tools_section["web_search"] = ws_config

            case ImageGenerationToolConfig(enabled=True):
                features["image_generation"] = True

            case ViewImageToolConfig(enabled=False):
                tools_section["view_image"] = False

            case JsReplToolConfig(enabled=True):
                features["js_repl"] = True

            case CollabToolsConfig(enabled=False):
                features["multi_agent"] = False

            case AgentJobsToolsConfig(enabled=True):
                features["enable_fanout"] = True

            case RequestPermissionsToolConfig(enabled=True):
                features["request_permissions_tool"] = True

            case CodeModeToolConfig(enabled=enabled, only=only):
                if enabled:
                    features["code_mode"] = True
                if only:
                    features["code_mode_only"] = True

            case ArtifactsToolConfig(enabled=True):
                features["artifact"] = True

            case ToolSuggestToolConfig(enabled=True):
                features["tool_suggest"] = True

            case GrepFilesToolConfig(enabled=True):
                experimental_tools.append("grep_files")

            case ReadFileToolConfig(enabled=True):
                experimental_tools.append("read_file")

            case ListDirToolConfig(enabled=True):
                experimental_tools.append("list_dir")

            case (
                PlanToolConfig()
                | RequestUserInputToolConfig()
                | ToolSearchToolConfig()
                | McpResourceToolsConfig()
            ):
                pass  # No config keys to emit for these currently

    if features:
        config["features"] = features
    if tools_section:
        config["tools"] = tools_section
    if experimental_tools:
        config["experimental_supported_tools"] = experimental_tools

    return config


# ===========================================================================
# Combined configuration (convenience)
# ===========================================================================


class BuiltinToolsConfig(BaseModel):
    """Combined configuration for all Codex builtin tools.

    Provides named fields as a convenience over ``list[ToolConfig]``.
    Call :meth:`to_config_dict` to obtain the ``config`` dict for
    ``ThreadStartParams``, or :meth:`to_tool_list` to get a
    ``list[ToolConfig]``.

    Example::

        tools = BuiltinToolsConfig(
            web_search=WebSearchToolConfig(mode="live", context_size="high"),
            js_repl=JsReplToolConfig(enabled=True),
            collab=CollabToolsConfig(enabled=False),
        )
        params = ThreadStartParams(config=tools.to_config_dict())
    """

    model_config = ConfigDict(populate_by_name=True)

    shell: ShellToolConfig = Field(default_factory=ShellToolConfig)
    apply_patch: ApplyPatchToolConfig = Field(default_factory=ApplyPatchToolConfig)
    web_search: WebSearchToolConfig = Field(default_factory=WebSearchToolConfig)
    image_generation: ImageGenerationToolConfig = Field(default_factory=ImageGenerationToolConfig)
    view_image: ViewImageToolConfig = Field(default_factory=ViewImageToolConfig)
    plan: PlanToolConfig = Field(default_factory=PlanToolConfig)
    js_repl: JsReplToolConfig = Field(default_factory=JsReplToolConfig)
    collab: CollabToolsConfig = Field(default_factory=CollabToolsConfig)
    agent_jobs: AgentJobsToolsConfig = Field(default_factory=AgentJobsToolsConfig)
    request_user_input: RequestUserInputToolConfig = Field(
        default_factory=RequestUserInputToolConfig,
    )
    request_permissions: RequestPermissionsToolConfig = Field(
        default_factory=RequestPermissionsToolConfig,
    )
    artifacts: ArtifactsToolConfig = Field(default_factory=ArtifactsToolConfig)
    grep_files: GrepFilesToolConfig = Field(default_factory=GrepFilesToolConfig)
    read_file: ReadFileToolConfig = Field(default_factory=ReadFileToolConfig)
    list_dir: ListDirToolConfig = Field(default_factory=ListDirToolConfig)
    code_mode: CodeModeToolConfig = Field(default_factory=CodeModeToolConfig)
    tool_search: ToolSearchToolConfig = Field(default_factory=ToolSearchToolConfig)
    tool_suggest: ToolSuggestToolConfig = Field(default_factory=ToolSuggestToolConfig)
    mcp_resources: McpResourceToolsConfig = Field(default_factory=McpResourceToolsConfig)

    def to_tool_list(self) -> list[ToolConfig]:
        """Return all tool configs as a list."""
        return [
            self.shell,
            self.apply_patch,
            self.web_search,
            self.image_generation,
            self.view_image,
            self.plan,
            self.js_repl,
            self.collab,
            self.agent_jobs,
            self.request_user_input,
            self.request_permissions,
            self.artifacts,
            self.grep_files,
            self.read_file,
            self.list_dir,
            self.code_mode,
            self.tool_search,
            self.tool_suggest,
            self.mcp_resources,
        ]

    def to_config_dict(self) -> dict[str, Any]:
        """Serialize into a flat config dict for ``ThreadStartParams.config``."""
        return tools_to_config_dict(self.to_tool_list())
