"""Core messsaging classes for AgentPool."""

from agentwolf.messaging.chat_filesystem import ChatMessageFileSystem
from agentwolf.messaging.messages import ChatMessage, TokenCost, AgentResponse, TeamResponse
from agentwolf.messaging.message_container import ChatMessageList
from agentwolf.messaging.event_manager import EventManager
from agentwolf.messaging.messagenode import MessageNode, SourceType, get_source_type
from agentwolf.messaging.message_history import MessageHistory
from agentwolf.messaging.compaction import (
    CompactionPipeline,
    CompactionStep,
    FilterBinaryContent,
    FilterEmptyMessages,
    FilterRetryPrompts,
    FilterThinking,
    FilterToolCalls,
    KeepFirstAndLast,
    KeepFirstMessages,
    KeepLastMessages,
    Summarize,
    TokenBudget,
    TruncateTextParts,
    TruncateToolOutputs,
    WhenMessageCountExceeds,
    balanced_context,
    minimal_context,
    summarizing_context,
)

__all__ = [
    "AgentResponse",
    "ChatMessage",
    "ChatMessageFileSystem",
    "ChatMessageList",
    "CompactionPipeline",
    "CompactionStep",
    "EventManager",
    "FilterBinaryContent",
    "FilterEmptyMessages",
    "FilterRetryPrompts",
    "FilterThinking",
    "FilterToolCalls",
    "KeepFirstAndLast",
    "KeepFirstMessages",
    "KeepLastMessages",
    "MessageHistory",
    "MessageNode",
    "SourceType",
    "Summarize",
    "TeamResponse",
    "TokenBudget",
    "TokenCost",
    "TruncateTextParts",
    "TruncateToolOutputs",
    "WhenMessageCountExceeds",
    "balanced_context",
    "get_source_type",
    "minimal_context",
    "summarizing_context",
]
