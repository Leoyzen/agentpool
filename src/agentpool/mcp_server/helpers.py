"""Helper functions for MCP server client operations.

This module contains stateless utility functions that support MCP tool conversion
and content handling for PydanticAI integration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger


if TYPE_CHECKING:
    from mcp.types import ContentBlock, Tool as MCPTool, ToolAnnotations

    from agentpool_config.tools import ToolHints


logger = get_logger(__name__)


def mcp_tool_to_input_schema(tool: MCPTool) -> dict[str, Any]:
    """Convert MCP tool inputSchema to OpenAI function schema format."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.inputSchema or {"type": "object", "properties": {}},
    }


def mcp_annotations_to_hints(annotations: ToolAnnotations | None) -> ToolHints | None:
    """Convert MCP ToolAnnotations to agentpool ToolHints."""
    if annotations is None:
        return None
    from agentpool_config.tools import ToolHints

    return ToolHints(
        read_only=annotations.readOnlyHint,
        destructive=annotations.destructiveHint,
        idempotent=annotations.idempotentHint,
        open_world=annotations.openWorldHint,
    )


def extract_text_content(mcp_content: list[ContentBlock]) -> str:
    """Extract text content from MCP content blocks.

    Args:
        mcp_content: List of MCP content blocks

    Returns:
        First available text content or fallback string
    """
    from mcp.types import TextContent

    for block in mcp_content:
        match block:
            case TextContent(text=text):
                return text

    # Fallback: stringify the content
    return str(mcp_content[0]) if mcp_content else "Tool executed successfully"
