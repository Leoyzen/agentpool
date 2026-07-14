"""Tool implementations and related classes / functions.

Re-exports from pydantic-ai:
- CallDeferred: Exception for tool authors to signal deferred execution.
- ApprovalRequired: Exception for tool authors to signal human-in-the-loop approval needed.
"""

from __future__ import annotations

from pydantic_ai.exceptions import ApprovalRequired, CallDeferred

from agentpool.tools.base import FunctionTool, Tool
from agentpool.tools.exceptions import ToolError
from agentpool.tools.tool_call_info import ToolCallInfo
from agentpool.skills.registry import SkillsRegistry

__all__ = [
    "ApprovalRequired",
    "CallDeferred",
    "FunctionTool",
    "SkillsRegistry",
    "Tool",
    "ToolCallInfo",
    "ToolError",
]
