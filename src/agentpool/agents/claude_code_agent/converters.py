"""Claude Agent SDK to native event converters.

This module provides conversion from Claude Agent SDK message types to native
agentpool streaming events, enabling ClaudeCodeAgent to yield the same
event types as native agents.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, assert_never, cast

from clawd_code_sdk import McpServerConfig
from clawd_code_sdk.models import BashInput
from clawd_code_sdk.models.output_types import (
    BashOutput,
    EditOutput,
    ReadOutput,
    TodoWriteOutput,
    WriteOutput,
)
from pydantic_ai import RequestUsage, RunUsage

from agentpool.utils.diffs import compute_unified_diff
from agentpool_server.opencode_server.models.tool_metadata import (
    BashMetadata,
    EditMetadata,
    FileDiff,
    ReadMetadata,
    TodoInfo,
    TodoMetadata,
    WriteMetadata,
)


if TYPE_CHECKING:
    from clawd_code_sdk import PermissionResult, ThinkingConfig
    from clawd_code_sdk.models import SystemPromptPreset, ToolInput, Usage
    from clawd_code_sdk.models.output_types import StructuredPatchHunk

    from agentpool.agents.context import ConfirmationResult
    from agentpool_config.mcp_server import MCPServerConfig as NativeMCPServerConfig
    from agentpool_server.opencode_server.models.tool_metadata import ToolMetadata


def to_thinking_config(
    max_thinking_tokens: int | Literal["adaptive"] | None,
) -> ThinkingConfig | None:
    from clawd_code_sdk import ThinkingConfigAdaptive, ThinkingConfigDisabled, ThinkingConfigEnabled

    if max_thinking_tokens == "adaptive":
        return ThinkingConfigAdaptive(type="adaptive")
    if max_thinking_tokens == 0:
        return ThinkingConfigDisabled(type="disabled")
    if max_thinking_tokens:
        return ThinkingConfigEnabled(type="enabled", budget_tokens=max_thinking_tokens)
    return None


def to_run_usage(usage_dict: Usage) -> RunUsage:
    return RunUsage(
        input_tokens=usage_dict["input_tokens"],
        output_tokens=usage_dict["output_tokens"],
        cache_read_tokens=usage_dict["cache_read_input_tokens"],
        cache_write_tokens=usage_dict["cache_creation_input_tokens"],
    )


def to_request_usage(usage_dict: Usage) -> RequestUsage:
    return RequestUsage(
        input_tokens=usage_dict["input_tokens"],
        output_tokens=usage_dict["output_tokens"],
        cache_read_tokens=usage_dict["cache_read_input_tokens"],
        cache_write_tokens=usage_dict["cache_creation_input_tokens"],
    )


def confirmation_result_to_native(result: ConfirmationResult) -> PermissionResult:
    from clawd_code_sdk import PermissionResultAllow, PermissionResultDeny

    match result:
        case "allow":
            return PermissionResultAllow()
        case "skip":
            return PermissionResultDeny(message="User skipped tool execution")
        case "abort_run" | "abort_chain":
            return PermissionResultDeny(message="User aborted execution", interrupt=True)
        case _ as unreachable:
            raise assert_never(unreachable)


def to_claude_system_prompt(
    system_prompt: str, include_default: bool = True
) -> SystemPromptPreset | str:
    from clawd_code_sdk.models import SystemPromptPreset

    if include_default:
        # Use SystemPromptPreset to append to builtin prompt
        return SystemPromptPreset(type="preset", preset="claude_code", append=system_prompt)
    return system_prompt


def convert_mcp_servers_to_sdk_format(
    mcp_servers: list[NativeMCPServerConfig],
) -> dict[str, McpServerConfig]:
    """Convert internal MCPServerConfig to Claude SDK format.

    Returns:
        Dict mapping server names to SDK-compatible config dicts
    """
    from urllib.parse import urlparse

    from agentpool_config.mcp_server import (
        SSEMCPServerConfig,
        StdioMCPServerConfig,
        StreamableHTTPMCPServerConfig,
    )

    result: dict[str, McpServerConfig] = {}

    for idx, server in enumerate(mcp_servers):
        # Determine server name
        match server:
            case _ if server.name:
                name = server.name
            case StdioMCPServerConfig() if server.args:
                name = server.args[-1].split("/")[-1].split("@")[0]
            case StdioMCPServerConfig():
                name = server.command
            case SSEMCPServerConfig() | StreamableHTTPMCPServerConfig():
                name = urlparse(str(server.url)).hostname or f"server_{idx}"
            case _ as unreachable:
                assert_never(unreachable)

        # Build SDK-compatible config
        config: dict[str, Any]
        match server:
            case StdioMCPServerConfig(command=command, args=args):
                config = {"type": "stdio", "command": command, "args": args}
                if server.env:
                    config["env"] = server.get_env_vars()
            case SSEMCPServerConfig(url=url):
                config = {"type": "sse", "url": str(url)}
                if server.headers:
                    config["headers"] = server.headers
            case StreamableHTTPMCPServerConfig(url=url):
                config = {"type": "http", "url": str(url)}
                if server.headers:
                    config["headers"] = server.headers

        result[name] = cast(McpServerConfig, config)

    return result


def convert_to_opencode_metadata(  # noqa: PLR0911
    tool_name: str,
    tool_use_result: dict[str, Any] | ToolInput | str | None,
    tool_input: ToolInput | dict[str, Any] | None = None,
) -> ToolMetadata | None:
    """Convert Claude Code SDK tool_use_result to OpenCode metadata format."""
    # Handle None or string results (bash errors come as plain strings)
    if tool_use_result is None or not isinstance(tool_use_result, dict):
        return None
    tool_input = tool_input or {}
    # Dispatch to appropriate converter based on tool name
    match tool_name.lower():
        case "write":
            return _convert_write_result(cast(WriteOutput, tool_use_result))
        case "edit":
            return _convert_edit_result(cast(EditOutput, tool_use_result))
        case "read":
            return _convert_read_result(cast(ReadOutput, tool_use_result))
        case "bash":
            return _convert_bash_result(
                cast(BashOutput, tool_use_result),
                cast(BashInput, tool_input),
            )
        case "todowrite":
            return _convert_todowrite_result(cast(TodoWriteOutput, tool_use_result))
        case _:
            return None


def _convert_write_result(result: WriteOutput) -> WriteMetadata:
    """Convert Write tool result to OpenCode metadata."""
    return WriteMetadata(filepath=result["filePath"], exists=True, diagnostics={})


def _convert_edit_result(result: EditOutput) -> EditMetadata:
    """Convert Edit tool result to OpenCode metadata."""
    file_path = result["filePath"]
    original_file = result["originalFile"]
    old_string = result["oldString"]
    new_string = result["newString"]
    structured_patch = result["structuredPatch"]
    # Compute the "after" content by applying the edit
    after_content = original_file
    if original_file is not None and old_string and new_string:
        after_content = original_file.replace(old_string, new_string, 1)

    # Build unified diff from structuredPatch or compute it
    diff = _build_unified_diff(file_path, original_file, after_content, structured_patch)
    # Count additions and deletions
    additions, deletions = _count_diff_changes(structured_patch)
    filediff = FileDiff(
        file=file_path,
        before=original_file or "",
        after=after_content or "",
        additions=additions,
        deletions=deletions,
    )
    return EditMetadata(diff=diff, filediff=filediff, diagnostics={})


def _convert_read_result(result: ReadOutput) -> ReadMetadata:
    """Convert Read tool result to OpenCode metadata."""
    # Only text reads have meaningful content for preview
    if result["type"] != "text":
        return ReadMetadata(preview="", truncated=False, loaded=[])
    file_info = result["file"]
    lines = file_info["content"].splitlines()
    preview = "\n".join(lines[:20])  # Build preview from first ~20 lines
    truncated = file_info["numLines"] < file_info["totalLines"]
    return ReadMetadata(preview=preview, truncated=truncated, loaded=[])


def _convert_bash_result(result: BashOutput, tool_input: BashInput) -> BashMetadata:
    """Convert Bash tool result to OpenCode metadata."""
    stdout = result["stdout"]
    stderr = result["stderr"]
    # Combine stdout and stderr
    output = stdout
    if stderr:
        output = f"{stdout}\n{stderr}" if stdout else stderr
    # Get description from tool input (Claude Code uses "description" field)
    description = tool_input.get("description") or tool_input["command"]
    # Note: Claude Code SDK doesn't provide exit code in the success result structure,
    # it's only available in error strings. For successful commands, exit is 0.
    # The SDK result doesn't have an exit_code field, so we infer:
    # - If we got here with a dict result, the command likely succeeded (exit 0)
    # - Errors come as strings, not dicts
    exit_code: int | None = 0
    if result["interrupted"]:
        exit_code = None  # Interrupted commands don't have a clean exit code
    return BashMetadata(output=output, exit=exit_code, description=description)


def _convert_todowrite_result(result: TodoWriteOutput) -> TodoMetadata | None:
    """Convert TodoWrite tool result to OpenCode metadata."""
    new_todos = result["newTodos"]
    todos: list[TodoInfo] = []
    for i, todo in enumerate(new_todos):
        content = todo["content"]
        priority = _infer_priority(content, i, len(new_todos))
        todos.append(TodoInfo(content=content, status=todo["status"], priority=priority))
    return TodoMetadata(todos=todos)


# Priority thresholds for position-based inference
_HIGH_PRIORITY_THRESHOLD = 0.33
_MEDIUM_PRIORITY_THRESHOLD = 0.67


def _infer_priority(content: str, index: int, total: int) -> str:
    """Infer priority from content keywords or position."""
    content_lower = content.lower()

    # Check for explicit priority keywords
    high_keywords = ("critical", "urgent", "asap", "immediately", "important", "soon", "priority")
    low_keywords = ("later", "eventually", "low priority", "nice to have")

    if any(kw in content_lower for kw in high_keywords):
        return "high"
    if any(kw in content_lower for kw in low_keywords):
        return "low"

    # Fall back to position-based priority
    # First third = high, middle third = medium, last third = low
    if total <= 1:
        return "medium"
    position_ratio = index / (total - 1) if total > 1 else 0
    if position_ratio < _HIGH_PRIORITY_THRESHOLD:
        return "high"
    if position_ratio < _MEDIUM_PRIORITY_THRESHOLD:
        return "medium"
    return "low"


def _build_unified_diff(
    file_path: str,
    before: str | None,
    after: str | None,
    structured_patch: list[StructuredPatchHunk],
) -> str:
    """Build unified diff string from structured patch or content."""
    # If we have both before and after, compute proper diff
    if before is not None and after is not None:
        name = Path(file_path).name
        return compute_unified_diff(
            before,
            after,
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            ensure_trailing_newline=True,
        )

    # Fallback: reconstruct from structuredPatch
    if structured_patch:
        return _structured_patch_to_diff(file_path, structured_patch)

    return ""


def _structured_patch_to_diff(file_path: str, structured_patch: list[StructuredPatchHunk]) -> str:
    """Convert Claude Code's structuredPatch to unified diff format.

    structuredPatch format:
        [
            {
                "oldStart": 1,
                "oldLines": 4,
                "newStart": 1,
                "newLines": 5,
                "lines": [" def hello_world():", "+    \"\"\"Docstring.\"\"\"", ...]
            }
        ]

    The lines array uses prefixes: " " (context), "+" (added), "-" (removed)
    """
    from pathlib import Path

    name = Path(file_path).name
    lines = [f"--- a/{name}", f"+++ b/{name}"]

    for hunk in structured_patch:
        old_start = hunk.get("oldStart", 1)
        old_lines = hunk.get("oldLines", 0)
        new_start = hunk.get("newStart", 1)
        new_lines = hunk.get("newLines", 0)
        hunk_lines = hunk.get("lines", [])

        # Add hunk header
        lines.append(f"@@ -{old_start},{old_lines} +{new_start},{new_lines} @@")

        # Add the diff lines (already prefixed with ' ', '+', or '-')
        lines.extend(hunk_lines)

    return "\n".join(lines) + "\n" if lines else ""


def _count_diff_changes(structured_patch: list[StructuredPatchHunk]) -> tuple[int, int]:
    """Count additions and deletions from structured patch."""
    additions = 0
    deletions = 0

    for hunk in structured_patch:
        for line in hunk.get("lines", []):
            if line.startswith("+"):
                additions += 1
            elif line.startswith("-"):
                deletions += 1

    return additions, deletions


def build_sdk_hooks_from_agent_hooks(
    hooks: Any,  # AgentHooks
    agent_name: str,
) -> dict[str, list[Any]]:
    """Convert AgentHooks to Claude SDK hooks format.

    Args:
        hooks: AgentHooks instance with pre/post tool hooks
        agent_name: Name of the agent for context

    Returns:
        Dictionary mapping hook event names to HookMatcher lists
    """
    from clawd_code_sdk.models import HookMatcher

    result: dict[str, list[Any]] = {}

    if not hooks:
        return result

    # Check if we have pre_tool_use hooks
    if hooks.pre_tool_use:

        async def on_pre_tool_use(
            input_data: Any,
            tool_use_id: str | None,
            context: Any,
        ) -> dict[str, Any]:
            """Adapter for pre_tool_use hooks."""
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})

            pre_result = await hooks.run_pre_tool_hooks(
                agent_name=agent_name,
                tool_name=tool_name,
                tool_input=tool_input,
                session_id=input_data.get("session_id"),
            )

            # Convert our hook result to SDK format
            decision = pre_result.get("decision")
            if decision == "deny":
                reason = pre_result.get("reason", "Blocked by pre-tool hook")
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }

            # Check for modified input
            output: dict[str, Any] = {}
            if modified := pre_result.get("modified_input"):
                output["hookSpecificOutput"] = {
                    "hookEventName": "PreToolUse",
                    "updatedInput": modified,
                }

            return output

        result["PreToolUse"] = [HookMatcher(matcher="*", hooks=[on_pre_tool_use])]  # type: ignore[list-item]

    # Check if we have post_tool_use hooks
    if hooks.post_tool_use:

        async def on_post_tool_use(
            input_data: Any,
            tool_use_id: str | None,
            context: Any,
        ) -> dict[str, Any]:
            """Adapter for post_tool_use hooks."""
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})
            tool_response = input_data.get("tool_response")

            await hooks.run_post_tool_hooks(
                agent_name=agent_name,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_output=tool_response,
                duration_ms=0,  # SDK doesn't provide timing
                session_id=input_data.get("session_id"),
            )

            # Post hooks are observation-only in SDK, can add context
            return {}

        result["PostToolUse"] = [HookMatcher(matcher="*", hooks=[on_post_tool_use])]  # type: ignore[list-item]

    return result
