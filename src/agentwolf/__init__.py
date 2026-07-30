"""AgentPool: main package.

Pydantic-AI based Multi-Agent Framework with YAML-based Agents, Teams, Workflows &
Extended ACP / AGUI integration.
"""

from __future__ import annotations

from importlib.metadata import version
from upathtools import register_http_filesystems

from agentwolf.models.agents import NativeAgentConfig
from agentwolf.models.manifest import AgentsManifest

# Builtin toolsets imports removed to avoid circular dependency
# Import them directly from agentwolf_toolsets.builtin when needed
from agentwolf.agents import Agent, AgentContext, ACPAgent
from agentwolf.delegation import AgentPool, BaseTeam
from dotenv import load_dotenv
from agentwolf.messaging.messages import ChatMessage
from agentwolf.tools import Tool, ToolCallInfo
from agentwolf.messaging.messagenode import MessageNode
from agentwolf.testing import acp_test_session
from pydantic_ai import (
    AudioUrl,
    BinaryContent,
    BinaryImage,
    DocumentUrl,
    ImageUrl,
    VideoUrl,
)

__version__ = version("agentwolf")
__title__ = "AgentPool"
__author__ = "Philipp Temminghoff"
__author_email__ = "philipptemminghoff@googlemail.com"
__copyright__ = "Copyright (c) 2025 Philipp Temminghoff"
__license__ = "MIT"
__url__ = "https://github.com/phil65/agentwolf"

load_dotenv()
register_http_filesystems()

# Rebuild models with forward references that couldn't be resolved during
# module initialization due to circular import avoidance.
# PromptType and BasePrompt are imported under TYPE_CHECKING in
# agentwolf_config.knowledge and agentwolf_config.task to break a circular
# import chain. By this point, agentwolf.prompts.prompts is fully loaded,
# so we can resolve the forward references.
from agentwolf.prompts.prompts import BasePrompt, PromptType
from agentwolf_config.knowledge import Knowledge
from agentwolf_config.task import Job

_ns = {"PromptType": PromptType, "BasePrompt": BasePrompt}
Knowledge.model_rebuild(_types_namespace=_ns)
Job.model_rebuild(_types_namespace=_ns)
NativeAgentConfig.model_rebuild(_types_namespace=_ns)
AgentsManifest.model_rebuild(_types_namespace=_ns)

__all__ = [
    "ACPAgent",
    "Agent",
    "AgentContext",
    "AgentPool",
    "AgentsManifest",
    "AudioUrl",
    "BaseTeam",
    "BinaryContent",
    "BinaryImage",
    "ChatMessage",
    "DocumentUrl",
    "ImageUrl",
    "MessageNode",
    "NativeAgentConfig",
    "Tool",
    "ToolCallInfo",
    "VideoUrl",
    "__version__",
    "acp_test_session",
]
