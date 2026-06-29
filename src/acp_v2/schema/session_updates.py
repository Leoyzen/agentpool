"""ACP v2 session update schema definitions.

Key differences from v1:
- Whole-message upserts: user_message, agent_message, agent_thought
- Unified tool_call_update (no separate tool_call creation)
- tool_call_content_chunk for streaming tool content
- state_update for turn lifecycle (running/idle/requires_action)
- plan_update with tagged content (type discriminator + plan id)
- messageId is required on all chunks (not optional like v1)
"""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Annotated, Any, Literal

from pydantic import Field

from acp.schema.agent_plan import PlanEntry  # noqa: TC001
from acp.schema.base import AnnotatedObject
from acp.schema.content_blocks import ContentBlock  # noqa: TC001
from acp.schema.session_state import SessionConfigOption  # noqa: TC001
from acp.schema.slash_commands import AvailableCommand  # noqa: TC001
from acp.schema.tool_call import (  # noqa: TC001
    SubagentRunInfo,
    ToolCallContent,
    ToolCallKind,
    ToolCallLocation,
)
from acp_v2.schema._unset import _UNSET, UnsetType


ToolCallStatus = Literal["pending", "in_progress", "completed", "failed"]
SessionState = Literal["running", "idle", "requires_action"]
StopReason = Literal[
    "end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"
]


class BaseChunk(AnnotatedObject):
    """Base class for v2 streamed message chunks."""

    content: ContentBlock
    message_id: str = Field(alias="messageId")


class UserMessageChunk(BaseChunk):
    """A chunk of the user's message being streamed."""

    session_update: Literal["user_message_chunk"] = Field(
        default="user_message_chunk", init=False
    )


class AgentMessageChunk(BaseChunk):
    """A chunk of the agent's response being streamed."""

    session_update: Literal["agent_message_chunk"] = Field(
        default="agent_message_chunk", init=False
    )


class AgentThoughtChunk(BaseChunk):
    """A chunk of the agent's internal reasoning being streamed."""

    session_update: Literal["agent_thought_chunk"] = Field(
        default="agent_thought_chunk", init=False
    )


class WholeMessage(AnnotatedObject):
    """Base for v2 whole-message upserts keyed by messageId.

    content present = replace entire array.
    content omitted (UNSET) = leave unchanged.
    content = None or [] = clear.
    """

    message_id: str = Field(alias="messageId")
    content: Sequence[ContentBlock] | None | UnsetType = None
    """Three-state: UNSET=unchanged, None=clear, list=replace."""


class UserMessage(WholeMessage):
    """Whole user message upsert."""

    session_update: Literal["user_message"] = Field(
        default="user_message", init=False
    )


class AgentMessage(WholeMessage):
    """Whole agent message upsert."""

    session_update: Literal["agent_message"] = Field(
        default="agent_message", init=False
    )


class AgentThought(WholeMessage):
    """Whole agent thought upsert."""

    session_update: Literal["agent_thought"] = Field(
        default="agent_thought", init=False
    )


class ToolCallUpdate(AnnotatedObject):
    """Unified v2 tool call upsert (replaces v1 tool_call + tool_call_update).

    First sighting of toolCallId creates the tool call.
    Subsequent updates patch fields. Omitted = unchanged,
    null = clear, value = replace.
    """

    session_update: Literal["tool_call_update"] = Field(
        default="tool_call_update", init=False
    )

    tool_call_id: str = Field(alias="toolCallId")
    title: str | None | UnsetType = _UNSET
    kind: ToolCallKind | None | UnsetType = _UNSET
    status: ToolCallStatus | None | UnsetType = _UNSET
    content: Sequence[ToolCallContent] | None | UnsetType = _UNSET
    locations: Sequence[ToolCallLocation] | None | UnsetType = _UNSET
    raw_input: Any | None | UnsetType = _UNSET
    raw_output: Any | None | UnsetType = _UNSET
    subagent: SubagentRunInfo | None | UnsetType = _UNSET


class ToolCallContentChunk(AnnotatedObject):
    """Stream a single ToolCallContent item that appends to a tool call."""

    session_update: Literal["tool_call_content_chunk"] = Field(
        default="tool_call_content_chunk", init=False
    )

    tool_call_id: str = Field(alias="toolCallId")
    content: ToolCallContent


class StateUpdate(AnnotatedObject):
    """Notify client of session state transitions."""

    session_update: Literal["state_update"] = Field(
        default="state_update", init=False
    )

    state: SessionState
    stop_reason: StopReason | None = None


class PlanItems(AnnotatedObject):
    """Item-based plan content variant (stable in v2)."""

    type: Literal["items"] = Field(default="items", init=False)
    id: str
    entries: Sequence[PlanEntry]


PlanUpdateContent = Annotated[PlanItems, Field(discriminator="type")]


class PlanUpdate(AnnotatedObject):
    """v2 plan update with tagged content (replaces v1 plan)."""

    session_update: Literal["plan_update"] = Field(
        default="plan_update", init=False
    )

    plan: PlanUpdateContent


class AvailableCommandsUpdate(AnnotatedObject):
    """Available commands are ready or have changed."""

    session_update: Literal["available_commands_update"] = Field(
        default="available_commands_update", init=False
    )

    available_commands: Sequence[AvailableCommand]


class ConfigOptionUpdate(AnnotatedObject):
    """A session configuration option value has changed."""

    session_update: Literal["config_option_update"] = Field(
        default="config_option_update", init=False
    )

    config_id: str = Field(alias="configId")
    value_id: str = Field(alias="valueId")
    config_options: Sequence[SessionConfigOption] = Field(alias="configOptions")


class Cost(AnnotatedObject):
    """Cost information for a session."""

    amount: float
    currency: str


class Usage(AnnotatedObject):
    """Token usage information for a prompt turn."""

    total_tokens: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    thought_tokens: int | None = Field(default=None, ge=0)
    cached_read_tokens: int | None = Field(default=None, ge=0)
    cached_write_tokens: int | None = Field(default=None, ge=0)


class UsageUpdate(AnnotatedObject):
    """Context window and cost update for a session."""

    session_update: Literal["usage_update"] = Field(
        default="usage_update", init=False
    )

    used: int = Field(ge=0)
    size: int = Field(ge=0)
    cost: Cost | None = None


class SessionInfoUpdate(AnnotatedObject):
    """Incremental update to session metadata."""

    session_update: Literal["session_info_update"] = Field(
        default="session_info_update", init=False
    )

    session_id: str = Field(alias="sessionId")
    title: str | None = None
    updated_at: str | None = Field(default=None, alias="updatedAt")
    meta: dict[str, Any] | None = None


SessionUpdate = Annotated[
    (
        UserMessageChunk
        | AgentMessageChunk
        | AgentThoughtChunk
        | UserMessage
        | AgentMessage
        | AgentThought
        | ToolCallUpdate
        | ToolCallContentChunk
        | StateUpdate
        | PlanUpdate
        | AvailableCommandsUpdate
        | ConfigOptionUpdate
        | UsageUpdate
        | SessionInfoUpdate
    ),
    Field(discriminator="session_update"),
]
