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

from pydantic import BaseModel, ConfigDict, Discriminator, Tag


class _ToolConfigBase(BaseModel):
    """Base for individual tool configuration models."""

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# Shell tool
# ---------------------------------------------------------------------------


class ShellToolConfig(_ToolConfigBase):
    """Configuration for the shell / exec tool."""

    type: Literal["shell"] = "shell"

    allow_login_shell: bool | None = None
    """Whether the model may request a login shell."""


# ---------------------------------------------------------------------------
# Apply-patch tool
# ---------------------------------------------------------------------------


class ApplyPatchToolConfig(_ToolConfigBase):
    """Configuration for the apply_patch tool."""

    type: Literal["apply_patch"] = "apply_patch"

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

    mode: Literal["disabled", "cached", "live"] | None = None
    """Search mode."""

    context_size: Literal["low", "medium", "high"] | None = None
    """Amount of context returned from search results."""

    allowed_domains: list[str] | None = None
    """Restrict search to these domains only."""

    location: WebSearchLocationConfig | None = None
    """Approximate user location for localised results."""


# ---------------------------------------------------------------------------
# Image generation tool
# ---------------------------------------------------------------------------


class ImageGenerationToolConfig(_ToolConfigBase):
    """Configuration for the image_generation tool."""

    type: Literal["image_generation"] = "image_generation"


# ---------------------------------------------------------------------------
# View image tool
# ---------------------------------------------------------------------------


class ViewImageToolConfig(_ToolConfigBase):
    """Configuration for the view_image tool."""

    type: Literal["view_image"] = "view_image"


# ---------------------------------------------------------------------------
# Plan tool
# ---------------------------------------------------------------------------


class PlanToolConfig(_ToolConfigBase):
    """Configuration for the update_plan tool."""

    type: Literal["plan"] = "plan"


# ---------------------------------------------------------------------------
# JavaScript REPL tool
# ---------------------------------------------------------------------------


class JsReplToolConfig(_ToolConfigBase):
    """Configuration for the js_repl / js_repl_reset tools."""

    type: Literal["js_repl"] = "js_repl"


# ---------------------------------------------------------------------------
# Collaboration / multi-agent tools
# ---------------------------------------------------------------------------


class CollabToolsConfig(_ToolConfigBase):
    """Configuration for multi-agent collaboration tools.

    Controls ``spawn_agent``, ``send_input``, ``resume_agent``,
    ``wait_agent``, and ``close_agent``.
    """

    type: Literal["collab"] = "collab"


# ---------------------------------------------------------------------------
# Agent jobs tools
# ---------------------------------------------------------------------------


class AgentJobsToolsConfig(_ToolConfigBase):
    """Configuration for CSV-backed agent job tools.

    Controls ``spawn_agents_on_csv`` and ``report_agent_job_result``.
    """

    type: Literal["agent_jobs"] = "agent_jobs"


# ---------------------------------------------------------------------------
# Request user input tool
# ---------------------------------------------------------------------------


class RequestUserInputToolConfig(_ToolConfigBase):
    """Configuration for the request_user_input tool."""

    type: Literal["request_user_input"] = "request_user_input"


# ---------------------------------------------------------------------------
# Request permissions tool
# ---------------------------------------------------------------------------


class RequestPermissionsToolConfig(_ToolConfigBase):
    """Configuration for the request_permissions tool."""

    type: Literal["request_permissions"] = "request_permissions"


# ---------------------------------------------------------------------------
# Artifacts tool
# ---------------------------------------------------------------------------


class ArtifactsToolConfig(_ToolConfigBase):
    """Configuration for the artifacts tool."""

    type: Literal["artifacts"] = "artifacts"


# ---------------------------------------------------------------------------
# Grep files tool (experimental)
# ---------------------------------------------------------------------------


class GrepFilesToolConfig(_ToolConfigBase):
    """Configuration for the grep_files tool (experimental)."""

    type: Literal["grep_files"] = "grep_files"


# ---------------------------------------------------------------------------
# Read file tool (experimental)
# ---------------------------------------------------------------------------


class ReadFileToolConfig(_ToolConfigBase):
    """Configuration for the read_file tool (experimental)."""

    type: Literal["read_file"] = "read_file"


# ---------------------------------------------------------------------------
# List dir tool (experimental)
# ---------------------------------------------------------------------------


class ListDirToolConfig(_ToolConfigBase):
    """Configuration for the list_dir tool (experimental)."""

    type: Literal["list_dir"] = "list_dir"


# ---------------------------------------------------------------------------
# Code mode tool
# ---------------------------------------------------------------------------


class CodeModeToolConfig(_ToolConfigBase):
    """Configuration for the code-mode tool (experimental)."""

    type: Literal["code_mode"] = "code_mode"

    only: bool = False
    """When True, restrict model-visible tools to code mode entrypoints only."""


# ---------------------------------------------------------------------------
# Tool search / suggest
# ---------------------------------------------------------------------------


class ToolSearchToolConfig(_ToolConfigBase):
    """Configuration for the tool_search tool (requires apps)."""

    type: Literal["tool_search"] = "tool_search"


class ToolSuggestToolConfig(_ToolConfigBase):
    """Configuration for the tool_suggest tool (requires discoverable tools)."""

    type: Literal["tool_suggest"] = "tool_suggest"


# ---------------------------------------------------------------------------
# MCP resource tools
# ---------------------------------------------------------------------------


class McpResourceToolsConfig(_ToolConfigBase):
    """Configuration for MCP resource browsing tools.

    Controls ``list_mcp_resources``, ``list_mcp_resource_templates``,
    and ``read_mcp_resource``.
    """

    type: Literal["mcp_resources"] = "mcp_resources"


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


def _disable_all_tools() -> dict[str, Any]:
    """Return config dict that disables all builtin tools."""
    return {
        "features": {
            "shell_tool": False,
            "image_generation": False,
            "js_repl": False,
            "multi_agent": False,
            "enable_fanout": False,
            "request_permissions_tool": False,
            "code_mode": False,
            "artifact": False,
            "tool_suggest": False,
        },
        "include_apply_patch_tool": False,
        "web_search": "disabled",
        "tools": {
            "view_image": False,
        },
    }


def tools_to_config_dict(tools: list[ToolConfig]) -> dict[str, Any]:  # noqa: PLR0915
    """Convert a list of tool configs into the ``config`` dict for ``ThreadStartParams``.

    Each tool config contributes its settings to the appropriate config keys.
    An empty list explicitly disables all builtin tools.

    Example::

        config = tools_to_config_dict([])  # disable all tools
        config = tools_to_config_dict([    # enable only these
            WebSearchToolConfig(mode="live", context_size="high"),
            JsReplToolConfig(),
        ])
    """
    if not tools:
        return _disable_all_tools()

    features: dict[str, bool] = {}
    config: dict[str, Any] = {}
    tools_section: dict[str, Any] = {}
    experimental_tools: list[str] = []

    for tool in tools:
        match tool:
            case ShellToolConfig(allow_login_shell=allow_login):
                features["shell_tool"] = True
                if allow_login is not None:
                    config["allow_login_shell"] = allow_login

            case ApplyPatchToolConfig(variant=variant):
                config["include_apply_patch_tool"] = True
                if variant == "freeform":
                    features["apply_patch_freeform"] = True
                elif variant == "function":
                    features["apply_patch_freeform"] = False

            case WebSearchToolConfig(
                mode=mode,
                context_size=context_size,
                allowed_domains=allowed_domains,
                location=location,
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

            case ImageGenerationToolConfig():
                features["image_generation"] = True

            case ViewImageToolConfig():
                tools_section["view_image"] = True

            case JsReplToolConfig():
                features["js_repl"] = True

            case CollabToolsConfig():
                features["multi_agent"] = True

            case AgentJobsToolsConfig():
                features["enable_fanout"] = True

            case RequestPermissionsToolConfig():
                features["request_permissions_tool"] = True

            case CodeModeToolConfig(only=only):
                features["code_mode"] = True
                if only:
                    features["code_mode_only"] = True

            case ArtifactsToolConfig():
                features["artifact"] = True

            case ToolSuggestToolConfig():
                features["tool_suggest"] = True

            case GrepFilesToolConfig():
                experimental_tools.append("grep_files")

            case ReadFileToolConfig():
                experimental_tools.append("read_file")

            case ListDirToolConfig():
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
            js_repl=JsReplToolConfig(),
        )
        params = ThreadStartParams(config=tools.to_config_dict())
    """

    model_config = ConfigDict(populate_by_name=True)

    shell: ShellToolConfig | None = None
    apply_patch: ApplyPatchToolConfig | None = None
    web_search: WebSearchToolConfig | None = None
    image_generation: ImageGenerationToolConfig | None = None
    view_image: ViewImageToolConfig | None = None
    plan: PlanToolConfig | None = None
    js_repl: JsReplToolConfig | None = None
    collab: CollabToolsConfig | None = None
    agent_jobs: AgentJobsToolsConfig | None = None
    request_user_input: RequestUserInputToolConfig | None = None
    request_permissions: RequestPermissionsToolConfig | None = None
    artifacts: ArtifactsToolConfig | None = None
    grep_files: GrepFilesToolConfig | None = None
    read_file: ReadFileToolConfig | None = None
    list_dir: ListDirToolConfig | None = None
    code_mode: CodeModeToolConfig | None = None
    tool_search: ToolSearchToolConfig | None = None
    tool_suggest: ToolSuggestToolConfig | None = None
    mcp_resources: McpResourceToolsConfig | None = None

    def to_tool_list(self) -> list[ToolConfig]:
        """Return only explicitly set tool configs as a list."""
        return [
            v
            for v in (
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
            )
            if v is not None
        ]

    def to_config_dict(self) -> dict[str, Any]:
        """Serialize into a flat config dict for ``ThreadStartParams.config``.

        Only explicitly set tools are included. An empty ``BuiltinToolsConfig``
        produces an empty dict (server defaults preserved).
        """
        tool_list = self.to_tool_list()
        if not tool_list:
            return {}
        return tools_to_config_dict(tool_list)
